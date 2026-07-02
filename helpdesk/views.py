from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.db.models import Count, Q, Avg
from django.utils import timezone
from django.http import JsonResponse
from django.core.paginator import Paginator
from datetime import timedelta
from .models import Ticket, TicketComment, Category, AuditLog, KnowledgeBase
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
    week_start = today_start - timedelta(days=7)

    tickets = Ticket.objects.all()

    stats = {
        'total_open': tickets.filter(status__in=['OPEN', 'IN_PROGRESS', 'REOPENED']).count(),
        'total_pending': tickets.filter(status='PENDING').count(),
        'total_resolved_today': tickets.filter(status='RESOLVED', resolved_at__gte=today_start).count(),
        'total_closed': tickets.filter(status='CLOSED').count(),
        'critical_open': tickets.filter(status__in=['OPEN', 'IN_PROGRESS'], priority='CRITICAL').count(),
        'overdue_count': sum(1 for t in tickets.filter(
            status__in=['OPEN', 'IN_PROGRESS', 'PENDING'],
            due_date__isnull=False) if t.is_overdue),
        'avg_resolution_minutes': tickets.filter(
            resolution_time_minutes__isnull=False
        ).aggregate(avg=Avg('resolution_time_minutes'))['avg'] or 0,
        'resolved_this_week': tickets.filter(resolved_at__gte=week_start).count(),
        'new_this_week': tickets.filter(created_at__gte=week_start).count(),
    }

    voip_stats, active_calls = get_voip_stats(today_start)

    recent_tickets = tickets.select_related(
        'requester', 'assigned_to', 'category'
    ).order_by('-created_at')[:10]

    status_data = list(tickets.values('status').annotate(count=Count('id')).order_by('status'))
    category_data = list(
        tickets.filter(category__isnull=False)
        .values('category__name').annotate(count=Count('id')).order_by('-count')[:6]
    )
    priority_data = list(tickets.values('priority').annotate(count=Count('id')).order_by('priority'))
    agent_workload = list(
        tickets.filter(status__in=['OPEN', 'IN_PROGRESS'], assigned_to__isnull=False)
        .values('assigned_to__username', 'assigned_to__first_name', 'assigned_to__last_name')
        .annotate(count=Count('id')).order_by('-count')[:5]
    )
    if request.user.is_staff:
        recent_audit = AuditLog.objects.select_related('user').order_by('-timestamp')[:10]
    else:
        recent_audit = AuditLog.objects.select_related('user').filter(
            user=request.user
        ).order_by('-timestamp')[:10]

    context = {
        'stats': stats,
        'voip_stats': voip_stats,
        'active_calls': active_calls,
        'recent_tickets': recent_tickets,
        'status_data': status_data,
        'category_data': category_data,
        'priority_data': priority_data,
        'agent_workload': agent_workload,
        'recent_audit': recent_audit,
    }
    return render(request, 'helpdesk/dashboard.html', context)


@login_required
def ticket_list(request):
    tickets = Ticket.objects.select_related('requester', 'assigned_to', 'category').all()

    status   = request.GET.get('status', '')
    priority = request.GET.get('priority', '')
    category = request.GET.get('category', '')
    assigned = request.GET.get('assigned', '')
    search   = request.GET.get('search', '')
    source   = request.GET.get('source', '')

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

    paginator = Paginator(tickets, 20)
    page = request.GET.get('page', 1)
    tickets_page = paginator.get_page(page)
    categories = Category.objects.all()

    context = {
        'tickets': tickets_page,
        'categories': categories,
        'filters': {
            'status': status, 'priority': priority,
            'category': category, 'assigned': assigned,
            'search': search, 'source': source,
        },
        'status_choices': Ticket.STATUS_CHOICES,
        'priority_choices': Ticket.PRIORITY_CHOICES,
        'source_choices': Ticket.SOURCE_CHOICES,
        'total_count': tickets.count(),
    }
    return render(request, 'helpdesk/ticket_list.html', context)


@login_required
def ticket_detail(request, ticket_id):
    ticket = get_object_or_404(Ticket, ticket_id=ticket_id)
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
    now = timezone.now()
    period = request.GET.get('period', '30')
    try:
        days = int(period)
    except ValueError:
        days = 30
    start_date = now - timedelta(days=days)

    tickets = Ticket.objects.filter(created_at__gte=start_date)

    # VoIP report stats — optional
    voip_total = voip_completed = voip_missed = 0
    voip_avg_duration = 0
    try:
        from voip.models import VoIPCall
        vcalls = VoIPCall.objects.filter(initiated_at__gte=start_date)
        voip_total = vcalls.count()
        voip_completed = vcalls.filter(status='COMPLETED').count()
        voip_missed = vcalls.filter(status='MISSED').count()
        voip_avg_duration = vcalls.filter(status='COMPLETED').aggregate(
            avg=Avg('talk_duration_seconds'))['avg'] or 0
    except Exception:
        pass

    context = {
        'period': period,
        'period_label': f'Last {days} days',
        'total_created': tickets.count(),
        'total_resolved': tickets.filter(status__in=['RESOLVED', 'CLOSED']).count(),
        'total_pending': tickets.filter(status__in=['OPEN', 'IN_PROGRESS', 'PENDING']).count(),
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
        'by_source': list(tickets.values('source').annotate(count=Count('id'))),
        'voip_total': voip_total,
        'voip_completed': voip_completed,
        'voip_missed': voip_missed,
        'voip_avg_duration': voip_avg_duration,
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
