import json
import re
from django.urls import reverse
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.db.models import Count, Q, Avg
from django.utils import timezone
from django.http import JsonResponse
from django.core.paginator import Paginator
from datetime import datetime, timedelta
from .models import (
    Ticket, TicketComment, Tag, Category, AuditLog, GlossaryTerm, DatabaseYear,
    LearnedPhrase, KBArticle, Asset, TicketTemplate, RecurringTicket,
    DatabaseBackup, PendingNotification, TicketApproval,
)
from . import insights
from .forms import TicketCommentForm, TicketFilterForm

# Tickets shown per page on the dashboard, ticket list and requester portal
TICKETS_PER_PAGE = 10


def log_action(request, action, model_name, object_id='', object_repr='', changes=None, notes=''):
    AuditLog.objects.create(
        user=request.user if request.user.is_authenticated else None,
        action=action,
        model_name=model_name,
        object_id=str(object_id),
        object_repr=object_repr,
        changes=changes,
        ip_address=get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
        notes=notes,
    )


def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def get_voip_stats(today_start):
    """Return VoIP stats safely — returns zeros if VoIP app is unavailable."""
    try:
        from voip.models import VoIPCall
        active_calls = VoIPCall.objects.filter(status__in=['RINGING', 'ACTIVE', 'ON_HOLD'])
        return {
            'active_calls': active_calls.count(),
            'calls_today': VoIPCall.objects.filter(initiated_at__gte=today_start).count(),
            'missed_today': VoIPCall.objects.filter(initiated_at__gte=today_start, status='MISSED').count(),
            'avg_talk_today': VoIPCall.objects.filter(
                initiated_at__gte=today_start, status='COMPLETED'
            ).aggregate(avg=Avg('talk_duration_seconds'))['avg'] or 0,
        }, list(active_calls.select_related('answered_by', 'destination_extension')[:5])
    except Exception:
        return {
            'active_calls': 0, 'calls_today': 0,
            'missed_today': 0, 'avg_talk_today': 0,
        }, []


@login_required
def dashboard(request):
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start  = today_start - timedelta(days=7)

    active_year    = request.session.get('active_db_year')
    active_db_name = request.session.get('active_db_name', '')
    viewing_label  = f'{active_year} Records' if active_year else 'Current Year'
    is_past_year   = bool(active_year)

    try:
        tickets = Ticket.objects.all()

        # ── Single aggregate query replaces 8 separate COUNTs ─────────────────
        from django.db.models import Count, Avg, Q
        agg = tickets.aggregate(
            total_tickets   = Count('id'),
            total_open      = Count('id', filter=Q(status__in=['OPEN','IN_PROGRESS','REOPENED'])),
            total_pending   = Count('id', filter=Q(status='PENDING')),
            total_closed    = Count('id', filter=Q(status='CLOSED')),
            critical_open   = Count('id', filter=Q(status__in=['OPEN','IN_PROGRESS'], priority='CRITICAL')),
            avg_resolution  = Avg('resolution_time_minutes', filter=Q(resolution_time_minutes__isnull=False)),
            resolved_today  = Count('id', filter=Q(status='RESOLVED', resolved_at__gte=today_start)) if not is_past_year else Count('id', filter=Q(pk=None)),
            resolved_week   = Count('id', filter=Q(resolved_at__gte=week_start)) if not is_past_year else Count('id', filter=Q(pk=None)),
            new_week        = Count('id', filter=Q(created_at__gte=week_start)) if not is_past_year else Count('id', filter=Q(pk=None)),
            overdue_count   = Count('id', filter=Q(
                status__in=['OPEN','IN_PROGRESS','PENDING'],
                due_date__isnull=False,
                due_date__lt=now,
            )) if not is_past_year else Count('id', filter=Q(pk=None)),
        )

        stats = {
            'total_open':             agg['total_open']      or 0,
            'total_pending':          agg['total_pending']   or 0,
            'total_resolved_today':   agg['resolved_today']  or 0,
            'total_closed':           agg['total_closed']    or 0,
            'critical_open':          agg['critical_open']   or 0,
            'overdue_count':          agg['overdue_count']   or 0,
            'avg_resolution_minutes': agg['avg_resolution']  or 0,
            'resolved_this_week':     agg['resolved_week']   or 0,
            'new_this_week':          agg['new_week']        or 0,
            'total_tickets':          agg['total_tickets']   or 0,
        }

        # ── All with select_related to avoid N+1 ──────────────────────────────
        recent_qs = tickets.select_related(
            'requester', 'assigned_to', 'category'
        ).prefetch_related('tags').order_by('-created_at')
        # 10 tickets per page; page number comes from ?tpage= so it never
        # clashes with other paginated blocks on the same screen.
        recent_tickets = Paginator(recent_qs, TICKETS_PER_PAGE).get_page(
            request.GET.get('tpage', 1))

        # ── Batch chart queries ────────────────────────────────────────────────
        status_data   = list(tickets.values('status').annotate(count=Count('id')).order_by('status'))
        category_data = list(tickets.filter(category__isnull=False)
                             .values('category__name').annotate(count=Count('id')).order_by('-count')[:6])
        priority_data = list(tickets.values('priority').annotate(count=Count('id')).order_by('priority'))
        agent_workload = list(
            tickets.filter(status__in=['OPEN','IN_PROGRESS'], assigned_to__isnull=False)
            .values('assigned_to__username','assigned_to__first_name','assigned_to__last_name')
            .annotate(count=Count('id')).order_by('-count')[:5]
        )

    except Exception:
        stats = {k: 0 for k in ['total_open','total_pending','total_resolved_today',
                                 'total_closed','critical_open','overdue_count',
                                 'avg_resolution_minutes','resolved_this_week',
                                 'new_this_week','total_tickets']}
        recent_tickets = []; status_data = []; category_data = []
        priority_data  = []; agent_workload = []

    if is_past_year:
        voip_stats = {'active_calls':0,'calls_today':0,'missed_today':0,'avg_talk_today':0}
        active_calls = []
    else:
        voip_stats, active_calls = get_voip_stats(today_start)

    try:
        if request.user.is_staff:
            recent_audit = AuditLog.objects.select_related('user').order_by('-timestamp')[:10]
        else:
            recent_audit = AuditLog.objects.select_related('user').filter(
                user=request.user).order_by('-timestamp')[:10]
    except Exception:
        recent_audit = []

    context = {
        'stats': stats, 'voip_stats': voip_stats, 'active_calls': active_calls,
        'recent_tickets': recent_tickets, 'status_data': status_data,
        'category_data': category_data, 'priority_data': priority_data,
        'agent_workload': agent_workload, 'recent_audit': recent_audit,
        'active_year': active_year, 'active_db_name': active_db_name,
        'viewing_label': viewing_label, 'is_past_year': is_past_year,
    }
    return render(request, 'helpdesk/dashboard.html', context)


_ORG_TICKET_COLUMNS = {'office': 'requester_office', 'department': 'requester_department',
                       'section': 'requester_section'}


def _parse_window(raw):
    """?since= / ?until= (ISO date or date-time) -> aware datetime, or None if blank / unreadable."""
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return timezone.make_aware(dt, timezone.get_current_timezone()) if timezone.is_naive(dt) else dt


def _qs_without(request, *drop, extra=None):
    """Current querystring minus some keys (plus optional extras), ready to append to a URL."""
    q = request.GET.copy()
    for k in drop:
        q.pop(k, None)
    for k, val in (extra or {}).items():
        q[k] = val
    return '?' + q.urlencode() if q else '?'


def _filter_chips(request, org_sel, requester_sel, since, until):
    """The organisation / requester / time-window filters currently applied, each with a link that removes it."""
    chips = []
    for f, val in org_sel.items():
        if val:
            chips.append({'label': f"{f.title()}: {'(not set)' if val == insights.NONE_TOKEN else val}",
                          'remove': _qs_without(request, f, 'page')})
    if requester_sel:
        u = User.objects.filter(pk=requester_sel).first()
        chips.append({'label': f"Requester: {(u.get_full_name() or u.username) if u else requester_sel}",
                      'remove': _qs_without(request, 'requester', 'page')})
    if since:
        chips.append({'label': f'Created since {timezone.localtime(since):%d %b %Y}', 'remove': _qs_without(request, 'since', 'page')})
    if until and until < timezone.now() - timedelta(hours=12):      # "until now" is just the report's own end
        chips.append({'label': f'Created until {timezone.localtime(until):%d %b %Y}', 'remove': _qs_without(request, 'until', 'page')})
    return chips


@login_required
def ticket_list(request):
    active_year    = request.session.get('active_db_year')
    active_db_name = request.session.get('active_db_name', '')
    is_past_year   = bool(active_year)
    db_error       = False

    try:
        # Staff and admins see all tickets; regular users see only theirs
        # prefetch_related('tags') prevents N+1 when rendering tag chips on each row
        if request.user.is_staff:
            tickets = Ticket.objects.select_related(
                'requester', 'assigned_to', 'category'
            ).prefetch_related('tags').all()
        else:
            tickets = Ticket.objects.select_related(
                'requester', 'assigned_to', 'category'
            ).prefetch_related('tags').filter(
                Q(requester=request.user) | Q(assigned_to=request.user)
            )

        status   = request.GET.get('status', '')
        priority = request.GET.get('priority', '')
        category = request.GET.get('category', '')
        assigned = request.GET.get('assigned', '')
        search   = request.GET.get('search', '')
        source   = request.GET.get('source', '')

        # ── Date filters ──────────────────────────────────────────────────────
        date_from   = request.GET.get('date_from', '')
        date_to     = request.GET.get('date_to', '')
        date_preset = request.GET.get('date_preset', '')
        now = timezone.now()

        if date_preset == 'today':
            date_from = now.date().isoformat()
            date_to   = now.date().isoformat()
        elif date_preset == 'week':
            date_from = (now - timedelta(days=7)).date().isoformat()
            date_to   = now.date().isoformat()
        elif date_preset == 'month':
            date_from = (now - timedelta(days=30)).date().isoformat()
            date_to   = now.date().isoformat()

        if date_from:
            try:
                tickets = tickets.filter(created_at__date__gte=date_from)
            except Exception:
                pass
        if date_to:
            try:
                tickets = tickets.filter(created_at__date__lte=date_to)
            except Exception:
                pass

        if status:
            tickets = tickets.filter(status=status)
        if priority:
            tickets = tickets.filter(priority=priority)
        if category:
            tickets = tickets.filter(category_id=category)
        if assigned == 'me':
            tickets = tickets.filter(assigned_to=request.user)
        elif assigned == 'unassigned':
            tickets = tickets.filter(assigned_to__isnull=True)
        if search:
            tickets = tickets.filter(
                Q(title__icontains=search) |
                Q(ticket_id__icontains=search) |
                Q(description__icontains=search) |
                Q(requester_name__icontains=search) |
                Q(requester_email__icontains=search)
            )
        if source:
            tickets = tickets.filter(source=source)

        # Organisation, requester and an exact time window — the filter bar, and where the
        # Request Demand report's rows lead.
        org_sel = {}
        for f, col in _ORG_TICKET_COLUMNS.items():
            org_sel[f] = request.GET.get(f, '').strip()
            if org_sel[f]:
                tickets = insights.filter_by_group(tickets, col, org_sel[f])
        requester_sel = request.GET.get('requester', '').strip()
        if requester_sel.isdigit():
            tickets = tickets.filter(requester_id=int(requester_sel))
        else:
            requester_sel = ''
        since_sel = _parse_window(request.GET.get('since', ''))
        until_sel = _parse_window(request.GET.get('until', ''))
        if since_sel:
            tickets = tickets.filter(created_at__gte=since_sel)
        if until_sel:
            tickets = tickets.filter(created_at__lte=until_sel)

        # Tag filter
        tag_filter = request.GET.get('tag', '')
        if tag_filter:
            try:
                tickets = tickets.filter(tags__name=tag_filter)
            except Exception:
                pass

        total_count  = tickets.count()
        paginator    = Paginator(tickets, TICKETS_PER_PAGE)
        tickets_page = paginator.get_page(request.GET.get('page', 1))
        categories   = Category.objects.all()
        try:
            from django.db.models import Count as _Count
            all_tags = Tag.objects.annotate(
                tc=_Count('ticket', distinct=True)
            ).order_by('-tc','name')
        except Exception:
            all_tags = []
        org_lists = {}
        if request.user.is_staff:
            for f, col in _ORG_TICKET_COLUMNS.items():
                spell = {}
                for val, n in Ticket.objects.exclude(**{col: ''}).values_list(col).annotate(n=Count('id')):
                    clean = ' '.join(val.split())
                    if clean:
                        d = spell.setdefault(clean.lower(), {})
                        d[clean] = d.get(clean, 0) + n
                org_lists[f] = sorted((insights.best_spelling(d) for d in spell.values()), key=str.lower)
        chips = _filter_chips(request, org_sel, requester_sel, since_sel, until_sel)
        preset_urls = {k: _qs_without(request, 'date_from', 'date_to', 'page', 'date_preset', extra={'date_preset': k})
                       for k in ('today', 'week', 'month')}

    except Exception:
        db_error     = True
        tickets_page = []
        categories   = []
        total_count  = 0
        all_tags     = []
        status = priority = category = assigned = search = source = tag_filter = ''
        date_from = date_to = date_preset = ''
        org_sel, requester_sel, since_sel, until_sel = {}, '', None, None
        org_lists, chips, preset_urls = {}, [], {}

    context = {
        'tickets':      tickets_page,
        'categories':   categories,
        'all_tags':     all_tags,
        'filters': {
            'status': status, 'priority': priority,
            'category': category, 'assigned': assigned,
            'search': search, 'source': source,
            'date_from': date_from, 'date_to': date_to,
            'date_preset': date_preset,
            'tag': tag_filter,
            'office': org_sel.get('office', ''), 'department': org_sel.get('department', ''),
            'section': org_sel.get('section', ''), 'requester': requester_sel,
            'since': since_sel.isoformat() if since_sel else '',
            'until': until_sel.isoformat() if until_sel else '',
        },
        'org_lists':        org_lists,
        'org_fields':       [('office', 'office'), ('department', 'department'), ('section', 'section')],
        'none_token':       insights.NONE_TOKEN,
        'chips':            chips,
        'preset_urls':      preset_urls,
        'status_choices':   Ticket.STATUS_CHOICES,
        'priority_choices': Ticket.PRIORITY_CHOICES,
        'source_choices':   Ticket.SOURCE_CHOICES,
        'total_count':      total_count,
        'active_year':      active_year,
        'active_db_name':   active_db_name,
        'is_past_year':     is_past_year,
        'db_error':         db_error,
    }
    return render(request, 'helpdesk/ticket_list.html', context)


