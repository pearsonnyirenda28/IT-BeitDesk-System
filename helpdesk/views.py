from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.db.models import Count, Q, Avg
from django.utils import timezone
from django.http import JsonResponse
from django.core.paginator import Paginator
from datetime import timedelta
from .models import Ticket, TicketComment, Category, AuditLog, KnowledgeBase, GlossaryTerm, DatabaseYear, LearnedPhrase
from .forms import TicketForm, TicketCommentForm, TicketFilterForm


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

    # ── Active year info ────────────────────────────────────────────────────
    active_year    = request.session.get('active_db_year')
    active_db_name = request.session.get('active_db_name', '')
    viewing_label  = f'{active_year} Records' if active_year else 'Current Year'

    # When viewing a past year, today/week stats don't apply — zero them
    is_past_year = bool(active_year)

    try:
        tickets = Ticket.objects.all()

        stats = {
            'total_open':           tickets.filter(status__in=['OPEN', 'IN_PROGRESS', 'REOPENED']).count(),
            'total_pending':        tickets.filter(status='PENDING').count(),
            'total_resolved_today': 0 if is_past_year else tickets.filter(
                                        status='RESOLVED', resolved_at__gte=today_start).count(),
            'total_closed':         tickets.filter(status='CLOSED').count(),
            'critical_open':        tickets.filter(status__in=['OPEN', 'IN_PROGRESS'], priority='CRITICAL').count(),
            'overdue_count':        0 if is_past_year else sum(
                                        1 for t in tickets.filter(
                                            status__in=['OPEN', 'IN_PROGRESS', 'PENDING'],
                                            due_date__isnull=False) if t.is_overdue),
            'avg_resolution_minutes': tickets.filter(
                                        resolution_time_minutes__isnull=False
                                      ).aggregate(avg=Avg('resolution_time_minutes'))['avg'] or 0,
            'resolved_this_week':   0 if is_past_year else tickets.filter(resolved_at__gte=week_start).count(),
            'new_this_week':        0 if is_past_year else tickets.filter(created_at__gte=week_start).count(),
            'total_tickets':        tickets.count(),
        }

        recent_tickets   = tickets.select_related('requester', 'assigned_to', 'category').order_by('-created_at')[:10]
        status_data      = list(tickets.values('status').annotate(count=Count('id')).order_by('status'))
        category_data    = list(tickets.filter(category__isnull=False).values('category__name').annotate(count=Count('id')).order_by('-count')[:6])
        priority_data    = list(tickets.values('priority').annotate(count=Count('id')).order_by('priority'))
        agent_workload   = list(
            tickets.filter(status__in=['OPEN', 'IN_PROGRESS'], assigned_to__isnull=False)
            .values('assigned_to__username', 'assigned_to__first_name', 'assigned_to__last_name')
            .annotate(count=Count('id')).order_by('-count')[:5]
        )

    except Exception:
        # DB for this year not yet set up — show empty state
        stats          = {k: 0 for k in ['total_open','total_pending','total_resolved_today',
                                          'total_closed','critical_open','overdue_count',
                                          'avg_resolution_minutes','resolved_this_week',
                                          'new_this_week','total_tickets']}
        recent_tickets = []
        status_data    = []
        category_data  = []
        priority_data  = []
        agent_workload = []

    # VoIP stats — only meaningful for current year
    if is_past_year:
        voip_stats  = {'active_calls': 0, 'calls_today': 0, 'missed_today': 0, 'avg_talk_today': 0}
        active_calls = []
    else:
        voip_stats, active_calls = get_voip_stats(today_start)

    # Audit — scoped to role as before
    try:
        if request.user.is_staff:
            recent_audit = AuditLog.objects.select_related('user').order_by('-timestamp')[:10]
        else:
            recent_audit = AuditLog.objects.select_related('user').filter(
                user=request.user).order_by('-timestamp')[:10]
    except Exception:
        recent_audit = []

    context = {
        'stats':          stats,
        'voip_stats':     voip_stats,
        'active_calls':   active_calls,
        'recent_tickets': recent_tickets,
        'status_data':    status_data,
        'category_data':  category_data,
        'priority_data':  priority_data,
        'agent_workload': agent_workload,
        'recent_audit':   recent_audit,
        # Year context for the banner
        'active_year':    active_year,
        'active_db_name': active_db_name,
        'viewing_label':  viewing_label,
        'is_past_year':   is_past_year,
    }
    return render(request, 'helpdesk/dashboard.html', context)


