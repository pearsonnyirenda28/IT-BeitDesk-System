"""
Quick request links.

A user can generate a personal link (with an expiry date they choose) that lets them
raise a request without a full login. The link only *identifies* the account — every
submission still has to prove it is the owner by entering the account password and,
when MFA is enabled, an authenticator or backup code. No session is ever created.

Security properties
  * only a SHA-256 hash of the token is stored; the URL is shown to the owner once
  * unknown / expired / revoked links all show the same page (nothing to enumerate)
  * 5 wrong attempts lock the link for 15 minutes; 3 lockouts disable it and tell the owner
  * each authenticator code can be used once; backup codes are consumed
  * face-recognition MFA happens in the browser and cannot be trusted from a link, so
    accounts with face-only MFA must add an authenticator app to use quick links
  * the page is never cached and sends no Referer, so the token does not leak
"""
import base64
import io
import json
import re
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core import signing
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache

from .models import AuditLog, Category, QuickRequestLink, Ticket, TicketTemplate
from .views import _resolve_org, _template_payload, get_client_ip, log_action

EXPIRY_MIN_MINUTES = 10
EXPIRY_MAX_DAYS = getattr(settings, 'QUICK_LINK_MAX_DAYS', 365)
MAX_ACTIVE_LINKS = 10
IP_LIMIT, IP_WINDOW = 30, 600            # attempts per IP per 10 minutes (secondary guard)
DONE_SALT = 'quick-link-done'


# ── Helpers ───────────────────────────────────────────────────────────────────

def mfa_requirement(user):
    """'none' | 'totp' (authenticator or backup code) | 'blocked' (face-only — not usable here)."""
    prof = getattr(user, 'profile', None)
    if not prof or not prof.mfa_enabled:
        return 'none'
    if prof.totp_enabled and prof.totp_secret:
        return 'totp'
    return 'blocked'


def parse_expiry(raw, now=None):
    """Parse a <input type=datetime-local> value. Returns (aware datetime | None, error | None)."""
    now = now or timezone.now()
    raw = (raw or '').strip()
    if not raw:
        return None, 'Choose when this link should expire.'
    dt = None
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            dt = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        return None, 'Enter a valid date and time.'
    dt = timezone.make_aware(dt, timezone.get_current_timezone())
    if dt < now + timedelta(minutes=EXPIRY_MIN_MINUTES):
        return None, f'The expiry must be at least {EXPIRY_MIN_MINUTES} minutes from now.'
    if dt > now + timedelta(days=EXPIRY_MAX_DAYS):
        return None, f'A link can last at most {EXPIRY_MAX_DAYS} days.'
    return dt, None


def _audit(owner, action, link, request, notes, changes=None):
    """Audit entry attributed to the link owner (the public page has no logged-in user)."""
    AuditLog.objects.create(
        user=owner, action=action, model_name='QuickRequestLink', object_id=str(link.pk),
        object_repr=f'{link.label} ({link.token_hint}…)', changes=changes,
        ip_address=get_client_ip(request), user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
        notes=notes)


def _mfa_log(user, method, result, request, note=''):
    try:
        from accounts.models import MFALog
        MFALog.objects.create(user=user, method=method, result=result, ip_address=get_client_ip(request),
                              user_agent=request.META.get('HTTP_USER_AGENT', '')[:300], note=note[:200])
    except Exception:
        pass


def _harden(resp):
    """The token is in the URL: never cache the page, never send it as a Referer, keep it out of search."""
    resp['Referrer-Policy'] = 'no-referrer'
    resp['X-Robots-Tag'] = 'noindex, nofollow'
    resp['Cache-Control'] = 'no-store'
    return resp


def _qr_data_uri(text):
    try:
        import qrcode
        buf = io.BytesIO()
        qrcode.make(text, box_size=6, border=2).save(buf, 'PNG')
        return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ''


def _absolute(request, token):
    base = getattr(settings, 'QUICK_LINK_BASE_URL', '').rstrip('/')
    path = reverse('quick_request', args=[token])
    return base + path if base else request.build_absolute_uri(path)


# ── Credential check ──────────────────────────────────────────────────────────

