"""
Global context processor — injected into every template render.
Kept MINIMAL: only 2 queries per request (tickets + voip).
Uses a single aggregate() call instead of separate COUNTs.
"""
from django.conf import settings
from django.utils import timezone


def global_stats(request):
    """Inject sidebar stats and push key into all templates."""
    ctx = {'PUSH_PUBLIC_KEY': getattr(settings, 'PUSH_PUBLIC_KEY', ''),
           'today': timezone.now().date()}

    if not request.user.is_authenticated:
        return ctx

    try:
        from django.db.models import Count, Q
        from .models import Ticket
        # Single aggregate — replaces 3 separate COUNT queries
        agg = Ticket.objects.aggregate(
            open_count    = Count('id', filter=Q(status__in=['OPEN','IN_PROGRESS'])),
            critical_count= Count('id', filter=Q(status__in=['OPEN','IN_PROGRESS'], priority='CRITICAL')),
            new_unassigned= Count('id', filter=Q(status='OPEN', assigned_to=None)),
        )
        ctx['sidebar_open_tickets'] = agg['open_count']    or 0
        ctx['sidebar_critical']     = agg['critical_count'] or 0
        ctx['sidebar_new_requests'] = agg['new_unassigned'] or 0
    except Exception:
        ctx['sidebar_open_tickets'] = 0
        ctx['sidebar_critical']     = 0
        ctx['sidebar_new_requests'] = 0

    try:
        from voip.models import VoIPCall
        ctx['sidebar_active_calls'] = VoIPCall.objects.filter(
            status__in=['RINGING','ACTIVE']).count()
    except Exception:
        ctx['sidebar_active_calls'] = 0

    return ctx