@login_required
def ticket_detail(request, ticket_id):
    ticket = get_object_or_404(Ticket, ticket_id=ticket_id)

    # Regular users may only view tickets they raised or are assigned to
    if not request.user.is_staff:
        if ticket.requester != request.user and ticket.assigned_to != request.user:
            messages.error(request, 'You do not have permission to view this ticket.')
            return redirect('ticket_list')

    comment_form = TicketCommentForm()
    esign_values, esign_errors = {}, {}

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'comment':
            comment_form = TicketCommentForm(request.POST)
            if comment_form.is_valid():
                comment = comment_form.save(commit=False)
                comment.ticket = ticket
                comment.author = request.user
                comment.save()
                log_action(request, AuditLog.ACTION_COMMENT, 'Ticket',
                           ticket.ticket_id, str(ticket),
                           notes=f'Comment: {comment.body[:100]}')
                try:
                    from helpdesk.notifications import notify_new_comment
                    notify_new_comment(ticket, comment)
                except Exception: pass
                messages.success(request, 'Comment added.')
                return redirect('ticket_detail', ticket_id=ticket_id)

        elif action == 'status_change':
            if not request.user.is_staff:
                messages.error(request, 'Only IT staff and administrators can change ticket status.')
                return redirect('ticket_detail', ticket_id=ticket_id)

            old_status = ticket.status
            new_status = request.POST.get('status')
            if new_status and new_status != old_status:
                ticket.status = new_status
                ticket.save()
                log_action(request, AuditLog.ACTION_STATUS_CHANGE, 'Ticket',
                           ticket.ticket_id, str(ticket),
                           changes={'status': {'from': old_status, 'to': new_status}})
                try:
                    from helpdesk.notifications import notify_ticket_status
                    notify_ticket_status(ticket, old_status, request.user)
                except Exception: pass
                messages.success(request, f'Status changed to {ticket.get_status_display()}.')
                return redirect('ticket_detail', ticket_id=ticket_id)

        elif action == 'assign':
            if not request.user.is_superuser:
                messages.error(request, 'Only administrators can assign tickets.')
                return redirect('ticket_detail', ticket_id=ticket_id)

            agent_id = request.POST.get('agent_id')
            old_agent = str(ticket.assigned_to) if ticket.assigned_to else 'Unassigned'
            ticket.assigned_to = User.objects.get(pk=agent_id) if agent_id else None
            ticket.save()
            log_action(request, AuditLog.ACTION_ASSIGN, 'Ticket',
                       ticket.ticket_id, str(ticket),
                       changes={'assigned_to': {'from': old_agent,
                                                'to': str(ticket.assigned_to or 'Unassigned')}})
            if ticket.assigned_to:
                try:
                    from helpdesk.notifications import notify_ticket_assigned
                    notify_ticket_assigned(ticket, assigned_by=request.user)
                except Exception: pass
            messages.success(request, 'Ticket assignment updated.')
            return redirect('ticket_detail', ticket_id=ticket_id)

        elif action == 'take_ticket':
            # Staff and admins may self-assign an unassigned, open ticket.
            # This is separate from full reassignment, which stays admin-only.
            if not request.user.is_staff:
                messages.error(request, 'Only IT staff and administrators can take tickets.')
                return redirect('ticket_detail', ticket_id=ticket_id)

            if ticket.assigned_to is not None:
                messages.error(request, 'This ticket is already assigned and cannot be taken.')
                return redirect('ticket_detail', ticket_id=ticket_id)

            ticket.assigned_to = request.user
            if ticket.status == Ticket.STATUS_OPEN:
                ticket.status = Ticket.STATUS_IN_PROGRESS
            ticket.save()
            log_action(request, AuditLog.ACTION_ASSIGN, 'Ticket',
                       ticket.ticket_id, str(ticket),
                       changes={'assigned_to': {'from': 'Unassigned',
                                                'to': str(request.user)}},
                       notes='Self-assigned via Take Ticket')
            messages.success(request, f'You have taken ticket {ticket.ticket_id}.')
            return redirect('ticket_detail', ticket_id=ticket_id)

        elif action == 'esign':
            esign_values = {
                'decision':     request.POST.get('decision', ''),
                'reason':       request.POST.get('reason', '').strip(),
                'signer_title': request.POST.get('signer_title', '').strip(),
                # Keep the drawn signature so it is redrawn after an error and
                # the person only fixes the field that needs attention.
                'signature':    request.POST.get('signature', ''),
            }
            esign_errors = {}

            if not request.user.is_staff:
                messages.error(request, 'Only IT staff and administrators can sign off tickets.')
                return redirect('ticket_detail', ticket_id=ticket_id)

            decision = esign_values['decision']
            if decision not in (TicketApproval.DECISION_APPROVED, TicketApproval.DECISION_REJECTED):
                esign_errors['decision'] = 'Choose Approve or Reject.'
            if decision == TicketApproval.DECISION_REJECTED and len(esign_values['reason']) < 5:
                esign_errors['reason'] = 'Please give a reason for rejecting this ticket.'

            from .esign import clean_signature
            signature, sig_error = clean_signature(esign_values['signature'])
            if sig_error:
                esign_errors['signature'] = sig_error

            # Re-confirm identity — the password is never echoed back to the page.
            if not request.user.check_password(request.POST.get('password', '')):
                esign_errors['password'] = 'Password is incorrect.'

            if not esign_errors:
                approval = TicketApproval.objects.create(
                    ticket=ticket,
                    decision=decision,
                    reason=esign_values['reason'],
                    signer=request.user,
                    signer_name=request.user.get_full_name() or request.user.username,
                    signer_title=esign_values['signer_title'][:100],
                    signature_image=signature,
                    ip_address=get_client_ip(request),
                )
                log_action(request, AuditLog.ACTION_UPDATE, 'Ticket',
                           ticket.ticket_id, str(ticket),
                           changes={'e_signature': {'decision': approval.decision,
                                                    'hash': approval.document_hash[:16]}},
                           notes=f'E-signature: {approval.get_decision_display()}'
                                 + (f' — {approval.reason[:100]}' if approval.reason else ''))
                try:
                    comment = TicketComment.objects.create(
                        ticket=ticket, author=request.user, is_internal=False,
                        body=(f'{approval.get_decision_display()} (e-signed by {approval.signer_name})'
                              + (f': {approval.reason}' if approval.reason else '')))
                    from helpdesk.notifications import notify_new_comment
                    notify_new_comment(ticket, comment)
                except Exception: pass
                messages.success(request, f'Ticket {approval.get_decision_display().lower()} and e-signed.')
                return redirect('ticket_detail', ticket_id=ticket_id)

            messages.error(request, 'Signature not saved — please fix the highlighted field(s).')

    audit_trail = AuditLog.objects.filter(
        model_name='Ticket', object_id=ticket.ticket_id
    ).order_by('-timestamp')
    agents = User.objects.filter(is_staff=True, is_active=True)
    approvals = list(ticket.approvals.all())

    context = {
        'ticket':         ticket,
        'comment_form':   comment_form,
        'audit_trail':    audit_trail,
        'agents':         agents,
        'status_choices': Ticket.STATUS_CHOICES,
        'all_tags':       Tag.objects.order_by('name'),
        'approvals':      approvals,
        'latest_approval': approvals[0] if approvals else None,
        'esign_values':   esign_values,
        'esign_errors':   esign_errors,
    }
    from helpdesk.notifications import mark_conversation_read
    mark_conversation_read(request.user, ticket.ticket_id)   # opening the chat clears its unread count
    return render(request, 'helpdesk/ticket_detail.html', context)


@login_required
def ticket_create(request):
    """
    Retired. Tickets are now raised through one form — the portal request
    page — by everyone. This redirect keeps old bookmarks and links working
    and carries VoIP pre-fill parameters (?caller=, ?caller_name=, ?source=,
    ?voip_call=) across.
    """
    url = reverse('portal_create')
    if request.GET:
        url += '?' + request.GET.urlencode()
    return redirect(url)


@login_required
def ticket_edit(request, ticket_id):
    # Tickets are immutable once created. Status changes go through the
    # dedicated status_change action in ticket_detail (staff/admin only);
    # no other field may be edited by anyone, including administrators.
    messages.error(
        request,
        'Tickets cannot be edited after creation. Use the status control '
        'on the ticket page to update its progress.'
    )
    return redirect('ticket_detail', ticket_id=ticket_id)


