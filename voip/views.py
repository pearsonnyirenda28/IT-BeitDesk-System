from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Avg, Sum, Q
from django.utils import timezone
from django.http import JsonResponse
from django.core.paginator import Paginator
from datetime import timedelta
from .models import VoIPCall, Extension, CallerProfile, VoIPProvider, CallEvent
from helpdesk.models import AuditLog, Ticket


def get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    return xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR')


def _year_context(request):
    """Return dict of year-viewing context variables."""
    active_year    = request.session.get('active_db_year')
    active_db_name = request.session.get('active_db_name', '')
    return {
        'active_year':    active_year,
        'active_db_name': active_db_name,
        'is_past_year':   bool(active_year),
    }


@login_required
def call_board(request):
    """Live active calls board — only meaningful for current year."""
    yctx = _year_context(request)

    if yctx['is_past_year']:
        # For past years show historical calls, not live board
        try:
            calls = VoIPCall.objects.select_related(
                'answered_by', 'destination_extension'
            ).order_by('-initiated_at')[:50]
            stats = {
                'active': 0,
                'completed_today': 0,
                'missed_today': 0,
                'total_today': calls.count(),
                'avg_duration': calls.filter(status='COMPLETED').aggregate(
                    avg=Avg('talk_duration_seconds'))['avg'] or 0,
                'total_minutes_today': (calls.filter(status='COMPLETED').aggregate(
                    total=Sum('talk_duration_seconds'))['total'] or 0) / 60,
            }
        except Exception:
            calls = []
            stats = {k: 0 for k in ['active','completed_today','missed_today',
                                     'total_today','avg_duration','total_minutes_today']}
        context = {'active_calls': calls, 'stats': stats, **yctx}
        return render(request, 'voip/call_board.html', context)

    active_calls = VoIPCall.objects.filter(
        status__in=['RINGING', 'ACTIVE', 'ON_HOLD']
    ).select_related('answered_by', 'destination_extension', 'caller_extension').order_by('-initiated_at')

    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_calls = VoIPCall.objects.filter(initiated_at__gte=today_start)

    stats = {
        'active': active_calls.count(),
        'completed_today': today_calls.filter(status='COMPLETED').count(),
        'missed_today': today_calls.filter(status='MISSED').count(),
        'total_today': today_calls.count(),
        'avg_duration': today_calls.filter(status='COMPLETED').aggregate(
            avg=Avg('talk_duration_seconds'))['avg'] or 0,
        'total_minutes_today': (today_calls.filter(status='COMPLETED').aggregate(
            total=Sum('talk_duration_seconds'))['total'] or 0) / 60,
    }
    context = {'active_calls': active_calls, 'stats': stats, **yctx}
    return render(request, 'voip/call_board.html', context)


@login_required
def call_log(request):
    yctx = _year_context(request)
    db_error = False

    try:
        calls = VoIPCall.objects.select_related('answered_by', 'destination_extension').all()

        status    = request.GET.get('status', '')
        direction = request.GET.get('direction', '')
        search    = request.GET.get('search', '')
        date_from = request.GET.get('date_from', '')
        date_to   = request.GET.get('date_to', '')

        if status:
            calls = calls.filter(status=status)
        if direction:
            calls = calls.filter(direction=direction)
        if search:
            calls = calls.filter(
                Q(caller_number__icontains=search) |
                Q(caller_name__icontains=search) |
                Q(destination_number__icontains=search) |
                Q(notes__icontains=search)
            )
        if date_from:
            calls = calls.filter(initiated_at__date__gte=date_from)
        if date_to:
            calls = calls.filter(initiated_at__date__lte=date_to)

        paginator    = Paginator(calls, 25)
        page         = request.GET.get('page', 1)
        calls_page   = paginator.get_page(page)
        total_count  = calls.count()

    except Exception:
        db_error    = True
        calls_page  = []
        total_count = 0
        status = direction = search = date_from = date_to = ''

    context = {
        'calls':            calls_page,
        'status_choices':   VoIPCall.STATUS_CHOICES,
        'direction_choices':VoIPCall.DIRECTION_CHOICES,
        'filters': {
            'status': status, 'direction': direction,
            'search': search, 'date_from': date_from, 'date_to': date_to
        },
        'total_count': total_count,
        'db_error':    db_error,
        **yctx,
    }
    return render(request, 'voip/call_log.html', context)