@login_required
def ticket_list(request):
    active_year    = request.session.get('active_db_year')
    active_db_name = request.session.get('active_db_name', '')
    is_past_year   = bool(active_year)
    db_error       = False

    try:
        # Staff and admins see all tickets; regular users see only theirs
        if request.user.is_staff:
            tickets = Ticket.objects.select_related('requester', 'assigned_to', 'category').all()
        else:
            tickets = Ticket.objects.select_related('requester', 'assigned_to', 'category').filter(
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

        total_count  = tickets.count()
        paginator    = Paginator(tickets, 20)
        tickets_page = paginator.get_page(request.GET.get('page', 1))
        categories   = Category.objects.all()

    except Exception:
        db_error     = True
        tickets_page = []
        categories   = []
        total_count  = 0
        status = priority = category = assigned = search = source = ''
        date_from = date_to = date_preset = ''

    context = {
        'tickets':      tickets_page,
        'categories':   categories,
        'filters': {
            'status': status, 'priority': priority,
            'category': category, 'assigned': assigned,
            'search': search, 'source': source,
            'date_from': date_from, 'date_to': date_to,
            'date_preset': date_preset,
        },
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

    audit_trail = AuditLog.objects.filter(
        model_name='Ticket', object_id=ticket.ticket_id
    ).order_by('-timestamp')
    agents = User.objects.filter(is_staff=True, is_active=True)

    context = {
        'ticket': ticket,
        'comment_form': comment_form,
        'audit_trail': audit_trail,
        'agents': agents,
        'status_choices': Ticket.STATUS_CHOICES,
    }
    return render(request, 'helpdesk/ticket_detail.html', context)


@login_required
def ticket_create(request):
    if request.method == 'POST':
        form = TicketForm(request.POST, request.FILES)
        if form.is_valid():
            ticket = form.save(commit=False)
            if not ticket.requester_id:
                ticket.requester = request.user
            # Only administrators may set the assignee directly on create
            if not request.user.is_superuser:
                ticket.assigned_to = None
            ticket.save()
            form.save_m2m()
            log_action(request, AuditLog.ACTION_CREATE, 'Ticket',
                       ticket.ticket_id, str(ticket), notes='New ticket created')
            messages.success(request, f'Ticket {ticket.ticket_id} created.')
            return redirect('ticket_detail', ticket_id=ticket.ticket_id)
    else:
        # Pre-fill from VoIP call redirect if applicable
        initial = {}
        caller = request.GET.get('caller', '')
        caller_name = request.GET.get('caller_name', '')
        source = request.GET.get('source', '')
        if caller:
            initial['requester_phone'] = caller
        if caller_name:
            initial['requester_name'] = caller_name
        if source:
            initial['source'] = source
        form = TicketForm(initial=initial)
        if not request.user.is_superuser:
            form.fields.pop('assigned_to', None)

    return render(request, 'helpdesk/ticket_form.html',
                  {'form': form, 'page_title': 'Create New Ticket'})


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


@login_required
def print_tickets(request):
    """Print all tickets (filtered) — admin only, opens print-ready page."""
    if not request.user.is_superuser:
        messages.error(request, 'Only administrators can print ticket records.')
        return redirect('ticket_list')

    tickets = Ticket.objects.select_related(
        'requester', 'assigned_to', 'category'
    ).all().order_by('status', '-created_at')

    # Optional filters from querystring
    status   = request.GET.get('status', '')
    priority = request.GET.get('priority', '')
    if status:
        tickets = tickets.filter(status=status)
    if priority:
        tickets = tickets.filter(priority=priority)

    log_action(request, AuditLog.ACTION_VIEW, 'Ticket', '',
               'Bulk print', notes=f'Printed {tickets.count()} tickets')

    context = {
        'tickets': tickets,
        'printed_by': request.user,
        'printed_at': timezone.now(),
        'filters': {'status': status, 'priority': priority},
        'status_choices': Ticket.STATUS_CHOICES,
        'priority_choices': Ticket.PRIORITY_CHOICES,
        'total': tickets.count(),
    }
    return render(request, 'helpdesk/print_tickets.html', context)


@login_required
def print_ticket_single(request, ticket_id):
    """Print a single ticket — admin only."""
    if not request.user.is_superuser:
        messages.error(request, 'Only administrators can print ticket records.')
        return redirect('ticket_detail', ticket_id=ticket_id)

    ticket = get_object_or_404(Ticket, ticket_id=ticket_id)
    log_action(request, AuditLog.ACTION_VIEW, 'Ticket', ticket.ticket_id,
               str(ticket), notes='Single ticket print')

    return render(request, 'helpdesk/print_ticket_single.html', {
        'ticket': ticket,
        'printed_by': request.user,
        'printed_at': timezone.now(),
        'comments': ticket.comments.all(),
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
        'suggested_db':   f'beitdesk_{current_year}',
        'db_user':        os.environ.get('DB_USER', 'beitdesk_user'),
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