@login_required
def reports(request):
    now    = timezone.now()
    period = request.GET.get('period', '30')
    try:
        days = int(period)
    except ValueError:
        days = 30

    # ── Year context ──────────────────────────────────────────────────────────
    active_year    = request.session.get('active_db_year')
    active_db_name = request.session.get('active_db_name', '')
    is_past_year   = bool(active_year)

    # For past year archives, show ALL records in that DB instead of a date window
    # (the date-window filter is only useful for current year live data)
    try:
        if is_past_year:
            # Show all tickets in the archive year
            tickets    = Ticket.objects.all()
            period_label = f'{active_year} Full Year Archive'
        else:
            start_date = now - timedelta(days=days)
            tickets    = Ticket.objects.filter(created_at__gte=start_date)
            period_label = f'Last {days} days'

        voip_total = voip_completed = voip_missed = voip_avg_duration = 0
        try:
            from voip.models import VoIPCall
            vcalls = VoIPCall.objects.all() if is_past_year else \
                     VoIPCall.objects.filter(initiated_at__gte=start_date if not is_past_year else now - timedelta(days=36500))
            if not is_past_year:
                vcalls = VoIPCall.objects.filter(initiated_at__gte=start_date)
            else:
                vcalls = VoIPCall.objects.all()
            voip_total     = vcalls.count()
            voip_completed = vcalls.filter(status='COMPLETED').count()
            voip_missed    = vcalls.filter(status='MISSED').count()
            voip_avg_duration = vcalls.filter(status='COMPLETED').aggregate(
                avg=Avg('talk_duration_seconds'))['avg'] or 0
        except Exception:
            pass

        context = {
            'period':       period,
            'period_label': period_label,
            'is_past_year': is_past_year,
            'active_year':  active_year,
            'active_db_name': active_db_name,
            'total_created':  tickets.count(),
            'total_resolved': tickets.filter(status__in=['RESOLVED', 'CLOSED']).count(),
            'total_pending':  tickets.filter(status__in=['OPEN', 'IN_PROGRESS', 'PENDING']).count(),
            'avg_resolution': tickets.filter(
                resolution_time_minutes__isnull=False
            ).aggregate(avg=Avg('resolution_time_minutes'))['avg'] or 0,
            'by_priority': list(tickets.values('priority').annotate(count=Count('id'))),
            'by_category': list(
                tickets.values('category__name').annotate(count=Count('id')).order_by('-count')[:10]
            ),
            'by_agent': list(
                tickets.filter(assigned_to__isnull=False)
                .values('assigned_to__username')
                .annotate(total=Count('id'),
                          resolved=Count('id', filter=Q(status__in=['RESOLVED', 'CLOSED'])))
                .order_by('-total')[:10]
            ),
            'by_source':       list(tickets.values('source').annotate(count=Count('id'))),
            'voip_total':      voip_total,
            'voip_completed':  voip_completed,
            'voip_missed':     voip_missed,
            'voip_avg_duration': voip_avg_duration,
            'tag_cloud':       list(Tag.objects.annotate(
                tc=Count('ticket', distinct=True)
            ).filter(tc__gt=0).order_by('-tc')[:20]),
        }
    except Exception:
        context = {
            'period': period, 'period_label': f'Error — {active_db_name} unreachable',
            'is_past_year': is_past_year, 'active_year': active_year,
            'active_db_name': active_db_name, 'db_error': True,
            'total_created': 0, 'total_resolved': 0, 'total_pending': 0,
            'avg_resolution': 0, 'by_priority': [], 'by_category': [],
            'by_agent': [], 'by_source': [], 'voip_total': 0,
            'voip_completed': 0, 'voip_missed': 0, 'voip_avg_duration': 0,
        }

    return render(request, 'helpdesk/reports.html', context)


@login_required
def audit_log(request):
    if not request.user.is_staff:
        messages.error(request, 'Access denied. The audit trail is restricted to IT staff and administrators.')
        return redirect('dashboard')

    # Superusers see everything; staff see only their own audit events
    if request.user.is_superuser:
        logs = AuditLog.objects.select_related('user').all()
    else:
        logs = AuditLog.objects.select_related('user').filter(user=request.user)

    search = request.GET.get('search', '')
    action = request.GET.get('action', '')
    model  = request.GET.get('model', '')

    if search:
        logs = logs.filter(
            Q(object_repr__icontains=search) | Q(user__username__icontains=search)
        )
    if action:
        logs = logs.filter(action=action)
    if model:
        logs = logs.filter(model_name=model)

    paginator = Paginator(logs, 50)
    page = request.GET.get('page', 1)
    logs_page = paginator.get_page(page)

    context = {
        'logs': logs_page,
        'action_choices': AuditLog.ACTION_CHOICES,
        'filters': {'search': search, 'action': action, 'model': model},
    }
    return render(request, 'helpdesk/audit_log.html', context)


def api_stats(request):
    """AJAX endpoint for live dashboard counter refresh."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    active_calls = 0
    try:
        from voip.models import VoIPCall
        active_calls = VoIPCall.objects.filter(
            status__in=['RINGING', 'ACTIVE', 'ON_HOLD']).count()
    except Exception:
        pass

    return JsonResponse({
        'active_calls': active_calls,
        'open_tickets': Ticket.objects.filter(
            status__in=['OPEN', 'IN_PROGRESS']).count(),
        'timestamp': timezone.now().isoformat(),
    })


# ── Glossary ──────────────────────────────────────────────────────────────────

@login_required
def glossary(request):
    terms = GlossaryTerm.objects.select_related('added_by').all()
    search   = request.GET.get('search', '')
    category = request.GET.get('category', '')

    if search:
        terms = terms.filter(
            Q(term__icontains=search) | Q(definition__icontains=search)
        )
    if category:
        terms = terms.filter(category__icontains=category)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add':
            term_text  = request.POST.get('term', '').strip()
            definition = request.POST.get('definition', '').strip()
            cat        = request.POST.get('category', '').strip()
            example    = request.POST.get('example', '').strip()
            if not term_text or not definition:
                messages.error(request, 'Term and definition are required.')
            elif GlossaryTerm.objects.filter(term__iexact=term_text).exists():
                messages.error(request, f'"{term_text}" already exists in the glossary.')
            else:
                GlossaryTerm.objects.create(
                    term=term_text, definition=definition,
                    category=cat, example=example, added_by=request.user
                )
                log_action(request, AuditLog.ACTION_CREATE, 'GlossaryTerm',
                           term_text, term_text, notes='Glossary term added')
                messages.success(request, f'"{term_text}" added to the glossary.')
            return redirect('glossary')

        elif action == 'delete':
            if not request.user.is_staff:
                messages.error(request, 'Only staff can delete glossary terms.')
                return redirect('glossary')
            try:
                t = GlossaryTerm.objects.get(pk=request.POST.get('term_id'))
                name = t.term
                t.delete()
                log_action(request, AuditLog.ACTION_DELETE, 'GlossaryTerm',
                           str(t.pk), name, notes='Glossary term deleted')
                messages.success(request, f'"{name}" removed from glossary.')
            except GlossaryTerm.DoesNotExist:
                messages.error(request, 'Term not found.')
            return redirect('glossary')

        elif action == 'edit':
            if not request.user.is_staff:
                messages.error(request, 'Only staff can edit glossary terms.')
                return redirect('glossary')
            try:
                t = GlossaryTerm.objects.get(pk=request.POST.get('term_id'))
                t.term       = request.POST.get('term', t.term).strip()
                t.definition = request.POST.get('definition', t.definition).strip()
                t.category   = request.POST.get('category', t.category).strip()
                t.example    = request.POST.get('example', t.example).strip()
                t.save()
                messages.success(request, f'"{t.term}" updated.')
            except GlossaryTerm.DoesNotExist:
                messages.error(request, 'Term not found.')
            return redirect('glossary')

    categories = GlossaryTerm.objects.values_list(
        'category', flat=True
    ).distinct().exclude(category='').order_by('category')

    return render(request, 'helpdesk/glossary.html', {
        'terms': terms,
        'search': search,
        'category': category,
        'categories': categories,
        'total': terms.count(),
    })


# ── Yearly Database Switcher ──────────────────────────────────────────────────

@login_required
def database_switcher(request):
    years      = DatabaseYear.objects.filter(is_active=True)
    active_year = request.session.get('active_db_year', None)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'switch':
            try:
                yr = DatabaseYear.objects.get(pk=request.POST.get('year_id'), is_active=True)
                request.session['active_db_year'] = yr.year
                request.session['active_db_name'] = yr.db_name
                log_action(request, AuditLog.ACTION_UPDATE, 'DatabaseYear',
                           str(yr.year), str(yr), notes=f'Switched to {yr.year} database')
                messages.success(
                    request,
                    f'Now viewing {yr.year} records (database: {yr.db_name}).'
                )
            except DatabaseYear.DoesNotExist:
                messages.error(request, 'Database year not found.')
            return redirect('database_switcher')

        elif action == 'create_year' and request.user.is_superuser:
            new_year = request.POST.get('new_year', '').strip()
            db_name  = request.POST.get('db_name', '').strip()
            desc     = request.POST.get('description', '').strip()
            if not new_year or not db_name:
                messages.error(request, 'Year and database name are required.')
            elif DatabaseYear.objects.filter(year=new_year).exists():
                messages.error(request, f'Year {new_year} already exists.')
            else:
                db_year = DatabaseYear.objects.create(
                    year=int(new_year), db_name=db_name,
                    description=desc, created_by=request.user, is_active=True,
                )
                log_action(request, AuditLog.ACTION_CREATE, 'DatabaseYear',
                           new_year, str(db_year),
                           notes=f'New year database registered: {db_name}')
                messages.success(
                    request,
                    f'{new_year} registered as "{db_name}". '
                    f'Run the SQL commands shown to create it in PostgreSQL, '
                    f'then run: py manage.py migrate --database={db_name}'
                )
            return redirect('database_switcher')

        elif action == 'set_current' and request.user.is_superuser:
            try:
                yr = DatabaseYear.objects.get(pk=request.POST.get('year_id'))
                yr.is_current = True
                yr.save()
                messages.success(request, f'{yr.year} is now the current operating year.')
            except DatabaseYear.DoesNotExist:
                messages.error(request, 'Year not found.')
            return redirect('database_switcher')

        elif action == 'clear_session':
            request.session.pop('active_db_year', None)
            request.session.pop('active_db_name', None)
            messages.success(request, 'Returned to current year.')
            return redirect('database_switcher')

    import os
    current_year = timezone.now().year
    return render(request, 'helpdesk/database_switcher.html', {
        'years':          years,
        'active_year':    active_year,
        'active_db_name': request.session.get('active_db_name', ''),
        'current_year':   current_year,
        'suggested_db':   f'it_helpdesk_{current_year}',
        'db_user':        os.environ.get('DB_USER', 'it_helpdesk_user'),
    })


# ── Word Prediction API ───────────────────────────────────────────────────────

def api_suggest(request):
    """
    Return autocomplete suggestions for a given prefix and field.
    GET /dashboard/api/suggest/?q=USB&field=title&category=Hardware&limit=8
    Open to all authenticated users; read-only.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'suggestions': []})

    q         = request.GET.get('q', '').strip().lower()
    field     = request.GET.get('field', LearnedPhrase.FIELD_TITLE)
    category  = request.GET.get('category', '')
    limit     = min(int(request.GET.get('limit', 8)), 20)

    if len(q) < 2:
        return JsonResponse({'suggestions': []})

    # Base query — match phrases starting with q OR containing q as a word
    qs = LearnedPhrase.objects.filter(
        field_name=field,
        phrase__icontains=q,
    )

    # Boost category-specific matches by fetching them first
    if category:
        cat_matches  = list(qs.filter(category__iexact=category)
                              .order_by('-use_count', '-last_used')
                              .values_list('phrase', flat=True)[:limit])
        gen_matches  = list(qs.exclude(category__iexact=category)
                              .order_by('-use_count', '-last_used')
                              .values_list('phrase', flat=True)[:limit])
        # Merge, deduplicate, cap
        seen = set()
        phrases = []
        for p in cat_matches + gen_matches:
            if p not in seen:
                seen.add(p)
                phrases.append(p)
                if len(phrases) >= limit:
                    break
    else:
        phrases = list(qs.order_by('-use_count', '-last_used')
                         .values_list('phrase', flat=True)[:limit])

    return JsonResponse({'suggestions': phrases, 'q': q, 'field': field})


def api_learn(request):
    """
    Manually trigger learning from submitted text.
    POST /dashboard/api/learn/   {text, field, category}
    Used for real-time learning as user types (optional enhancement).
    """
    if not request.user.is_authenticated or request.method != 'POST':
        return JsonResponse({'ok': False}, status=403)

    import json
    try:
        data     = json.loads(request.body)
        text     = data.get('text', '').strip()
        field    = data.get('field', LearnedPhrase.FIELD_TITLE)
        category = data.get('category', '')
        if text and field:
            LearnedPhrase.learn(text, field, category=category)
        return JsonResponse({'ok': True})
    except Exception:
        return JsonResponse({'ok': False})