def _check_totp(link, code):
    """Verify an authenticator code once only. Returns True/False and records the used step."""
    import pyotp
    import time
    prof = link.user.profile
    totp = pyotp.TOTP(prof.totp_secret)
    now = time.time()
    for offset in (-1, 0, 1):
        when = now + offset * totp.interval
        if totp.at(when) == code:
            step = int(when // totp.interval)
            if step <= link.last_totp_step:
                return False                           # this code was already used
            link.last_totp_step = step
            return True
    return False


def _check_backup(user, code):
    prof = user.profile
    try:
        codes = json.loads(prof.totp_backup_codes or '[]')
    except ValueError:
        return False
    if code in codes:
        codes.remove(code)
        prof.totp_backup_codes = json.dumps(codes)
        prof.save(update_fields=['totp_backup_codes'])
        return True
    return False


def verify_owner(request, link, password, code):
    """
    Prove the person at the keyboard is the account owner.
    Returns 'ok' | 'bad' | 'locked' | 'disabled' | 'throttled'.
    """
    ip = get_client_ip(request) or 'unknown'
    ip_key = f'ql_ip_{ip}'
    if cache.get(ip_key, 0) >= IP_LIMIT:
        return 'throttled'
    link.refresh_from_db()
    if not link.is_active:
        return 'disabled'
    if link.is_locked:
        return 'locked'

    cache.set(ip_key, cache.get(ip_key, 0) + 1, IP_WINDOW)
    user = link.user
    need = mfa_requirement(user)

    ok = bool(password) and authenticate(request, username=user.get_username(), password=password) is not None
    method = None
    if ok and need == 'blocked':
        ok = False
    elif ok and need == 'totp':
        code = re.sub(r'\s+', '', code or '')
        if re.fullmatch(r'\d{6}', code):
            method, ok = 'TOTP', _check_totp(link, code)
        elif code:
            method, ok = 'BACKUP', _check_backup(user, code.upper())
        else:
            ok = False
        _mfa_log(user, method or 'TOTP', 'PASS' if ok else 'FAIL', request, f'Quick link {link.token_hint}…')

    if ok:
        link.failed_attempts = 0
        link.save(update_fields=['failed_attempts', 'last_totp_step'])
        return 'ok'

    link.failed_attempts += 1
    result = 'bad'
    if link.failed_attempts >= QuickRequestLink.MAX_FAILED:
        link.failed_attempts = 0
        link.lock_count += 1
        link.locked_until = timezone.now() + timedelta(minutes=QuickRequestLink.LOCK_MINUTES)
        result = 'locked'
        _audit(user, AuditLog.ACTION_UPDATE, link, request,
               f'Quick link locked for {QuickRequestLink.LOCK_MINUTES} minutes after repeated wrong credentials '
               f'(lockout {link.lock_count} of {QuickRequestLink.MAX_LOCKS})')
        if link.lock_count >= QuickRequestLink.MAX_LOCKS:
            link.revoked_at = timezone.now()
            link.revoked_note = 'Disabled automatically after repeated failed attempts'
            result = 'disabled'
            _audit(user, AuditLog.ACTION_UPDATE, link, request, 'Quick link disabled after repeated failed attempts')
            try:
                from helpdesk.notifications import notify
                notify(user, title='Quick link disabled',
                       body=f'Your quick link "{link.label}" was disabled after repeated wrong passwords or codes. '
                            f'If that was not you, change your password.',
                       url='/dashboard/quick-links/', tag=f'quicklink-{link.pk}', level='danger',
                       sender='Security', kind='link')
            except Exception:
                pass
    link.save()
    return result


# ── Owner pages ───────────────────────────────────────────────────────────────

@login_required
def quick_links(request):
    """Create and manage your own quick links."""
    user = request.user
    errors, values = {}, {'label': '', 'expires_at': ''}
    need = mfa_requirement(user)

    if request.method == 'POST':
        values = {'label': ' '.join(request.POST.get('label', '').split()),
                  'expires_at': request.POST.get('expires_at', '').strip()}
        if not values['label']:
            errors['label'] = 'Give the link a name so you can recognise it later.'
        elif len(values['label']) > 100:
            errors['label'] = 'Too long — maximum 100 characters.'
        expires, err = parse_expiry(values['expires_at'])
        if err:
            errors['expires_at'] = err
        if need == 'blocked':
            errors['general'] = ('Your account uses face recognition only. Quick links need an authenticator app — '
                                 'add one in MFA settings first.')
        active = [l for l in QuickRequestLink.objects.filter(user=user) if l.is_active]
        if len(active) >= MAX_ACTIVE_LINKS:
            errors['general'] = f'You already have {MAX_ACTIVE_LINKS} active links. Revoke one before creating another.'
        if not errors:
            link, raw = QuickRequestLink.issue(user, values['label'], expires, ip=get_client_ip(request))
            log_action(request, AuditLog.ACTION_CREATE, 'QuickRequestLink', link.pk,
                       f'{link.label} ({link.token_hint}…)', notes=f'Quick link created, expires {expires:%Y-%m-%d %H:%M}')
            request.session['quick_link_new'] = {'url': _absolute(request, raw), 'id': link.pk}
            return redirect('quick_links')
        messages.error(request, 'Link not created — please correct the highlighted field(s).')

    fresh = request.session.pop('quick_link_new', None)       # shown exactly once
    if fresh:
        fresh['qr'] = _qr_data_uri(fresh['url'])
        fresh['link'] = QuickRequestLink.objects.filter(pk=fresh['id'], user=user).first()

    links = list(QuickRequestLink.objects.filter(user=user))
    links.sort(key=lambda l: (l.status != 'active', -l.created_at.timestamp()))
    default_expiry = (timezone.localtime() + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M')
    return render(request, 'helpdesk/quick_links.html', {
        'links': links, 'fresh': fresh, 'errors': errors, 'v': values, 'mfa_need': need,
        'default_expiry': values['expires_at'] or default_expiry,
        'max_days': EXPIRY_MAX_DAYS, 'max_active': MAX_ACTIVE_LINKS,
        'is_staff': user.is_staff,
        'minimum': (timezone.localtime() + timedelta(minutes=EXPIRY_MIN_MINUTES)).strftime('%Y-%m-%dT%H:%M'),
        'maximum': (timezone.localtime() + timedelta(days=EXPIRY_MAX_DAYS)).strftime('%Y-%m-%dT%H:%M'),
    })


@login_required
def quick_link_revoke(request, pk):
    if request.method != 'POST':
        return redirect('quick_links')
    link = get_object_or_404(QuickRequestLink, pk=pk)
    if link.user_id != request.user.pk and not request.user.is_superuser:
        messages.error(request, 'You can only revoke your own links.')
        return redirect('quick_links')
    link.revoke('Revoked by owner' if link.user_id == request.user.pk else f'Revoked by {request.user.username}')
    log_action(request, AuditLog.ACTION_DELETE, 'QuickRequestLink', link.pk,
               f'{link.label} ({link.token_hint}…)', notes='Quick link revoked')
    messages.success(request, f'"{link.label}" can no longer be used.')
    return redirect('quick_links_admin' if request.POST.get('from') == 'admin' and request.user.is_superuser
                    else 'quick_links')


@login_required
def quick_links_admin(request):
    """Administrators can see every quick link (never the token) and revoke any of them."""
    if not request.user.is_superuser:
        messages.error(request, 'Administrator access required.')
        return redirect('dashboard')
    status = request.GET.get('status', 'all')
    q = request.GET.get('q', '').strip()
    links = QuickRequestLink.objects.select_related('user')
    if q:
        links = links.filter(user__username__icontains=q) | links.filter(label__icontains=q)
    links = sorted(links, key=lambda l: -l.created_at.timestamp())
    counts = {'active': 0, 'expired': 0, 'revoked': 0}
    for l in links:
        counts[l.status] += 1
    if status in counts:
        links = [l for l in links if l.status == status]
    page = Paginator(links, 20).get_page(request.GET.get('page', 1))
    return render(request, 'helpdesk/quick_links_admin.html', {
        'links': page, 'status': status, 'q': q, 'counts': counts, 'total': sum(counts.values()),
    })


# ── Public page ───────────────────────────────────────────────────────────────

def _invalid(request, code=404):
    return _harden(render(request, 'helpdesk/quick_request_invalid.html', status=code))


def _page_context(link, values, errors, need):
    templates = list(TicketTemplate.objects.filter(is_active=True).select_related('category')
                     .order_by('-use_count', 'name')[:60])
    return {'link': link, 'owner': link.user,
            'owner_name': link.user.get_full_name() or link.user.username,
            'templates': templates, 'tpl_json': _template_payload(templates),
            'categories': Category.objects.all(), 'v': values, 'errors': errors, 'mfa_need': need}


@never_cache
def quick_request(request, token):
    """Raise a request as the link's owner, after confirming their password (and MFA code)."""
    link = QuickRequestLink.find(token)
    if not link or not link.is_active:
        return _invalid(request)
    owner = link.user
    need = mfa_requirement(owner)
    values = {k: '' for k in ('title', 'description', 'category', 'priority', 'template_id')}
    values['priority'] = 'MEDIUM'
    errors = {}

    if request.method == 'POST':
        values = {k: request.POST.get(k, '').strip() for k in values}
        values['priority'] = (values['priority'] or 'MEDIUM').upper()
        category = None
        if not values['title']:
            errors['title'] = 'Please enter a title for your request.'
        elif len(values['title']) > 255:
            errors['title'] = 'The title is too long — keep it under 255 characters.'
        if values['category']:
            category = Category.objects.filter(pk=values['category']).first() if values['category'].isdigit() else None
            if not category:
                errors['category'] = 'Please choose a category from the list.'
        if values['priority'] not in ('LOW', 'MEDIUM', 'HIGH'):
            errors['priority'] = 'Choose an urgency level.'
        if not request.POST.get('password'):
            errors['password'] = 'Enter your account password to confirm it is you.'
        if need == 'totp' and not request.POST.get('mfa_code', '').strip():
            errors['mfa_code'] = 'Enter the code from your authenticator app (or a backup code).'
        if need == 'blocked':
            errors['general'] = ('This account uses face recognition only, which cannot be used from a link. '
                                 'Sign in to BeitDesk normally, or add an authenticator app in MFA settings.')

        if not errors:                       # only spend an attempt on a complete, valid form
            result = verify_owner(request, link, request.POST.get('password', ''), request.POST.get('mfa_code', ''))
            if result == 'ok':
                return _create_from_link(request, link, values, category, token)
            errors['general'] = {
                'bad': 'That password or code is not correct.',
                'locked': f'Too many wrong attempts. This link is locked for {QuickRequestLink.LOCK_MINUTES} minutes.',
                'throttled': 'Too many attempts from your network. Please wait a few minutes and try again.',
                'disabled': None,
            }[result]
            if result == 'disabled':
                return _invalid(request)

        messages.error(request, 'Request not submitted — please correct the highlighted field(s). Everything else has been kept.')
        return _harden(render(request, 'helpdesk/quick_request.html', _page_context(link, values, errors, need)))
    return _harden(render(request, 'helpdesk/quick_request.html', _page_context(link, values, errors, need)))


def _create_from_link(request, link, values, category, token):
    owner = link.user
    org = _resolve_org(owner, {})
    ticket = None
    for _attempt in range(3):
        try:
            with transaction.atomic(using=Ticket.objects.db):
                ticket = Ticket.objects.create(
                    title=values['title'], description=values['description'] or '(No further details given)',
                    priority=values['priority'], category=category, requester=owner,
                    requester_name=owner.get_full_name() or owner.username, requester_email=owner.email,
                    requester_office=org['office'], requester_department=org['department'],
                    requester_section=org['section'], status='OPEN', source='LINK')
            break
        except IntegrityError:
            continue
    if ticket is None:
        messages.error(request, 'Could not generate a ticket number — nothing was lost, please press Submit again.')
        return _harden(render(request, 'helpdesk/quick_request.html',
                              _page_context(link, values, {}, mfa_requirement(owner))))

    if values['template_id'].isdigit():
        TicketTemplate.objects.filter(pk=values['template_id']).update(use_count=F('use_count') + 1)
    QuickRequestLink.objects.filter(pk=link.pk).update(use_count=F('use_count') + 1, last_used_at=timezone.now())
    AuditLog.objects.create(
        user=owner, action=AuditLog.ACTION_CREATE, model_name='Ticket', object_id=ticket.ticket_id,
        object_repr=ticket.title, ip_address=get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
        notes=f'Created via quick link "{link.label}" ({link.token_hint}…)')
    try:
        from helpdesk.notifications import notify, notify_ticket_created
        notify_ticket_created(ticket, None)
        notify(owner, title=f'Quick link used — {ticket.ticket_id}',
               body=f'Request submitted with your quick link "{link.label}": {ticket.title}',
               url=f'/dashboard/portal/ticket/{ticket.ticket_id}/', tag=f'quicklink-{link.pk}',
               level='info', sender='Quick Link', ticket=ticket, kind='link')
    except Exception:
        pass
    ref = signing.dumps({'t': ticket.ticket_id, 'l': link.pk}, salt=DONE_SALT)
    return _harden(redirect(reverse('quick_request_done', args=[token]) + f'?r={ref}'))


@never_cache
def quick_request_done(request, token):
    link = QuickRequestLink.find(token)
    try:
        data = signing.loads(request.GET.get('r', ''), salt=DONE_SALT, max_age=900)
    except signing.BadSignature:
        return _invalid(request)
    if not link or data.get('l') != link.pk:
        return _invalid(request)
    ticket = Ticket.objects.filter(ticket_id=data.get('t')).first()
    if not ticket:
        return _invalid(request)
    return _harden(render(request, 'helpdesk/quick_request_done.html', {
        'ticket': ticket, 'link': link, 'token': token, 'owner_name': link.user.get_full_name() or link.user.username,
        'track_url': f'/dashboard/portal/ticket/{ticket.ticket_id}/', 'still_active': link.is_active}))
