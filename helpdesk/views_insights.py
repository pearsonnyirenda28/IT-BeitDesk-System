"""
Admin / staff pages added alongside the core ticket views:

  organisation      (admin)  set each user's office / department / section
  analytics_staff   (admin)  IT staff performance
  analytics_demand  (staff)  who / which office, department, section raises more or fewer requests
  new_requests      (staff)  queue of new open requests; admins assign, staff may take
"""
import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core import signing
from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, Value, When
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from . import insights, orgdata
from .db_router import get_current_db_alias
from .models import AuditLog, Ticket
from .views import _org_choices, log_action

DEMAND_PAGE_SIZE = 20
QUEUE_PAGE_SIZE = 10


def _clean(value):
    return ' '.join((value or '').split())


def _csv(filename, header, rows):
    resp = HttpResponse(content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    resp.write('\ufeff')                       # so Excel opens UTF-8 correctly
    w = csv.writer(resp)
    w.writerow(header)
    for r in rows:
        # neutralise spreadsheet formula injection from user-typed values
        w.writerow([("'" + c) if isinstance(c, str) and c[:1] in ('=', '+', '-', '@') else c for c in r])
    return resp


# ── Organisation (admin) ──────────────────────────────────────────────────────

@login_required
def organisation(request):
    """One table to record each user's office, department and section."""
    if not request.user.is_superuser:
        messages.error(request, 'Administrator access required.')
        return redirect('dashboard')

    users = list(User.objects.filter(is_active=True).select_related('profile')
                 .order_by('first_name', 'last_name', 'username'))
    errors, values = {}, {}

    if request.method == 'POST':
        from accounts.models import UserProfile
        changes = []
        for u in users:
            new = {f: _clean(request.POST.get(f'{f}_{u.pk}', '')) for f in ('office', 'department', 'section')}
            values[u.pk] = new
            for f, val in new.items():
                if len(val) > 100:
                    errors[u.pk] = f'{f.title()} is too long — maximum 100 characters.'
            prof = getattr(u, 'profile', None)
            if u.pk not in errors and any(new[f] != getattr(prof, f, '') for f in new):
                changes.append((u, new))
        if not errors:
            for u, new in changes:
                prof, _ = UserProfile.objects.get_or_create(user=u)
                for f, val in new.items():
                    setattr(prof, f, val)
                prof.save(update_fields=['office', 'department', 'section'])
            if changes:
                log_action(request, AuditLog.ACTION_UPDATE, 'UserProfile', '', 'Organisation',
                           changes={'users_updated': [c[0].username for c in changes]},
                           notes=f'Organisation details updated for {len(changes)} user(s)')
            messages.success(request, f'{len(changes)} user(s) updated.' if changes else 'Nothing to change.')
            return redirect('organisation')
        messages.error(request, 'Nothing was saved — fix the highlighted row(s). Everything you typed has been kept.')

    rows = []
    for u in users:
        prof = getattr(u, 'profile', None)
        v = values.get(u.pk) or {f: getattr(prof, f, '') for f in ('office', 'department', 'section')}
        rows.append({'user': u, 'name': u.get_full_name() or u.username, 'v': v, 'error': errors.get(u.pk)})
    missing = sum(1 for r in rows if not any(r['v'].values()))
    try:
        backfill = orgdata.backfill_ticket_org(get_current_db_alias(), apply=False)
    except Exception:
        backfill = None
    return render(request, 'helpdesk/organisation.html', {
        'rows': rows, 'org_choices': _org_choices(), 'missing': missing, 'total': len(rows),
        'backfill': backfill,
    })


@login_required
def organisation_export(request):
    """Download everyone with their current office / department / section (a file to fill in)."""
    if not request.user.is_superuser:
        messages.error(request, 'Administrator access required.')
        return redirect('dashboard')
    rows = orgdata.export_rows()
    return _csv('beitdesk_organisation.csv', rows[0], rows[1:])


_IMPORT_SALT = 'org-import'


@login_required
def organisation_import(request):
    """
    Import offices / departments / sections from a CSV or Excel file.
    Step 1: upload -> a preview of exactly what would change.  Step 2: confirm -> apply.
    """
    if not request.user.is_superuser:
        messages.error(request, 'Administrator access required.')
        return redirect('dashboard')
    ctx = {'max_mb': orgdata.MAX_BYTES // (1024 * 1024), 'max_rows': orgdata.MAX_ROWS}
    if request.method != 'POST':
        return render(request, 'helpdesk/organisation_import.html', ctx)

    # ── Step 2: confirm and apply ────────────────────────────────────────────
    if request.POST.get('payload'):
        try:
            data = signing.loads(request.POST['payload'], salt=_IMPORT_SALT, max_age=1800)
        except signing.BadSignature:
            messages.error(request, 'That preview has expired. Please upload the file again.')
            return redirect('organisation_import')
        plan = orgdata.plan_import(data['rows'], data['columns'], data['clear'])   # re-checked against today's data
        updated = orgdata.apply_import(plan)
        extra = ''
        if request.POST.get('also_backfill'):
            r = orgdata.backfill_ticket_org(get_current_db_alias(), apply=True)
            extra = f' {r["tickets"]} older ticket(s) were also filled in.'
        if updated:
            log_action(request, AuditLog.ACTION_UPDATE, 'UserProfile', '', 'Organisation import',
                       changes={'users_updated': [u['user'].username for u in plan['updates']][:200]},
                       notes=f'Organisation imported from file: {updated} user(s) updated')
        messages.success(request, f'Import complete — {updated} user(s) updated, {plan["unchanged"]} already up to date.' + extra)
        return redirect('organisation')

    # ── Step 1: read the upload and show a preview ───────────────────────────
    upload = request.FILES.get('file')
    if not upload:
        ctx['error'] = 'Choose a CSV or Excel file first.'
        return render(request, 'helpdesk/organisation_import.html', ctx)
    clear = bool(request.POST.get('clear_blanks'))
    try:
        rows, columns, warnings = orgdata.parse_upload(upload.name, upload.read(orgdata.MAX_BYTES + 1))
    except orgdata.ImportProblem as e:
        ctx['error'] = str(e)
        return render(request, 'helpdesk/organisation_import.html', {**ctx, 'clear': clear})
    plan = orgdata.plan_import(rows, columns, clear)
    shown = plan['updates'][:100]
    ctx.update({
        'preview': True, 'plan': plan, 'shown': shown, 'more': max(0, len(plan['updates']) - len(shown)),
        'columns': columns, 'warnings': warnings, 'file_name': upload.name, 'clear': clear,
        'payload': signing.dumps({'rows': rows, 'columns': columns, 'clear': clear}, salt=_IMPORT_SALT, compress=True),
        # what the optional backfill would do AFTER this import (profiles are empty before it, so the
        # current state would always say 0)
        'backfill': orgdata.estimate_backfill_after_import(plan, get_current_db_alias()),
    })
    return render(request, 'helpdesk/organisation_import.html', ctx)


@login_required
def organisation_backfill(request):
    """Fill the organisation on older tickets from each requester's profile (blanks only)."""
    if request.method != 'POST':
        return redirect('organisation')
    if not request.user.is_superuser:
        messages.error(request, 'Administrator access required.')
        return redirect('dashboard')
    r = orgdata.backfill_ticket_org(get_current_db_alias(), apply=True)
    if r['tickets']:
        log_action(request, AuditLog.ACTION_UPDATE, 'Ticket', '', 'Organisation backfill',
                   changes=r, notes=f'Organisation filled in on {r["tickets"]} older ticket(s)')
    messages.success(request, f'Updated {r["tickets"]} older ticket(s) for {r["users"]} requester(s).'
                     if r['tickets'] else 'Nothing to update — no older ticket was missing details that a profile can supply.')
    return redirect('organisation')


# ── IT staff performance (admin) ──────────────────────────────────────────────

@login_required
def analytics_staff(request):
    if not request.user.is_superuser:
        messages.error(request, 'Administrator access required.')
        return redirect('dashboard')
    period = insights.parse_period(request.GET)
    data = insights.staff_performance(period)

    sort = request.GET.get('sort', 'resolved')
    keys = {'name': lambda r: r['name'].lower(), 'total': lambda r: r['total'],
            'resolved': lambda r: r['resolved'], 'rate': lambda r: r['resolution_rate'] if r['resolution_rate'] is not None else -1,
            'sla': lambda r: r['sla_rate'] if r['sla_rate'] is not None else -1,
            'avg': lambda r: r['avg_min'] if r['avg_min'] is not None else 10 ** 9,
            'open': lambda r: r['open_now'], 'comments': lambda r: r['comments']}
    if sort not in keys:
        sort = 'resolved'
    default_desc = sort not in ('name', 'avg')
    desc = (request.GET.get('dir') == 'desc') if request.GET.get('dir') else default_desc
    data['rows'].sort(key=keys[sort], reverse=desc)

    if request.GET.get('export') == 'csv':
        return _csv('it_staff_performance.csv',
                    ['Staff', 'Tickets assigned', 'Resolved', 'Resolution rate %', 'SLA compliance %',
                     'Avg resolution', 'Open now', 'High/Critical', 'Comments', 'E-sign decisions'],
                    [[r['name'], r['total'], r['resolved'], r['resolution_rate'] if r['resolution_rate'] is not None else '',
                      r['sla_rate'] if r['sla_rate'] is not None else '', r['avg_label'], r['open_now'],
                      r['urgent'], r['comments'], r['signoffs']] for r in data['rows']])

    return render(request, 'helpdesk/analytics_staff.html', {
        **data, 'period': period, 'period_choices': insights.PERIOD_CHOICES,
        'sort': sort, 'dir': 'desc' if desc else 'asc',
        'max_total': max([r['total'] for r in data['rows']] or [1]) or 1,
    })


# ── Request demand (staff) ────────────────────────────────────────────────────

@login_required
def analytics_demand(request):
    if not request.user.is_staff:
        messages.error(request, 'IT staff access required.')
        return redirect('dashboard')
    by = request.GET.get('by', 'office')
    if by not in ('user', 'office', 'department', 'section'):
        by = 'office'
    period = insights.parse_period(request.GET)
    data = insights.demand(by, period, request.GET.get('sort', 'count'), request.GET.get('dir', 'desc'))

    if request.GET.get('export') == 'csv':
        return _csv(f'request_demand_by_{by}.csv',
                    [by.title(), 'Requests', 'Share %', 'Open', 'Resolved', 'High/Critical',
                     'Avg resolution', 'People', 'Requests per person', 'Last request'],
                    [[r['label'], r['count'], r['share'], r['open'], r['resolved'], r['urgent'], r['avg_label'],
                      r['headcount'] if r['headcount'] is not None else '',
                      r['per_head'] if r['per_head'] is not None else '',
                      r['last'].strftime('%Y-%m-%d %H:%M') if r['last'] else ''] for r in data['rows']])

    # Each row links to its tickets, over the same time window the report used — so the counts match
    from urllib.parse import urlencode
    window = {}
    if period['start']:
        window['since'] = period['start'].isoformat()
    if period['end']:
        window['until'] = period['end'].isoformat()
    for r in data['rows']:
        if not r['count']:
            r['drill'] = ''
            continue
        if by == 'user':
            query = {'requester': r['user_id'], **window}
        else:
            query = {by: insights.NONE_TOKEN if r['label'] == insights.NOT_SET else r['label'], **window}
        r['drill'] = reverse('ticket_list') + '?' + urlencode(query)

    fillable = None
    if request.user.is_superuser and by != 'user' and data['summary']['not_set']:
        try:
            fillable = orgdata.backfill_ticket_org(get_current_db_alias(), apply=False)['tickets']
        except Exception:
            fillable = None
    page = Paginator(data['rows'], DEMAND_PAGE_SIZE).get_page(request.GET.get('page', 1))
    return render(request, 'helpdesk/analytics_demand.html', {
        **data, 'rows': page, 'fillable_tickets': fillable, 'period': period, 'period_choices': insights.PERIOD_CHOICES,
        'max_count': max([r['count'] for r in data['rows']] or [1]) or 1,
        'by_choices': [('office', 'Office'), ('department', 'Department'), ('section', 'Section'), ('user', 'User')],
        'by_label': dict([('office', 'Office'), ('department', 'Department'), ('section', 'Section'), ('user', 'User')])[by],
    })


# ── New requests queue ────────────────────────────────────────────────────────

_PRIORITY_RANK = Case(When(priority='CRITICAL', then=Value(0)), When(priority='HIGH', then=Value(1)),
                      When(priority='MEDIUM', then=Value(2)), default=Value(3), output_field=IntegerField())


def _queue_tabs(user):
    new = Ticket.objects.filter(status='OPEN')
    return {'new': new, 'unassigned': new.filter(assigned_to=None), 'mine': new.filter(assigned_to=user)}


@login_required
def new_requests(request):
    """
    Everything that has come in and is still Open, most urgent then oldest first.
    Administrators assign from here; other IT staff can take an unassigned request.
    """
    if not request.user.is_staff:
        messages.error(request, 'IT staff access required.')
        return redirect('dashboard')
    tabs = _queue_tabs(request.user)
    view = request.GET.get('view', 'new')
    if view not in tabs:
        view = 'new'
    qs = tabs[view].select_related('requester', 'assigned_to', 'category') \
                   .annotate(_rank=_PRIORITY_RANK).order_by('_rank', 'created_at')
    page = Paginator(qs, QUEUE_PAGE_SIZE).get_page(request.GET.get('page', 1))
    return render(request, 'helpdesk/new_requests.html', {
        'tickets': page, 'view': view,
        'counts': {k: v.count() for k, v in tabs.items()},
        'agents': User.objects.filter(is_staff=True, is_active=True).order_by('first_name', 'username'),
    })


def _back_to_queue(request):
    url = reverse('new_requests')
    keep = {k: request.POST[k] for k in ('view', 'page') if request.POST.get(k, '').strip()}
    if keep:
        from urllib.parse import urlencode
        url += '?' + urlencode(keep)
    return redirect(url)


@login_required
def queue_assign(request, ticket_id):
    """Administrators assign (or reassign / unassign) a request from the queue."""
    if request.method != 'POST':
        return redirect('new_requests')
    if not request.user.is_superuser:
        messages.error(request, 'Only administrators can assign tickets.')
        return _back_to_queue(request)
    ticket = get_object_or_404(Ticket, ticket_id=ticket_id)
    agent_id = request.POST.get('agent_id', '')
    agent = None
    if agent_id:
        agent = User.objects.filter(pk=agent_id, is_staff=True, is_active=True).first() if agent_id.isdigit() else None
        if not agent:
            messages.error(request, 'Choose an IT staff member from the list.')
            return _back_to_queue(request)
    old = str(ticket.assigned_to) if ticket.assigned_to else 'Unassigned'
    ticket.assigned_to = agent
    ticket.save()
    log_action(request, AuditLog.ACTION_ASSIGN, 'Ticket', ticket.ticket_id, str(ticket),
               changes={'assigned_to': {'from': old, 'to': str(agent or 'Unassigned')}},
               notes='Assigned from the New Requests queue')
    if agent:
        try:
            from helpdesk.notifications import notify_ticket_assigned
            notify_ticket_assigned(ticket, assigned_by=request.user)
        except Exception:
            pass
    messages.success(request, f'{ticket.ticket_id} assigned to {agent.get_full_name() or agent.username}.'
                     if agent else f'{ticket.ticket_id} is now unassigned.')
    return _back_to_queue(request)


@login_required
def queue_take(request, ticket_id):
    """Any IT staff member can take an unassigned request (same rule as on the ticket page)."""
    if request.method != 'POST':
        return redirect('new_requests')
    if not request.user.is_staff:
        messages.error(request, 'Only IT staff can take tickets.')
        return redirect('dashboard')
    ticket = get_object_or_404(Ticket, ticket_id=ticket_id)
    if ticket.assigned_to_id:
        messages.error(request, f'{ticket.ticket_id} is already assigned.')
        return _back_to_queue(request)
    ticket.assigned_to = request.user
    if ticket.status == Ticket.STATUS_OPEN:
        ticket.status = Ticket.STATUS_IN_PROGRESS
    ticket.save()
    log_action(request, AuditLog.ACTION_ASSIGN, 'Ticket', ticket.ticket_id, str(ticket),
               changes={'assigned_to': {'from': 'Unassigned', 'to': str(request.user)}},
               notes='Self-assigned from the New Requests queue')
    messages.success(request, f'You have taken {ticket.ticket_id}.')
    return _back_to_queue(request)