# ── Notifications poll ────────────────────────────────────────────────────────
def api_notifications(request):
    """
    Notifications for the WhatsApp-style panel.

    Returns the raw list (`notifications`) plus the same messages grouped into
    one conversation per ticket (`conversations`), each with an unread count.
    `read` means the person has actually seen it (`read_at`) — not merely that
    it reached their browser (`delivered`).
    """
    if not request.user.is_authenticated:
        return JsonResponse({'notifications': [], 'conversations': [], 'unread': 0})

    from helpdesk.models import PendingNotification
    from helpdesk.notifications import build_conversations

    notifs = PendingNotification.objects.filter(
        user=request.user
    ).order_by('-created_at', '-id')[:100]

    items = []
    for n in notifs:
        try:
            payload = json.loads(n.payload)
        except Exception:
            continue
        payload['id']         = n.pk
        payload['read']       = n.read_at is not None
        payload['created_at'] = n.created_at.isoformat() if n.created_at else None
        if n.conversation:
            payload.setdefault('ticket_id', n.conversation)
        items.append(payload)

    conversations = build_conversations(items)
    unread = sum(1 for i in items if not i['read'])
    return JsonResponse({'notifications': items, 'conversations': conversations,
                         'unread': unread, 'count': len(items)})


@login_required
def api_notifications_read(request):
    """
    Mark notifications as read: one (`id`), a whole conversation (`conversation`
    = ticket id, like opening a chat) or everything (`all`).
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    from helpdesk.models import PendingNotification
    try:
        data = json.loads(request.body)
        qs = PendingNotification.objects.filter(user=request.user, read_at__isnull=True)
        if data.get('all'):
            pass
        elif data.get('conversation'):
            qs = qs.filter(conversation=str(data['conversation'])[:40])
        elif data.get('id'):
            qs = qs.filter(pk=data['id'])
        else:
            return JsonResponse({'ok': False, 'error': 'nothing to mark'}, status=400)
        updated = qs.update(read_at=timezone.now(), delivered=True)
        remaining = PendingNotification.objects.filter(
            user=request.user, read_at__isnull=True).count()
        return JsonResponse({'ok': True, 'updated': updated, 'unread': remaining})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)


def api_push_subscribe(request):
    if not request.user.is_authenticated or request.method!='POST':
        return JsonResponse({'ok':False},status=403)
    try:
        data = json.loads(request.body)
        sub  = data.get('subscription','')
        ena  = data.get('enabled',True)
        p = request.user.profile
        p.push_subscription = json.dumps(sub) if isinstance(sub,dict) else (sub or '')
        p.push_enabled = bool(ena) and bool(sub)
        p.save(update_fields=['push_subscription','push_enabled'])
        return JsonResponse({'ok':True,'push_enabled':p.push_enabled})
    except Exception as e:
        return JsonResponse({'ok':False,'error':str(e)})


def api_theme(request):
    if not request.user.is_authenticated or request.method!='POST':
        return JsonResponse({'ok':False},status=403)
    try:
        data  = json.loads(request.body)
        theme = 'light' if data.get('theme')=='light' else 'dark'
        p = request.user.profile
        p.theme = theme
        p.save(update_fields=['theme'])
        request.session['user_theme'] = theme
        return JsonResponse({'ok':True,'theme':theme})
    except Exception as e:
        return JsonResponse({'ok':False,'error':str(e)})


def api_bulk_action(request):
    if not request.user.is_staff:
        return JsonResponse({'ok':False,'error':'Permission denied'},status=403)
    if request.method!='POST':
        return JsonResponse({'ok':False},status=405)
    try:
        data       = json.loads(request.body)
        action     = data.get('action')
        ticket_ids = data.get('ticket_ids',[])
        if not ticket_ids:
            return JsonResponse({'ok':False,'error':'No tickets selected'})
        tickets = Ticket.objects.filter(ticket_id__in=ticket_ids)
        count   = 0
        if action=='close':
            count = tickets.filter(status__in=['OPEN','IN_PROGRESS','PENDING','RESOLVED']).update(status='CLOSED')
            log_action(request,AuditLog.ACTION_UPDATE,'Ticket','',f'Bulk close',notes=f'Bulk closed {count} tickets')
        elif action=='resolve':
            count = tickets.filter(status__in=['OPEN','IN_PROGRESS','PENDING']).update(status='RESOLVED',resolved_at=timezone.now())
            log_action(request,AuditLog.ACTION_UPDATE,'Ticket','',f'Bulk resolve',notes=f'Bulk resolved {count} tickets')
        elif action=='assign_me':
            count = tickets.filter(assigned_to__isnull=True).update(assigned_to=request.user,status='IN_PROGRESS')
            log_action(request,AuditLog.ACTION_ASSIGN,'Ticket','',f'Bulk assign to {request.user.username}')
        elif action=='delete' and request.user.is_superuser:
            count = tickets.count(); tickets.delete()
            log_action(request,AuditLog.ACTION_DELETE,'Ticket','',f'Bulk delete {count}')
        else:
            return JsonResponse({'ok':False,'error':f'Unknown action: {action}'})
        return JsonResponse({'ok':True,'count':count,'action':action})
    except Exception as e:
        return JsonResponse({'ok':False,'error':str(e)})


# ── Knowledge Base ────────────────────────────────────────────────────────────

@login_required
def kb_list(request):
    articles = KBArticle.objects.filter(is_published=True).select_related('category','author')
    search   = request.GET.get('search','')
    category = request.GET.get('category','')
    if search:
        articles = articles.filter(
            Q(title__icontains=search)|Q(content__icontains=search)|
            Q(summary__icontains=search)|Q(tags__icontains=search))
    if category:
        articles = articles.filter(category_id=category)
    categories = Category.objects.all()
    return render(request,'helpdesk/kb_list.html',{
        'articles':articles,'search':search,'category':category,'categories':categories,
        'total':articles.count()})

@login_required
def kb_detail(request, pk):
    article = get_object_or_404(KBArticle, pk=pk, is_published=True)
    article.views += 1; article.save(update_fields=['views'])
    if request.method == 'POST':
        if request.POST.get('feedback') == 'helpful':
            article.helpful += 1
        else:
            article.not_helpful += 1
        article.save(update_fields=['helpful','not_helpful'])
        messages.success(request,'Thanks for your feedback!')
        return redirect('kb_detail', pk=pk)
    related = KBArticle.objects.filter(
        is_published=True, category=article.category
    ).exclude(pk=pk)[:4]
    return render(request,'helpdesk/kb_detail.html',{'article':article,'related':related})

@login_required
def kb_create(request):
    if not request.user.is_staff:
        messages.error(request,'Only IT staff can create KB articles.')
        return redirect('kb_list')
    if request.method == 'POST':
        title   = request.POST.get('title','').strip()
        content = request.POST.get('content','').strip()
        if not title or not content:
            messages.error(request,'Title and content are required.')
        else:
            article = KBArticle.objects.create(
                title=title, summary=request.POST.get('summary','').strip(),
                content=content, tags=request.POST.get('tags','').strip(),
                category_id=request.POST.get('category') or None,
                is_published=request.POST.get('is_published')=='on',
                author=request.user)
            log_action(request,AuditLog.ACTION_CREATE,'KBArticle',
                       article.pk,article.title,notes='KB article created')
            messages.success(request,f'Article "{title}" published.')
            return redirect('kb_detail', pk=article.pk)
    return render(request,'helpdesk/kb_form.html',{
        'categories':Category.objects.all(),'page_title':'New KB Article'})

@login_required
def kb_edit(request, pk):
    article = get_object_or_404(KBArticle, pk=pk)
    if not request.user.is_staff:
        messages.error(request,'Only IT staff can edit KB articles.')
        return redirect('kb_detail', pk=pk)
    if request.method == 'POST':
        article.title   = request.POST.get('title','').strip()
        article.summary = request.POST.get('summary','').strip()
        article.content = request.POST.get('content','').strip()
        article.tags    = request.POST.get('tags','').strip()
        article.category_id = request.POST.get('category') or None
        article.is_published = request.POST.get('is_published')=='on'
        article.save()
        log_action(request,AuditLog.ACTION_UPDATE,'KBArticle',
                   article.pk,article.title,notes='KB article updated')
        messages.success(request,'Article updated.')
        return redirect('kb_detail', pk=pk)
    return render(request,'helpdesk/kb_form.html',{
        'article':article,'categories':Category.objects.all(),
        'page_title':f'Edit — {article.title}'})


# ── Asset Registry ────────────────────────────────────────────────────────────

@login_required
def asset_list(request):
    if not request.user.is_staff:
        messages.error(request,'Asset registry is for IT staff only.')
        return redirect('dashboard')
    assets = Asset.objects.select_related('assigned_to_user','created_by').all()
    search  = request.GET.get('search','')
    atype   = request.GET.get('type','')
    status  = request.GET.get('status','')
    dept    = request.GET.get('department','')
    if search:
        assets = assets.filter(
            Q(asset_tag__icontains=search)|Q(name__icontains=search)|
            Q(serial_number__icontains=search)|Q(location__icontains=search)|
            Q(imei__icontains=search)|Q(imei2__icontains=search)|Q(imei3__icontains=search)|
            Q(assigned_to_name__icontains=search))
    if atype:  assets = assets.filter(asset_type=atype)
    if status: assets = assets.filter(status=status)
    if dept:   assets = assets.filter(department__icontains=dept)
    paginator = Paginator(assets, 25)
    page      = request.GET.get('page',1)
    return render(request,'helpdesk/asset_list.html',{
        'assets':paginator.get_page(page),
        'total':assets.count(),
        'filters':{'search':search,'type':atype,'status':status,'department':dept},
        'type_choices':Asset.TYPE_CHOICES,
        'status_choices':Asset.STATUS_CHOICES})

@login_required
def asset_detail(request, pk):
    if not request.user.is_staff:
        return redirect('dashboard')
    asset = get_object_or_404(Asset, pk=pk)
    if request.method == 'POST' and request.user.is_superuser:
        action = request.POST.get('action')
        if action == 'delete':
            name = str(asset)
            asset.delete()
            log_action(request,AuditLog.ACTION_DELETE,'Asset',pk,name)
            messages.success(request,'Asset deleted.')
            return redirect('asset_list')
    return render(request,'helpdesk/asset_detail.html',{'asset':asset})

# ── Asset form helpers ────────────────────────────────────────────────────────
# Serial numbers that are placeholders rather than real identifiers; these are
# allowed to repeat across assets.
_SERIAL_PLACEHOLDERS = {'', 'n/a', 'na', 'none', 'nil', 'unknown', 'tbc', 'tbd', '-', '--'}

_ASSET_TEXT_FIELDS = ['asset_tag', 'name', 'asset_type', 'status', 'serial_number', 'imei', 'imei2', 'imei3',
                      'manufacturer', 'model', 'department', 'section', 'location',
                      'assigned_to_name', 'purchase_date', 'purchase_cost',
                      'warranty_expiry', 'notes']


def _asset_values_from_post(post):
    return {f: post.get(f, '').strip() for f in _ASSET_TEXT_FIELDS}


def _asset_values_from_instance(asset):
    vals = {f: ('' if getattr(asset, f) is None else str(getattr(asset, f)))
            for f in _ASSET_TEXT_FIELDS}
    return vals


def _find_serial_conflict(serial, exclude_pk=None):
    """Return the existing Asset that already uses this serial, or None."""
    key = serial.strip().lower()
    if key in _SERIAL_PLACEHOLDERS:
        return None
    qs = Asset.objects.filter(serial_number__iexact=serial.strip())
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


def _find_imei_conflict(number, exclude_pk=None):
    """The existing Asset that already carries this IMEI (in any of its three columns), or None."""
    qs = Asset.objects.filter(Q(imei__iexact=number) | Q(imei2__iexact=number) | Q(imei3__iexact=number))
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


def _validate_asset(values, instance=None):
    """
    Validate posted asset data. Returns an {field: message} dict — empty when
    the data is acceptable. Nothing is saved here.
    """
    from datetime import date
    from decimal import Decimal, InvalidOperation
    errors = {}
    editing = instance is not None

    tag = values['asset_tag']
    if not editing:
        if not tag:
            errors['asset_tag'] = 'Asset tag is required.'
        elif Asset.objects.filter(asset_tag__iexact=tag).exists():
            errors['asset_tag'] = f'Asset tag "{tag}" already exists. Use a different tag.'
    if not values['name']:
        errors['name'] = 'Asset name is required.'

    if values['asset_type'] not in dict(Asset.TYPE_CHOICES):
        errors['asset_type'] = 'Choose a valid asset type.'
    if values['status'] not in dict(Asset.STATUS_CHOICES):
        errors['status'] = 'Choose a valid status.'

    serial = values['serial_number']
    conflict = _find_serial_conflict(serial, instance.pk if editing else None)
    if conflict:
        errors['serial_number'] = (
            f'Serial number "{serial}" is already registered to asset '
            f'{conflict.asset_tag} ({conflict.name}). Check the serial number and correct it.')

    # IMEI numbers: kept exactly as printed (some carry a "/01" suffix), so only the characters are
    # checked — but one IMEI can only belong to one asset, and may not be entered twice on the same one.
    used = {}
    for f, label in (('imei', 'IMEI 1'), ('imei2', 'IMEI 2'), ('imei3', 'IMEI 3')):
        num = values[f]
        if not num:
            continue
        if len(num) > 40:
            errors[f] = 'Too long — maximum 40 characters.'
        elif not re.fullmatch(r'[0-9A-Za-z/\-]+', num):
            errors[f] = 'Use only letters, numbers, "/" and "-", exactly as printed on the device.'
        elif num.lower() in used:
            errors[f] = f'This is the same number as {used[num.lower()]}.'
        else:
            used[num.lower()] = label
            clash = _find_imei_conflict(num, instance.pk if editing else None)
            if clash:
                errors[f] = (f'IMEI "{num}" is already registered to asset {clash.asset_tag} ({clash.name}). '
                             f'Check the number and correct it.')

    parsed = {}
    for f in ('purchase_date', 'warranty_expiry'):
        if values[f]:
            try:
                parsed[f] = date.fromisoformat(values[f])
            except ValueError:
                errors[f] = 'Enter a valid date.'
    if (parsed.get('purchase_date') and parsed.get('warranty_expiry')
            and parsed['warranty_expiry'] < parsed['purchase_date']):
        errors['warranty_expiry'] = 'Warranty expiry cannot be before the purchase date.'

    if values['purchase_cost']:
        try:
            cost = Decimal(values['purchase_cost'])
            if cost < 0 or cost >= Decimal('100000000'):
                raise InvalidOperation
        except InvalidOperation:
            errors['purchase_cost'] = 'Enter a valid, non-negative amount.'

    for f, limit in (('asset_tag', 30), ('name', 150), ('serial_number', 100),
                     ('manufacturer', 100), ('model', 100), ('department', 100),
                     ('section', 100), ('location', 150), ('assigned_to_name', 150)):
        if len(values[f]) > limit and f not in errors:
            errors[f] = f'Too long — maximum {limit} characters.'
    return errors


def _apply_asset_values(asset, values):
    asset.name             = values['name']
    asset.asset_type       = values['asset_type']
    asset.status           = values['status']
    asset.serial_number    = values['serial_number']
    asset.imei, asset.imei2, asset.imei3 = values['imei'], values['imei2'], values['imei3']
    asset.manufacturer     = values['manufacturer']
    asset.model            = values['model']
    asset.department       = values['department']
    asset.section          = values['section']
    asset.location         = values['location']
    asset.assigned_to_name = values['assigned_to_name']
    asset.notes            = values['notes']
    asset.purchase_date    = values['purchase_date'] or None
    asset.warranty_expiry  = values['warranty_expiry'] or None
    asset.purchase_cost    = values['purchase_cost'] or None


def _render_asset_form(request, values, errors, page_title, asset=None):
    return render(request, 'helpdesk/asset_form.html', {
        'asset': asset, 'v': values, 'errors': errors,
        'type_choices': Asset.TYPE_CHOICES, 'status_choices': Asset.STATUS_CHOICES,
        'page_title': page_title,
    })


@login_required
def asset_create(request):
    if not request.user.is_staff:
        return redirect('dashboard')
    if request.method == 'POST':
        values = _asset_values_from_post(request.POST)
        errors = _validate_asset(values)
        if not errors:
            from django.db import IntegrityError
            asset = Asset(asset_tag=values['asset_tag'], created_by=request.user)
            _apply_asset_values(asset, values)
            try:
                asset.save()
            except IntegrityError:   # lost a race with another user
                errors['asset_tag'] = f'Asset tag "{values["asset_tag"]}" already exists. Use a different tag.'
            else:
                log_action(request, AuditLog.ACTION_CREATE, 'Asset',
                           asset.pk, str(asset), notes='Asset registered')
                messages.success(request, f'Asset {asset.asset_tag} registered.')
                return redirect('asset_detail', pk=asset.pk)
        # Rejected: re-show the form with everything the person typed intact
        messages.error(request, 'Asset not saved — please correct the highlighted field(s). Everything else has been kept.')
        return _render_asset_form(request, values, errors, 'Register New Asset')
    values = {f: '' for f in _ASSET_TEXT_FIELDS}
    values.update(asset_type='DESKTOP', status='ACTIVE')
    return _render_asset_form(request, values, {}, 'Register New Asset')


@login_required
def asset_edit(request, pk):
    if not request.user.is_staff:
        return redirect('dashboard')
    asset = get_object_or_404(Asset, pk=pk)
    if request.method == 'POST':
        values = _asset_values_from_post(request.POST)
        values['asset_tag'] = asset.asset_tag          # tag is read-only when editing
        errors = _validate_asset(values, instance=asset)
        if not errors:
            _apply_asset_values(asset, values)
            asset.save()
            log_action(request, AuditLog.ACTION_UPDATE, 'Asset',
                       asset.pk, str(asset), notes='Asset updated')
            messages.success(request, 'Asset updated.')
            return redirect('asset_detail', pk=pk)
        messages.error(request, 'Asset not updated — please correct the highlighted field(s). Everything else has been kept.')
        return _render_asset_form(request, values, errors, f'Edit {asset.asset_tag}', asset=asset)
    return _render_asset_form(request, _asset_values_from_instance(asset), {},
                              f'Edit {asset.asset_tag}', asset=asset)


@login_required
def api_check_serial(request):
    """Live duplicate-serial check used by the asset form (advisory only —
    the server re-validates on submit)."""
    if not request.user.is_staff:
        return JsonResponse({'duplicate': False})
    serial = request.GET.get('serial', '').strip()
    exclude = request.GET.get('exclude') or None
    conflict = _find_serial_conflict(serial, int(exclude) if exclude and exclude.isdigit() else None)
    if not conflict:
        return JsonResponse({'duplicate': False})
    return JsonResponse({'duplicate': True, 'asset_tag': conflict.asset_tag,
                         'name': conflict.name})


# ── Database Backup / Restore / Delete ────────────────────────────────────────

@login_required
def db_backup(request):
    if not request.user.is_staff:
        messages.error(request,'Only IT staff can manage backups.')
        return redirect('dashboard')

    from helpdesk.models import DatabaseBackup
    backups = DatabaseBackup.objects.all().select_related('performed_by')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create':
            import os, subprocess
            from django.conf import settings
            db_cfg  = settings.DATABASES.get('default',{})
            db_name = db_cfg.get('NAME','it_helpdesk')
            db_user = db_cfg.get('USER','it_helpdesk_user')
            db_host = db_cfg.get('HOST','localhost')
            db_port = db_cfg.get('PORT','5432')

            ts       = timezone.now().strftime('%Y%m%d_%H%M%S')
            filename = f'it_helpdesk_backup_{ts}.sql'
            backup_dir = os.path.join(settings.BASE_DIR, 'backups')
            os.makedirs(backup_dir, exist_ok=True)
            filepath = os.path.join(backup_dir, filename)

            note = request.POST.get('note','').strip()

            # For SQLite (dev), just copy the db file
            if 'sqlite' in db_cfg.get('ENGINE',''):
                import shutil
                src = db_cfg.get('NAME','')
                if os.path.exists(src):
                    shutil.copy2(src, filepath.replace('.sql','.sqlite3'))
                    filepath = filepath.replace('.sql','.sqlite3')
                    filename = filename.replace('.sql','.sqlite3')
                    file_size = os.path.getsize(filepath)
                    status = 'OK'
                else:
                    status = 'FAIL'; file_size = 0
            else:
                # PostgreSQL: use pg_dump
                env = os.environ.copy()
                env['PGPASSWORD'] = db_cfg.get('PASSWORD','')
                cmd = ['pg_dump','-h',db_host,'-p',str(db_port),
                       '-U',db_user,'-F','c','-f',filepath, db_name]
                try:
                    subprocess.run(cmd, env=env, check=True,
                                   capture_output=True, timeout=120)
                    file_size = os.path.getsize(filepath)
                    status = 'OK'
                except Exception as e:
                    file_size = 0; status = 'FAIL'
                    note = f'{note}\nError: {str(e)}'

            backup = DatabaseBackup.objects.create(
                filename=filename, file_path=filepath,
                file_size=file_size, action='CREATE', status=status,
                db_name=db_name, note=note, performed_by=request.user)
            log_action(request,AuditLog.ACTION_CREATE,'DatabaseBackup',
                       backup.pk,filename,notes=f'DB backup {status}')
            if status=='OK':
                messages.success(request,f'Backup created: {filename} ({backup.file_size_display})')
            else:
                messages.error(request,f'Backup failed. Check logs.')

        elif action == 'delete' and request.user.is_superuser:
            backup_id = request.POST.get('backup_id')
            try:
                import os
                backup = DatabaseBackup.objects.get(pk=backup_id)
                name   = backup.filename
                # Delete file
                if backup.file_path and os.path.exists(backup.file_path):
                    os.remove(backup.file_path)
                backup.delete()
                log_action(request,AuditLog.ACTION_DELETE,'DatabaseBackup',
                           backup_id,name,notes='Backup file deleted')
                messages.success(request,f'Backup "{name}" deleted.')
            except DatabaseBackup.DoesNotExist:
                messages.error(request,'Backup not found.')

        elif action == 'restore' and request.user.is_superuser:
            backup_id = request.POST.get('backup_id')
            try:
                import os, subprocess
                from django.conf import settings
                backup  = DatabaseBackup.objects.get(pk=backup_id)
                db_cfg  = settings.DATABASES.get('default',{})
                db_name = db_cfg.get('NAME','it_helpdesk')
                db_user = db_cfg.get('USER','it_helpdesk_user')
                db_host = db_cfg.get('HOST','localhost')
                db_port = db_cfg.get('PORT','5432')

                if 'sqlite' in db_cfg.get('ENGINE',''):
                    import shutil
                    if os.path.exists(backup.file_path):
                        shutil.copy2(backup.file_path, db_cfg.get('NAME',''))
                        status = 'OK'
                    else:
                        status = 'FAIL'
                else:
                    env = os.environ.copy()
                    env['PGPASSWORD'] = db_cfg.get('PASSWORD','')
                    cmd = ['pg_restore','-h',db_host,'-p',str(db_port),
                           '-U',db_user,'-d',db_name,'--clean','--if-exists',
                           backup.file_path]
                    try:
                        subprocess.run(cmd,env=env,check=True,
                                       capture_output=True,timeout=300)
                        status = 'OK'
                    except Exception as e:
                        status = 'FAIL'

                DatabaseBackup.objects.create(
                    filename=backup.filename, file_path=backup.file_path,
                    file_size=backup.file_size, action='RESTORE', status=status,
                    db_name=db_name, performed_by=request.user,
                    note=f'Restored from backup {backup.pk}')
                log_action(request,AuditLog.ACTION_UPDATE,'DatabaseBackup',
                           backup.pk,backup.filename,
                           notes=f'DB restore attempt: {status}')
                if status=='OK':
                    messages.success(request,f'Database restored from {backup.filename}.')
                else:
                    messages.error(request,'Restore failed. Check that pg_restore is available.')
            except DatabaseBackup.DoesNotExist:
                messages.error(request,'Backup not found.')

        return redirect('db_backup')

    import os
    from django.conf import settings
    backup_dir = os.path.join(settings.BASE_DIR,'backups')
    os.makedirs(backup_dir, exist_ok=True)

    return render(request,'helpdesk/db_backup.html',{
        'backups': backups,
        'total':   backups.count(),
        'total_size': sum(b.file_size for b in backups),
    })


# ── Per-User Activity Audit ───────────────────────────────────────────────────

@login_required
def user_activity(request, user_id):
    """Full activity audit for a specific user — admin/staff only or own."""
    from django.contrib.auth.models import User as AuthUser
    target = get_object_or_404(AuthUser, pk=user_id)

    # Permission: admin sees all, staff sees non-admin, users see own only
    if not request.user.is_staff and target != request.user:
        messages.error(request,'Access denied.')
        return redirect('dashboard')
    if request.user.is_staff and not request.user.is_superuser and target.is_superuser:
        messages.error(request,'Cannot view admin activity.')
        return redirect('user_list')

    logs = AuditLog.objects.filter(user=target).order_by('-timestamp')

    # Filters
    action = request.GET.get('action','')
    search = request.GET.get('search','')
    date_from = request.GET.get('date_from','')
    date_to   = request.GET.get('date_to','')

    if action: logs = logs.filter(action=action)
    if search: logs = logs.filter(
        Q(object_repr__icontains=search)|Q(notes__icontains=search)|
        Q(model_name__icontains=search))
    if date_from: logs = logs.filter(timestamp__date__gte=date_from)
    if date_to:   logs = logs.filter(timestamp__date__lte=date_to)

    paginator = Paginator(logs, 30)
    page      = request.GET.get('page',1)

    # Summary stats
    from django.db.models import Count
    action_summary = list(
        AuditLog.objects.filter(user=target)
        .values('action').annotate(count=Count('id'))
        .order_by('-count')
    )

    return render(request,'helpdesk/user_activity.html',{
        'target':         target,
        'logs':           paginator.get_page(page),
        'total':          logs.count(),
        'action_summary': action_summary,
        'action_choices': AuditLog.ACTION_CHOICES,
        'filters':{'action':action,'search':search,
                   'date_from':date_from,'date_to':date_to},
    })


# ── CSV Export ────────────────────────────────────────────────────────────────

@login_required
def export_tickets_csv(request):
    """Export ticket list as CSV — IT Staff and Admin only."""
    if not request.user.is_staff:
        messages.error(request, 'Only IT Staff and Administrators can export data.')
        return redirect('ticket_list')
    import csv
    from django.http import StreamingHttpResponse

    tickets = Ticket.objects.select_related('requester','assigned_to','category').all()

    # Apply same filters as ticket_list
    status   = request.GET.get('status','')
    priority = request.GET.get('priority','')
    if status:   tickets = tickets.filter(status=status)
    if priority: tickets = tickets.filter(priority=priority)

    log_action(request,AuditLog.ACTION_VIEW,'Ticket','',
               f'CSV export {tickets.count()} tickets')

    def rows():
        yield ['Ticket ID','Title','Status','Priority','Category','Source',
               'Requester','Department','Section','Assigned To',
               'Created','Resolved','Resolution (min)','SLA Breach']
        for t in tickets:
            yield [
                t.ticket_id, t.title, t.status, t.priority,
                t.category.name if t.category else '',
                t.source,
                t.requester_name or (t.requester.username if t.requester else ''),
                t.requester_department, t.requester_section,
                t.assigned_to.username if t.assigned_to else '',
                t.created_at.strftime('%Y-%m-%d %H:%M') if t.created_at else '',
                t.resolved_at.strftime('%Y-%m-%d %H:%M') if t.resolved_at else '',
                t.resolution_time_minutes or '',
                'Yes' if t.sla_breach else 'No',
            ]

    class Echo:
        def write(self, value): return value

    writer = csv.writer(Echo())
    response = StreamingHttpResponse(
        (writer.writerow(row) for row in rows()),
        content_type='text/csv')
    ts = timezone.now().strftime('%Y%m%d_%H%M')
    response['Content-Disposition'] = f'attachment; filename="it_helpdesk_tickets_{ts}.csv"'
    return response


@login_required
def export_assets_csv(request):
    """Export asset registry as CSV — IT Staff and Admin only."""
    if not request.user.is_staff:
        messages.error(request, 'Only IT Staff and Administrators can export data.')
        return redirect('asset_list')

    import csv
    from django.http import StreamingHttpResponse

    assets = Asset.objects.select_related('assigned_to_user', 'created_by').all()

    # Filters
    atype  = request.GET.get('type','')
    status = request.GET.get('status','')
    dept   = request.GET.get('department','')
    if atype:  assets = assets.filter(asset_type=atype)
    if status: assets = assets.filter(status=status)
    if dept:   assets = assets.filter(department__icontains=dept)

    log_action(request, AuditLog.ACTION_VIEW, 'Asset', '',
               f'CSV export {assets.count()} assets')

    def rows():
        yield ['Asset Tag','Name','Type','Status','Serial Number','IMEI 1','IMEI 2','IMEI 3','Manufacturer',
               'Model','Department','Section','Location','Assigned To',
               'Purchase Date','Purchase Cost (USD)','Warranty Expiry',
               'Warranty Expired','Notes','Registered By','Registered At']
        for a in assets:
            yield [
                a.asset_tag, a.name,
                a.get_asset_type_display(), a.get_status_display(),
                a.serial_number, a.imei, a.imei2, a.imei3, a.manufacturer, a.model,
                a.department, a.section, a.location,
                a.assigned_to_user.get_full_name() if a.assigned_to_user
                    else a.assigned_to_name,
                a.purchase_date.strftime('%Y-%m-%d') if a.purchase_date else '',
                a.purchase_cost or '',
                a.warranty_expiry.strftime('%Y-%m-%d') if a.warranty_expiry else '',
                'Yes' if a.is_warranty_expired else 'No',
                a.notes,
                a.created_by.username if a.created_by else '',
                a.created_at.strftime('%Y-%m-%d %H:%M') if a.created_at else '',
            ]

    class Echo:
        def write(self, value): return value

    writer = csv.writer(Echo())
    response = StreamingHttpResponse(
        (writer.writerow(row) for row in rows()),
        content_type='text/csv')
    ts = timezone.now().strftime('%Y%m%d_%H%M')
    response['Content-Disposition'] = f'attachment; filename="it_helpdesk_assets_{ts}.csv"'
    return response


# ═══════════════════════════════════════════════════════════════════════════════
# RECURRING TICKETS
# ═══════════════════════════════════════════════════════════════════════════════

@login_required
def recurring_list(request):
    if not request.user.is_staff:
        messages.error(request, 'IT Staff access required.')
        return redirect('dashboard')
    schedules = RecurringTicket.objects.select_related(
        'category','assigned_to','created_by').order_by('-is_active','-created_at')
    return render(request, 'helpdesk/recurring_list.html', {
        'schedules': schedules,
        'total': schedules.count(),
        'active': schedules.filter(is_active=True).count(),
    })


@login_required
def recurring_create(request):
    if not request.user.is_staff:
        messages.error(request, 'IT Staff access required.')
        return redirect('dashboard')
    from django.contrib.auth.models import User
    if request.method == 'POST':
        from django.utils import timezone as tz
        import calendar as cal
        freq = request.POST.get('frequency', 'WEEKLY')
        dom = request.POST.get('day_of_month', '1')
        try:
            dom = int(dom)
        except Exception:
            dom = 1
        # Compute next_due_at
        now = tz.now()
        today = now.date()
        if freq == 'DAILY':
            next_due = now + __import__('datetime').timedelta(days=1)
        elif freq == 'MONTHLY':
            m = today.month + 1
            y = today.year + (1 if m > 12 else 0)
            m = m if m <= 12 else m - 12
            day = min(dom, cal.monthrange(y, m)[1])
            from django.utils.timezone import make_aware
            import datetime
            next_due = make_aware(datetime.datetime(y, m, day, 6, 0))
        else:
            days = {'WEEKLY': 7, 'BIWEEKLY': 14}.get(freq, 7)
            next_due = now + __import__('datetime').timedelta(days=days)

        assigned_id = request.POST.get('assigned_to', '')
        cat_id = request.POST.get('category', '')
        sched = RecurringTicket.objects.create(
            name=request.POST['name'].strip(),
            frequency=freq,
            day_of_month=dom if freq == 'MONTHLY' else None,
            title_template=request.POST['title_template'].strip(),
            description_template=request.POST.get('description_template','').strip(),
            category_id=cat_id if cat_id else None,
            priority=request.POST.get('priority', 'medium'),
            assigned_to_id=assigned_id if assigned_id else None,
            is_active=bool(request.POST.get('is_active')),
            next_due_at=next_due,
            created_by=request.user,
        )
        log_action(request, AuditLog.ACTION_CREATE, 'RecurringTicket',
                   str(sched.pk), sched.name)
        messages.success(request, f'Recurring schedule "{sched.name}" created.')
        return redirect('recurring_list')

    cats = Category.objects.all()
    from django.contrib.auth.models import User
    staff = User.objects.filter(is_staff=True, is_active=True)
    return render(request, 'helpdesk/recurring_form.html', {
        'page_title': 'New Recurring Schedule',
        'categories': cats, 'staff_users': staff,
        'freq_choices': [('DAILY','Daily'),('WEEKLY','Weekly'),
                         ('BIWEEKLY','Bi-weekly'),('MONTHLY','Monthly')],
        'priority_choices': Ticket.PRIORITY_CHOICES,
    })


@login_required
def recurring_toggle(request, pk):
    if not request.user.is_staff:
        return redirect('dashboard')
    sched = get_object_or_404(RecurringTicket, pk=pk)
    sched.is_active = not sched.is_active
    sched.save(update_fields=['is_active'])
    state = 'enabled' if sched.is_active else 'paused'
    messages.success(request, f'Schedule "{sched.name}" {state}.')
    return redirect('recurring_list')


@login_required
def recurring_delete(request, pk):
    if not request.user.is_superuser:
        messages.error(request, 'Admin access required.')
        return redirect('recurring_list')
    sched = get_object_or_404(RecurringTicket, pk=pk)
    name = sched.name
    sched.delete()
    messages.success(request, f'Schedule "{name}" deleted.')
    return redirect('recurring_list')


# ═══════════════════════════════════════════════════════════════════════════════
# TICKET TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════

@login_required
def template_list(request):
    if not request.user.is_staff:
        messages.error(request, 'IT Staff access required.')
        return redirect('dashboard')
    templates = TicketTemplate.objects.select_related('category','created_by')\
                              .order_by('-is_active','-use_count','-created_at')
    return render(request, 'helpdesk/template_list.html', {
        'templates': templates,
        'total': templates.count(),
    })


TEMPLATE_ICONS = [
    ('fas fa-laptop', 'Laptop'), ('fas fa-print', 'Printer'),
    ('fas fa-wifi', 'Network'), ('fas fa-envelope', 'Email'),
    ('fas fa-lock', 'Password / Access'), ('fas fa-desktop', 'Desktop PC'),
    ('fas fa-mobile-screen', 'Phone'), ('fas fa-server', 'Server'),
    ('fas fa-bug', 'Software Bug'), ('fas fa-plug', 'Hardware'),
    ('fas fa-shield-halved', 'Security'), ('fas fa-database', 'Database'),
]
TEMPLATE_COLORS = [   # (hex, name) — the swatches offered; any #rrggbb is accepted
    ('#2ea043', 'Green'), ('#58a6ff', 'Blue'), ('#39c5cf', 'Teal'), ('#a371f7', 'Purple'),
    ('#db61a2', 'Pink'), ('#f85149', 'Red'), ('#f0883e', 'Orange'), ('#e3b341', 'Gold'),
    ('#8b949e', 'Grey'), ('#d2a8ff', 'Lilac'),
]


def _template_form_response(request, values, errors, page_title, template=None):
    return render(request, 'helpdesk/template_form.html', {
        'page_title': page_title, 'template': template, 'v': values, 'errors': errors,
        'categories': Category.objects.all(),
        'priority_choices': Ticket.PRIORITY_CHOICES,
        'icon_choices': TEMPLATE_ICONS, 'color_choices': TEMPLATE_COLORS,
    })


def _save_template(request, template=None):
    """Validate + save a ticket template; returns (template, values, errors)."""
    v = {
        'name': request.POST.get('name', '').strip(),
        'description': request.POST.get('description', '').strip(),
        'icon': request.POST.get('icon', '').strip(),
        'color': request.POST.get('color', '').strip().lower(),
        'title_template': request.POST.get('title_template', '').strip(),
        'description_template': request.POST.get('description_template', '').strip(),
        'category': request.POST.get('category', '').strip(),
        'priority': request.POST.get('priority', 'MEDIUM').strip().upper(),
        'is_active': 'on' if request.POST.get('is_active') else '',
    }
    errors = {}
    if not v['name']:
        errors['name'] = 'Template name is required.'
    elif len(v['name']) > 100:
        errors['name'] = 'Too long — maximum 100 characters.'
    elif TemplateQS(template).filter(name__iexact=v['name']).exists():
        errors['name'] = f'A template called "{v["name"]}" already exists.'
    if len(v['description']) > 200:
        errors['description'] = 'Too long — maximum 200 characters.'
    if not v['title_template']:
        errors['title_template'] = 'Ticket title is required.'
    elif len(v['title_template']) > 255:
        errors['title_template'] = 'Too long — maximum 255 characters.'
    if v['icon'] not in dict(TEMPLATE_ICONS):
        errors['icon'] = 'Choose an icon from the list.'
    if not re.fullmatch(r'#[0-9a-f]{6}', v['color']):
        errors['color'] = 'Choose a colour.'
    if v['priority'] not in dict(Ticket.PRIORITY_CHOICES):
        errors['priority'] = 'Choose a priority.'
    if v['category'] and not (v['category'].isdigit() and Category.objects.filter(pk=v['category']).exists()):
        errors['category'] = 'Choose a category from the list.'
    if errors:
        return None, v, errors

    obj = template or TicketTemplate(created_by=request.user)
    obj.name = v['name']; obj.description = v['description']; obj.icon = v['icon']
    obj.color = v['color']; obj.title_template = v['title_template']
    obj.description_template = v['description_template']
    obj.category_id = int(v['category']) if v['category'] else None
    obj.priority = v['priority']; obj.is_active = bool(v['is_active'])
    obj.save()
    return obj, v, {}


def TemplateQS(template):
    """Other templates (excluding the one being edited) — for the unique-name check."""
    qs = TicketTemplate.objects.all()
    return qs.exclude(pk=template.pk) if template else qs


@login_required
def template_create(request):
    if not request.user.is_staff:
        messages.error(request, 'IT Staff access required.')
        return redirect('dashboard')
    if request.method == 'POST':
        tmpl, values, errors = _save_template(request)
        if tmpl:
            log_action(request, AuditLog.ACTION_CREATE, 'TicketTemplate', str(tmpl.pk), tmpl.name)
            messages.success(request, f'Template "{tmpl.name}" created.')
            return redirect('template_list')
        messages.error(request, 'Template not saved — please correct the highlighted field(s). Everything else has been kept.')
        return _template_form_response(request, values, errors, 'New Ticket Template')
    values = dict(name='', description='', icon='fas fa-laptop', color='#2ea043', title_template='',
                  description_template='', category='', priority='MEDIUM', is_active='on')
    return _template_form_response(request, values, {}, 'New Ticket Template')


@login_required
def template_edit(request, pk):
    if not request.user.is_staff:
        messages.error(request, 'IT Staff access required.')
        return redirect('dashboard')
    template = get_object_or_404(TicketTemplate, pk=pk)
    if request.method == 'POST':
        tmpl, values, errors = _save_template(request, template)
        if tmpl:
            log_action(request, AuditLog.ACTION_UPDATE, 'TicketTemplate', str(tmpl.pk), tmpl.name)
            messages.success(request, f'Template "{tmpl.name}" updated.')
            return redirect('template_list')
        messages.error(request, 'Template not saved — please correct the highlighted field(s). Everything else has been kept.')
        return _template_form_response(request, values, errors, f'Edit {template.name}', template)
    values = dict(name=template.name, description=template.description, icon=template.icon,
                  color=template.color, title_template=template.title_template,
                  description_template=template.description_template,
                  category=str(template.category_id or ''), priority=(template.priority or 'MEDIUM').upper(),
                  is_active='on' if template.is_active else '')
    return _template_form_response(request, values, {}, f'Edit {template.name}', template)


@login_required
def template_delete(request, pk):
    if not request.user.is_superuser:
        messages.error(request, 'Admin access required.')
        return redirect('template_list')
    tmpl = get_object_or_404(TicketTemplate, pk=pk)
    name = tmpl.name
    tmpl.delete()
    messages.success(request, f'Template "{name}" deleted.')
    return redirect('template_list')


def api_templates(request):
    """Return active templates as JSON for quick-fill on the request form."""
    if not request.user.is_authenticated:
        from django.http import JsonResponse
        return JsonResponse({'templates': []})
    templates = TicketTemplate.objects.filter(is_active=True)\
                              .select_related('category')\
                              .order_by('-use_count','-created_at')[:20]
    data = [{
        'id': t.pk,
        'name': t.name,
        'icon': t.icon,
        'description': t.description,
        'title': t.title_template,
        'body': t.description_template,
        'category': t.category_id,
        'priority': t.priority,
    } for t in templates]
    from django.http import JsonResponse
    return JsonResponse({'templates': data})


def api_template_use(request, pk):
    """Increment use_count when a template is applied."""
    if request.method == 'POST' and request.user.is_authenticated:
        TicketTemplate.objects.filter(pk=pk).update(
            use_count=__import__('django.db.models',fromlist=['F']).F('use_count') + 1)
    from django.http import JsonResponse
    return JsonResponse({'ok': True})


# ═══════════════════════════════════════════════════════════════════════════════
# REQUESTER PORTAL — Simplified view for non-IT staff
# ═══════════════════════════════════════════════════════════════════════════════

def ticket_stage(ticket):
    """Where a request is on its journey: 1 Submitted · 2 Assigned · 3 In progress · 4 Resolved."""
    if ticket.status in ('RESOLVED', 'CLOSED'):
        return 4
    if ticket.status in ('IN_PROGRESS', 'PENDING', 'REOPENED'):
        return 3
    return 2 if ticket.assigned_to_id else 1


TICKET_STAGES = ['Submitted', 'Assigned', 'In progress', 'Resolved']


@login_required
def portal(request):
    """
    "My requests" — everyone's own requests, with search, status filters and a progress
    tracker. Open to IT staff as well, so they can follow the tickets they raised themselves.
    """
    user = request.user

    my_tickets = Ticket.objects.filter(requester=user).select_related(
        'category','assigned_to').order_by('-created_at')

    open_count     = my_tickets.filter(status__in=['OPEN','IN_PROGRESS','PENDING','REOPENED']).count()
    resolved_count = my_tickets.filter(status__in=['RESOLVED','CLOSED']).count()
    total          = my_tickets.count()

    flt = request.GET.get('status', 'all')
    q   = request.GET.get('q', '').strip()
    shown = my_tickets
    if flt == 'open':
        shown = shown.filter(status__in=['OPEN','IN_PROGRESS','PENDING','REOPENED'])
    elif flt == 'resolved':
        shown = shown.filter(status__in=['RESOLVED','CLOSED'])
    else:
        flt = 'all'
    if q:
        shown = shown.filter(Q(title__icontains=q) | Q(ticket_id__icontains=q))

    templates  = TicketTemplate.objects.filter(is_active=True).order_by('-use_count')[:12]
    categories = Category.objects.all()

    from helpdesk.models import KBArticle
    kb_articles = KBArticle.objects.filter(is_published=True).order_by('-views')[:6]

    # Default service categories when no templates exist
    default_services = [
        {'name':'Computer / Laptop',   'icon':'fas fa-laptop'},
        {'name':'Printer',             'icon':'fas fa-print'},
        {'name':'Internet / Network',  'icon':'fas fa-wifi'},
        {'name':'Email',               'icon':'fas fa-envelope'},
        {'name':'Password Reset',      'icon':'fas fa-lock'},
        {'name':'Phone / VoIP',        'icon':'fas fa-phone'},
        {'name':'Software',            'icon':'fas fa-bug'},
        {'name':'Hardware',            'icon':'fas fa-computer'},
    ]

    my_tickets_page = Paginator(shown, TICKETS_PER_PAGE).get_page(
        request.GET.get('page', 1))
    for t in my_tickets_page:
        t.stage = ticket_stage(t)

    return render(request, 'helpdesk/portal.html', {
        'my_tickets':       my_tickets_page,
        'filter':           flt,
        'q':                q,
        'stages':           TICKET_STAGES,
        'is_staff_user':    user.is_staff,
        'open_count':       open_count,
        'resolved_count':   resolved_count,
        'templates':        templates,
        'categories':       categories,
        'kb_articles':      kb_articles,
        'default_services': default_services,
        'total':            total,
    })


_PORTAL_FIELDS = ['title', 'description', 'category', 'requester_office', 'requester_department',
                  'requester_section', 'priority', 'template_id', 'source', 'requester_name',
                  'requester_phone', 'requester_email', 'on_behalf_of', 'assigned_to', 'voip_call']
_ORG_FIELDS = ('office', 'department', 'section')   # profile names; ticket uses requester_<name>
_STAFF_SOURCES = ('PHONE', 'WALK_IN', 'VOIP', 'EMAIL', 'PORTAL')


def _profile_org(user):
    """(office, department, section) recorded on a user's profile — blanks if none."""
    prof = getattr(user, 'profile', None) if user else None
    return {f: (getattr(prof, f, '') or '') for f in _ORG_FIELDS}