@login_required
def call_detail(request, call_uuid):
    yctx   = _year_context(request)
    call   = get_object_or_404(VoIPCall, call_uuid=call_uuid)
    events = call.events.all()
    linked_tickets = call.tickets.all()

    if request.method == 'POST' and not yctx['is_past_year']:
        action = request.POST.get('action')
        if action == 'update_notes':
            call.notes = request.POST.get('notes', '')
            call.save()
            messages.success(request, 'Notes updated.')
            return redirect('call_detail', call_uuid=call_uuid)

        elif action == 'create_ticket':
            ticket = Ticket.objects.create(
                title=f'VoIP Call from {call.caller_number} - {call.caller_name or "Unknown"}',
                description=(f'Ticket created from VoIP call.\n\n'
                             f'Caller: {call.caller_number} ({call.caller_name})\n'
                             f'Duration: {call.talk_duration_display}\n'
                             f'Date: {call.initiated_at}\n\nNotes: {call.notes}'),
                source=Ticket.SOURCE_VOIP,
                requester_phone=call.caller_number,
                requester_name=call.caller_name or call.caller_number,
                requester=request.user,
                voip_call=call,
            )
            call.ticket_created = True
            call.save()
            AuditLog.objects.create(
                user=request.user, action=AuditLog.ACTION_CREATE, model_name='Ticket',
                object_id=ticket.ticket_id, object_repr=str(ticket),
                ip_address=get_client_ip(request),
                notes=f'Created from VoIP call {call_uuid}',
            )
            messages.success(request, f'Ticket {ticket.ticket_id} created.')
            return redirect('ticket_detail', ticket_id=ticket.ticket_id)

        elif action == 'end_call':
            if call.status in ['RINGING', 'ACTIVE', 'ON_HOLD']:
                call.status = VoIPCall.STATUS_COMPLETED if call.answered_at else VoIPCall.STATUS_MISSED
                call.ended_at = timezone.now()
                call.save()
                CallEvent.objects.create(call=call, event_type=CallEvent.EVENT_ENDED,
                                         performed_by=request.user, description='Call ended manually')
                AuditLog.objects.create(
                    user=request.user, action=AuditLog.ACTION_CALL_END, model_name='VoIPCall',
                    object_id=str(call.call_uuid), object_repr=str(call),
                    ip_address=get_client_ip(request),
                )
                messages.success(request, 'Call marked as ended.')
            return redirect('call_detail', call_uuid=call_uuid)

    context = {
        'call':           call,
        'events':         events,
        'linked_tickets': linked_tickets,
        **yctx,
    }
    return render(request, 'voip/call_detail.html', context)


@login_required
def log_call(request):
    yctx = _year_context(request)

    if yctx['is_past_year']:
        messages.warning(request, f'You are viewing the {yctx["active_year"]} archive. Switch to the current year to log new calls.')
        return redirect('call_log')

    if request.method == 'POST':
        caller_number = request.POST.get('caller_number', '').strip()
        caller_name   = request.POST.get('caller_name', '').strip()
        destination   = request.POST.get('destination_number', '').strip()
        direction     = request.POST.get('direction', 'INBOUND')
        status        = request.POST.get('status', 'COMPLETED')
        talk_minutes  = request.POST.get('talk_minutes', '0')
        notes         = request.POST.get('notes', '')

        try:
            talk_secs = int(float(talk_minutes) * 60)
        except ValueError:
            talk_secs = 0

        now  = timezone.now()
        call = VoIPCall.objects.create(
            caller_number=caller_number, caller_name=caller_name,
            destination_number=destination or 'HELPDESK',
            direction=direction, status=status, notes=notes,
            initiated_at=now - timedelta(seconds=talk_secs),
            answered_at=now - timedelta(seconds=talk_secs) if status == 'COMPLETED' else None,
            ended_at=now if status == 'COMPLETED' else None,
            answered_by=request.user,
        )

        profile, _ = CallerProfile.objects.get_or_create(phone_number=caller_number)
        if caller_name and not profile.name:
            profile.name = caller_name
        profile.total_calls += 1
        profile.last_call = now
        profile.save()

        AuditLog.objects.create(
            user=request.user, action=AuditLog.ACTION_CALL_END, model_name='VoIPCall',
            object_id=str(call.call_uuid), object_repr=str(call),
            ip_address=get_client_ip(request), notes='Manual call log entry',
        )
        messages.success(request, f'Call from {caller_number} logged successfully.')

        if request.POST.get('create_ticket'):
            return redirect(f'/dashboard/tickets/create/?caller={caller_number}&caller_name={caller_name}&source=VOIP&voip_call={call.pk}')
        return redirect('call_log')

    return render(request, 'voip/log_call.html', {
        'direction_choices': VoIPCall.DIRECTION_CHOICES,
        'status_choices':    VoIPCall.STATUS_CHOICES,
        **yctx,
    })


@login_required
def caller_directory(request):
    yctx    = _year_context(request)
    db_error = False

    try:
        callers = CallerProfile.objects.all().order_by('-total_calls')
        search  = request.GET.get('search', '')
        if search:
            callers = callers.filter(
                Q(phone_number__icontains=search) |
                Q(name__icontains=search) |
                Q(company__icontains=search)
            )
        paginator = Paginator(callers, 25)
        page      = request.GET.get('page', 1)
        callers_page = paginator.get_page(page)
    except Exception:
        db_error     = True
        callers_page = []
        search       = ''

    return render(request, 'voip/caller_directory.html', {
        'callers':  callers_page,
        'search':   search,
        'db_error': db_error,
        **yctx,
    })


def api_active_calls(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    now = timezone.now()
    try:
        calls = VoIPCall.objects.filter(status__in=['RINGING', 'ACTIVE', 'ON_HOLD']).values(
            'call_uuid', 'caller_number', 'caller_name', 'destination_number',
            'status', 'direction', 'initiated_at', 'answered_at', 'talk_duration_seconds',
        )
        call_list = []
        for c in calls:
            c['call_uuid'] = str(c['call_uuid'])
            if c['initiated_at']:
                c['elapsed_seconds'] = int((now - c['initiated_at']).total_seconds())
            c['initiated_at'] = c['initiated_at'].isoformat() if c['initiated_at'] else None
            c['answered_at']  = c['answered_at'].isoformat()  if c['answered_at']  else None
            call_list.append(c)
        return JsonResponse({'calls': call_list, 'count': len(call_list)})
    except Exception:
        return JsonResponse({'calls': [], 'count': 0})