def _resolve_org(user, typed):
    """
    The organisation snapshot stored on a ticket. A value on the person's profile
    always wins (so people cannot fragment the reports by retyping their office);
    anything typed only fills the gaps.
    """
    prof = _profile_org(user)
    return {f: prof[f] or (typed.get(f) or '').strip()[:100] for f in _ORG_FIELDS}


def _org_choices():
    """Known offices / departments / sections, for pick-lists and type-ahead."""
    from accounts.models import UserProfile
    out = {}
    for f in _ORG_FIELDS:
        out[f] = list(UserProfile.objects.exclude(**{f: ''}).order_by(f)
                      .values_list(f, flat=True).distinct())
    return out


def _template_payload(templates):
    """JSON-safe description of templates for the picker's JavaScript."""
    return {str(t.pk): {
        'id': t.pk, 'name': t.name, 'description': t.description, 'icon': t.icon,
        'color': t.color, 'title': t.title_template, 'body': t.description_template,
        'category': t.category_id or '', 'priority': (t.priority or 'MEDIUM').upper(),
    } for t in templates}


def _portal_form_context(request, values, errors):
    templates = list(TicketTemplate.objects.filter(is_active=True).select_related('category')
                     .order_by('-use_count', 'name')[:60])
    mine = Ticket.objects.filter(requester=request.user).order_by('-created_at')
    ctx = {
        'templates':      templates,
        'tpl_json':       _template_payload(templates),
        'categories':     Category.objects.all(),
        'v':              values,
        'errors':         errors,
        'org_choices':    _org_choices(),
        'org_locked':     {f: bool(v) for f, v in _profile_org(request.user).items()},
        'my_recent':      list(mine.select_related('assigned_to')[:3]),
        'my_open_count':  mine.filter(status__in=['OPEN', 'IN_PROGRESS', 'PENDING', 'REOPENED']).count(),
    }
    if request.user.is_staff:
        ctx['staff_mode']     = True
        ctx['source_choices'] = [c for c in Ticket.SOURCE_CHOICES if c[0] in _STAFF_SOURCES]
        ctx['staff_users']    = [
            {'pk': u.pk, 'name': u.get_full_name() or u.username, **_profile_org(u)}
            for u in User.objects.filter(is_active=True).select_related('profile')
                         .order_by('first_name', 'username')]
        ctx['agents']         = User.objects.filter(is_staff=True, is_active=True).order_by('first_name', 'username')
    return ctx


@login_required
def portal_create(request):
    """
    The one place tickets are raised.

    Requesters see the simple form. IT staff see the same form plus a
    "logging for someone else" section (phone / walk-in / VoIP callers), where an
    existing user can be picked per office / department / section, and which is
    where VoIP call-log links land with the caller pre-filled.
    """
    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError
    from django.db import IntegrityError, transaction
    from django.db.models import F
    is_staff = request.user.is_staff

    if request.method != 'POST':
        values = {f: '' for f in _PORTAL_FIELDS}
        values.update(priority='MEDIUM', source='PHONE')
        if is_staff:
            # Pre-fill from a VoIP call-log link
            values['requester_phone'] = request.GET.get('caller', '').strip()[:30]
            values['requester_name']  = request.GET.get('caller_name', '').strip()[:150]
            values['voip_call']       = request.GET.get('voip_call', '').strip()
            src = request.GET.get('source', '').strip().upper()
            if src in _STAFF_SOURCES:
                values['source'] = src
            elif values['voip_call']:
                values['source'] = 'VOIP'
            if request.GET.get('on_behalf_of', '').isdigit():
                values['on_behalf_of'] = request.GET['on_behalf_of']
        else:
            for f, val in _profile_org(request.user).items():
                values[f'requester_{f}'] = val
        # ?template=<id> opens the form with that template already applied
        tid = request.GET.get('template', '')
        if tid.isdigit():
            tpl = TicketTemplate.objects.filter(pk=tid, is_active=True).first()
            if tpl:
                values.update(template_id=str(tpl.pk), title=tpl.title_template,
                              description=tpl.description_template,
                              category=str(tpl.category_id or ''),
                              priority=(tpl.priority or 'MEDIUM').upper())
        return render(request, 'helpdesk/portal_create.html', _portal_form_context(request, values, {}))

    values = {f: request.POST.get(f, '').strip() for f in _PORTAL_FIELDS}
    values['priority'] = (values['priority'] or 'MEDIUM').upper()
    errors = {}

    if not values['title']:
        errors['title'] = 'Please enter a title for your request.'
    elif len(values['title']) > 255:
        errors['title'] = 'The title is too long — keep it under 255 characters.'
    for f in ('requester_office', 'requester_department', 'requester_section'):
        if len(values[f]) > 100:
            errors[f] = 'Too long — maximum 100 characters.'

    category = None
    if values['category']:
        category = Category.objects.filter(pk=values['category']).first() \
            if values['category'].isdigit() else None
        if not category:
            errors['category'] = 'Please choose a category from the list.'

    allowed_pri = ['LOW', 'MEDIUM', 'HIGH'] + (['CRITICAL'] if is_staff else [])
    if values['priority'] not in allowed_pri:
        errors['priority'] = 'Choose an urgency level.'

    on_behalf = assignee = voip_call = None
    source = 'PORTAL'
    if is_staff:
        if values['source'] not in _STAFF_SOURCES:
            errors['source'] = 'Choose how this request came in.'
        else:
            source = values['source']

        if values['on_behalf_of']:
            on_behalf = User.objects.filter(pk=values['on_behalf_of'], is_active=True).first() \
                if values['on_behalf_of'].isdigit() else None
            if not on_behalf:
                errors['on_behalf_of'] = 'Choose a user from the list.'
        if not on_behalf and not values['requester_name']:
            errors['requester_name'] = 'Who is this request for? Enter their name or pick a user.'
        if len(values['requester_name']) > 150:
            errors['requester_name'] = 'Too long — maximum 150 characters.'
        if values['requester_phone'] and not re.fullmatch(r'[0-9+()\-\s.]{3,30}', values['requester_phone']):
            errors['requester_phone'] = 'Enter a valid phone number.'
        if values['requester_email']:
            try:
                validate_email(values['requester_email'])
            except ValidationError:
                errors['requester_email'] = 'Enter a valid email address.'

        if values['voip_call']:
            from voip.models import VoIPCall
            voip_call = VoIPCall.objects.filter(pk=values['voip_call']).first() \
                if values['voip_call'].isdigit() else None
            if not voip_call:
                errors['voip_call'] = 'That call record was not found.'
        # Only administrators may set the assignee directly on create
        if request.user.is_superuser and values['assigned_to']:
            assignee = User.objects.filter(pk=values['assigned_to'], is_staff=True, is_active=True).first() \
                if values['assigned_to'].isdigit() else None
            if not assignee:
                errors['assigned_to'] = 'Choose a staff member from the list.'

    if errors:
        messages.error(request, 'Request not submitted — please correct the highlighted field(s). Everything else has been kept.')
        return render(request, 'helpdesk/portal_create.html', _portal_form_context(request, values, errors))

    # ── Build the ticket ──────────────────────────────────────────────────────
    typed_org = {f: values[f'requester_{f}'] for f in _ORG_FIELDS}
    if is_staff:
        # A picked user is the requester; a caller who is not a system user leaves
        # `requester` empty so reports are not credited to the staff member typing.
        requester       = on_behalf
        requester_name  = values['requester_name'] or (on_behalf.get_full_name() or on_behalf.username)
        requester_email = values['requester_email'] or (on_behalf.email if on_behalf else '')
        requester_phone = values['requester_phone']
        logged_by       = request.user
    else:
        requester       = request.user
        requester_name  = request.user.get_full_name() or request.user.username
        requester_email = request.user.email
        requester_phone = ''
        logged_by       = None
    org = _resolve_org(requester, typed_org)

    description = values['description'] or '(No further details given)'
    if voip_call and not values['description']:
        description = (f'Logged from VoIP call.\nCaller: {voip_call.caller_number} '
                       f'({voip_call.caller_name or "unknown"})\nDuration: {voip_call.talk_duration_display}')

    ticket = None
    for _attempt in range(3):          # ticket numbers are max(pk)+1, so retry on a rare race
        try:
            with transaction.atomic(using=Ticket.objects.db):
                ticket = Ticket.objects.create(
                    title=values['title'], description=description,
                    priority=values['priority'], category=category,
                    requester=requester, logged_by=logged_by, requester_name=requester_name,
                    requester_email=requester_email, requester_phone=requester_phone,
                    requester_office=org['office'], requester_department=org['department'],
                    requester_section=org['section'],
                    status='OPEN', source=source,
                    assigned_to=assignee, voip_call=voip_call,
                )
            break
        except IntegrityError:
            continue
    if ticket is None:
        messages.error(request, 'Could not generate a ticket number — nothing was lost, please press Submit again.')
        return render(request, 'helpdesk/portal_create.html', _portal_form_context(request, values, {}))

    if voip_call:
        voip_call.ticket_created = True
        voip_call.save(update_fields=['ticket_created'])
    if values['template_id'].isdigit():
        TicketTemplate.objects.filter(pk=values['template_id']).update(use_count=F('use_count') + 1)

    audit_note = (f'Logged by IT staff via {source}' if is_staff else 'Created via Requester Portal')
    if voip_call:
        audit_note += f' (VoIP call {voip_call.pk})'
    log_action(request, AuditLog.ACTION_CREATE, 'Ticket', ticket.ticket_id, ticket.title,
               notes=audit_note)
    try:
        from helpdesk.notifications import notify_ticket_created
        notify_ticket_created(ticket, request.user)
    except Exception:
        pass

    if is_staff:
        messages.success(request, f'Ticket {ticket.ticket_id} created.')
        return redirect('ticket_detail', ticket_id=ticket.ticket_id)
    messages.success(request, f'Your request [{ticket.ticket_id}] has been submitted. '
                              f'The IT team will respond shortly.')
    return redirect('portal')


@login_required
def portal_ticket(request, ticket_id):
    """Requester view of their own ticket — simplified read-only."""
    if request.user.is_staff:
        return redirect('ticket_detail', ticket_id=ticket_id)

    ticket = get_object_or_404(Ticket, ticket_id=ticket_id, requester=request.user)
    comments = ticket.comments.order_by('created_at')

    if request.method == 'POST':
        # Allow requester to add a comment
        body = request.POST.get('comment','').strip()
        if body:
            from helpdesk.models import TicketComment
            comment = TicketComment.objects.create(
                ticket=ticket, author=request.user,
                body=body, is_internal=False)
            log_action(request, AuditLog.ACTION_COMMENT, 'Ticket',
                       ticket.ticket_id, ticket.title, notes=body[:100])
            try:   # tell IT — previously a reply from the portal alerted nobody
                from helpdesk.notifications import notify_new_comment
                notify_new_comment(ticket, comment)
            except Exception:
                pass
            messages.success(request, 'Comment added.')
            return redirect('portal_ticket', ticket_id=ticket_id)

    from helpdesk.notifications import mark_conversation_read
    mark_conversation_read(request.user, ticket.ticket_id)
    return render(request, 'helpdesk/portal_ticket.html', {
        'ticket': ticket, 'comments': comments,
        'stage': ticket_stage(ticket), 'stages': TICKET_STAGES,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# TAGS
# ═══════════════════════════════════════════════════════════════════════════════

@login_required
def tag_list(request):
    if not request.user.is_staff:
        messages.error(request, 'IT Staff access required.')
        return redirect('dashboard')
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create':
            name  = request.POST.get('name','').strip().lower().replace(' ','-')
            color = request.POST.get('color','#1a6b30').strip()
            desc  = request.POST.get('description','').strip()
            if name:
                _, created = Tag.objects.get_or_create(
                    name=name, defaults={'color':color,'description':desc})
                if created:
                    messages.success(request, f'Tag #{name} created.')
                else:
                    messages.warning(request, f'Tag #{name} already exists.')
            else:
                messages.error(request, 'Tag name is required.')
        elif action == 'delete' and request.user.is_superuser:
            tag = get_object_or_404(Tag, pk=request.POST.get('tag_id'))
            name = tag.name
            tag.delete()
            messages.success(request, f'Tag #{name} deleted.')
        elif action == 'edit':
            tag = get_object_or_404(Tag, pk=request.POST.get('tag_id'))
            tag.color = request.POST.get('color', tag.color)
            tag.description = request.POST.get('description', tag.description)
            tag.save(update_fields=['color','description'])
            messages.success(request, f'Tag #{tag.name} updated.')
        return redirect('tag_list')

    from django.db.models import Count
    try:
        tags = Tag.objects.annotate(
            ticket_count=Count('ticket', distinct=True)
        ).order_by('-ticket_count','-use_count','name')
        total = tags.count()
    except Exception:
        # Migration not yet applied — graceful fallback
        tags = Tag.objects.all()
        for t in tags:
            t.ticket_count = 0
            t.use_count = getattr(t, 'use_count', 0)
        total = tags.count()
        messages.warning(request,
            'Run "py manage.py migrate" to enable full tag features.')
    return render(request, 'helpdesk/tag_list.html', {
        'tags': tags, 'total': total,
    })


@login_required
def tag_tickets(request, tag_name):
    """View all tickets with a specific tag."""
    tag = get_object_or_404(Tag, name=tag_name)
    tickets = tag.ticket_set.select_related(
        'requester','assigned_to','category'
    ).order_by('-created_at')
    return render(request, 'helpdesk/tag_tickets.html', {
        'tag': tag, 'tickets': tickets, 'total': tickets.count(),
    })


def api_tags(request):
    from django.http import JsonResponse
    tags = list(Tag.objects.values('id','name','color','description').order_by('name'))
    return JsonResponse({'tags': tags})


@login_required
def api_ticket_tags(request, ticket_id):
    from django.http import JsonResponse
    from django.db.models import F
    if not request.user.is_staff:
        return JsonResponse({'error':'forbidden'}, status=403)
    ticket = get_object_or_404(Ticket, ticket_id=ticket_id)
    if request.method == 'POST':
        import json as _json
        data = _json.loads(request.body)
        tag  = get_object_or_404(Tag, pk=data.get('tag_id'))
        if data.get('remove'):
            ticket.tags.remove(tag)
            log_action(request, AuditLog.ACTION_UPDATE, 'Ticket',
                       ticket.ticket_id, ticket.title, notes=f'Removed tag #{tag.name}')
        else:
            ticket.tags.add(tag)
            Tag.objects.filter(pk=tag.pk).update(use_count=F('use_count')+1)
            log_action(request, AuditLog.ACTION_UPDATE, 'Ticket',
                       ticket.ticket_id, ticket.title, notes=f'Added tag #{tag.name}')
    return JsonResponse({'tags': list(ticket.tags.values('id','name','color'))})
