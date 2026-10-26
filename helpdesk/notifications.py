"""
BeitDesk Notification Service
==============================
Two delivery mechanisms work together:

1. WebSocket (instant, while browser is open)
   Uses Django Channels channel layer to push to ws://host/ws/notifications/
   The consumer delivers it to the connected browser immediately.

2. VAPID Web Push (background, even when browser is closed)
   Uses pywebpush to send OS-level push notifications via the browser's
   push service (Chrome, Edge, Firefox).

3. PendingNotification (queue for offline users)
   Stored in DB, flushed to WebSocket when user reconnects.

4. Email (fallback for important events)
   Used for ticket assignments when push fails.

Notifications are shaped like chat messages (WhatsApp-style): each one carries a
`sender`, a short message `body` and the ticket it belongs to (`ticket_id` /
`ticket_title`), and the browser groups them into one conversation per ticket
with an unread count. `read_at` (seen) is tracked separately from `delivered`
(reached the browser).
"""
import json
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _name(user, default='BeitDesk'):
    if not user:
        return default
    return user.get_full_name() or user.username


def _preview(text, limit=140):
    """One-line message preview: collapse whitespace, truncate with an ellipsis."""
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _ticket_url(ticket, user):
    if user.is_staff:
        return f'/dashboard/tickets/{ticket.ticket_id}/'
    return f'/dashboard/portal/ticket/{ticket.ticket_id}/'


def _active_staff(exclude=None):
    from django.contrib.auth.models import User
    qs = User.objects.filter(is_staff=True, is_active=True)
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    return qs


# ── WebSocket push via channel layer ─────────────────────────────────────────

def ws_push(user_id, title, body, url='/dashboard/', tag='beitdesk',
            level='info', notif_id=None, extra=None):
    """
    Push a notification to a user's WebSocket connection instantly.
    Non-blocking — uses async_to_sync to call from synchronous Django views.
    `extra` carries the chat-style fields (sender, ticket_id, ticket_title, kind, created_at).
    """
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        channel_layer = get_channel_layer()
        if not channel_layer:
            return False

        payload = {
            'type':  'notification',
            'id':    notif_id,
            'title': title,
            'body':  body,
            'url':   url,
            'tag':   tag,
            'icon':  '/static/pwa/icon-192.png',
            'level': level,
        }
        payload.update(extra or {})
        async_to_sync(channel_layer.group_send)(
            f'user_{user_id}', payload
        )
        return True
    except Exception as e:
        logger.debug('WS push failed for user %s: %s', user_id, e)
        return False


def ws_broadcast_staff(ticket_id, title, status, priority, url):
    """
    Broadcast a ticket update to all connected IT staff (live feed).
    """
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        channel_layer = get_channel_layer()
        if not channel_layer:
            return

        async_to_sync(channel_layer.group_send)(
            'it_staff', {
                'type':      'ticket_update',
                'ticket_id': ticket_id,
                'title':     title,
                'status':    status,
                'priority':  priority,
                'url':       url,
            }
        )
    except Exception as e:
        logger.debug('Staff broadcast failed: %s', e)


# ── VAPID Web Push (background / browser closed) ─────────────────────────────

def vapid_push(user, title, body, url='/dashboard/', tag='beitdesk', extra=None):
    """
    Send a real OS-level push notification using VAPID / pywebpush.
    Works even when the browser tab is closed.

    Laid out like a WhatsApp notification: the title is "Sender · TICKET-ID",
    the body is the message, and all messages for one ticket share a tag so the
    OS shows a single, updating entry per conversation.
    """
    try:
        profile = user.profile
        if not getattr(profile, 'push_enabled', False):
            return False
        if not getattr(profile, 'push_subscription', None):
            return False

        subscription = json.loads(profile.push_subscription)
        if not subscription.get('endpoint'):
            return False

        if not settings.VAPID_PUBLIC_KEY or not settings.VAPID_PRIVATE_KEY:
            return False

        from pywebpush import webpush
        import base64

        private_key_pem = base64.urlsafe_b64decode(
            settings.VAPID_PRIVATE_KEY + '==').decode()

        extra = extra or {}
        sender, ticket_id = extra.get('sender', ''), extra.get('ticket_id', '')
        os_title = f'{sender} · {ticket_id}' if sender and ticket_id else title
        payload = json.dumps({
            'title': os_title, 'body': body,
            'url':   url,  'tag':  f'ticket-{ticket_id}' if ticket_id else tag,
            'renotify': True,
            'icon':  '/static/pwa/icon-192.png',
            'badge': '/static/pwa/icon-72.png',
        })

        webpush(
            subscription_info=subscription,
            data=payload,
            vapid_private_key=private_key_pem,
            vapid_claims={
                'sub': f'mailto:{settings.VAPID_EMAIL}',
                'aud': '/'.join(subscription['endpoint'].split('/')[:3]),
            },
        )
        return True
    except Exception as e:
        logger.debug('VAPID push failed for %s: %s', user.username, e)
        return False


# ── PendingNotification queue ─────────────────────────────────────────────────

def queue_notification(user, title, body, url, tag, level='info', extra=None):
    """
    Save notification to DB so it can be delivered when user reconnects.
    Returns the PendingNotification pk for ack tracking.
    """
    try:
        from helpdesk.models import PendingNotification
        extra = extra or {}
        n = PendingNotification.objects.create(
            user=user,
            conversation=(extra.get('ticket_id') or '')[:40],
            payload=json.dumps({
                'title': title, 'body': body,
                'url': url, 'tag': tag,
                'icon': '/static/pwa/icon-192.png',
                'level': level,
                **extra,
            }),
            delivered=False,
        )
        return n.pk
    except Exception as e:
        logger.warning('Failed to queue notification: %s', e)
        return None


# ── Main notification functions ───────────────────────────────────────────────

def notify(user, title, body, url='/dashboard/', tag='beitdesk',
           level='info', email_subject=None, *, sender='', ticket=None, kind=''):
    """
    Deliver a notification to a user via all available channels:
    1. WebSocket (instant if online)
    2. VAPID push (background)
    3. DB queue (flush on reconnect)
    4. Email (if email_subject provided and VAPID failed)

    `sender`, `ticket` and `kind` give it its chat-message shape; all optional
    so older call sites keep working.
    """
    from django.utils import timezone
    extra = {'kind': kind, 'created_at': timezone.now().isoformat()}
    if sender:
        extra['sender'] = sender
    if ticket is not None:
        extra['ticket_id'] = ticket.ticket_id
        extra['ticket_title'] = ticket.title

    # Queue first so we have the ID
    notif_id = queue_notification(user, title, body, url, tag, level, extra)

    # 1. WebSocket — instant if browser is open
    ws_ok = ws_push(user.pk, title, body, url, tag, level, notif_id, extra)

    # 2. VAPID push — background even if browser closed
    vapid_ok = vapid_push(user, title, body, url, tag, extra)

    # 3. Email fallback for important notifications
    if email_subject and not vapid_ok and user.email:
        try:
            from django.core.mail import send_mail
            send_mail(
                subject=f'[BeitDesk] {email_subject}',
                message=f'{body}\n\nView: http://localhost:8000{url}',
                from_email='noreply@beitbridge.gov.zw',
                recipient_list=[user.email],
                fail_silently=True,
            )
        except Exception:
            pass

    logger.debug('notify(%s): ws=%s vapid=%s id=%s', user.username, ws_ok, vapid_ok, notif_id)
    return notif_id


def mark_conversation_read(user, ticket_id):
    """Opening a ticket clears its unread count, like opening a chat."""
    try:
        from django.utils import timezone
        from helpdesk.models import PendingNotification
        return PendingNotification.objects.filter(
            user=user, conversation=ticket_id, read_at__isnull=True
        ).update(read_at=timezone.now(), delivered=True)
    except Exception as e:
        logger.debug('mark_conversation_read failed: %s', e)
        return 0


# ── Specific notification triggers ───────────────────────────────────────────

def notify_ticket_created(ticket, created_by=None):
    """
    A new ticket was raised (via the portal form, by a requester or by IT staff
    on someone's behalf). Every active IT staff member except the person who
    raised it gets a "New request" message; the assignee, if there is one, gets
    the more specific "assigned to you" message instead.
    """
    level = 'danger' if ticket.priority == 'CRITICAL' else \
            'warning' if ticket.priority == 'HIGH' else 'info'
    who = ticket.requester_name or _name(ticket.requester or created_by)
    urgent = {'CRITICAL': '🚨 ', 'HIGH': '🔴 '}.get(ticket.priority, '')

    for staff in _active_staff(exclude=created_by):
        if ticket.assigned_to_id and staff.pk == ticket.assigned_to_id:
            continue          # covered by notify_ticket_assigned below
        notify(staff,
               title=f'New request — {ticket.ticket_id}',
               body=_preview(f'{urgent}New request: {ticket.title}'),
               url=f'/dashboard/tickets/{ticket.ticket_id}/',
               tag=f'created-{ticket.ticket_id}', level=level,
               sender=who, ticket=ticket, kind='created')

    if ticket.assigned_to_id and ticket.assigned_to_id != getattr(created_by, 'pk', None):
        notify_ticket_assigned(ticket, assigned_by=created_by)

    # Logged on someone's behalf (phone / walk-in): let that person know it exists.
    r = ticket.requester
    if r and created_by and r.pk != created_by.pk:
        notify(r,
               title=f'Request logged — {ticket.ticket_id}',
               body=_preview(f'Your request was logged: {ticket.title}'),
               url=_ticket_url(ticket, r),
               tag=f'created-{ticket.ticket_id}', level='success',
               sender=_name(created_by, 'IT Desk'), ticket=ticket, kind='created')

    ws_broadcast_staff(ticket.ticket_id, ticket.title, ticket.status, ticket.priority,
                       f'/dashboard/tickets/{ticket.ticket_id}/')


def notify_ticket_assigned(ticket, assigned_by=None):
    """Notify IT staff when a ticket is assigned to them."""
    if not ticket.assigned_to:
        return
    user = ticket.assigned_to
    if not getattr(getattr(user, 'profile', None), 'notify_assigned', True):
        return

    url = f'/dashboard/tickets/{ticket.ticket_id}/'
    notify(
        user,
        title=f'Ticket Assigned — {ticket.ticket_id}',
        body=_preview(f'📌 Assigned to you: {ticket.title}'),
        url=url,
        tag=f'assigned-{ticket.ticket_id}',
        level='info',
        email_subject=f'Ticket Assigned — {ticket.ticket_id}',
        sender=_name(assigned_by, 'BeitDesk'), ticket=ticket, kind='assigned',
    )
    # Also broadcast to all IT staff live feed
    ws_broadcast_staff(
        ticket.ticket_id, ticket.title,
        ticket.status, ticket.priority, url)


def notify_ticket_status(ticket, old_status, changed_by):
    """Notify requester when their ticket status changes."""
    if not ticket.requester:
        return
    user = ticket.requester
    if user == changed_by:
        return
    # The profile field is `notify_status_change` (the old code read a
    # non-existent `notify_status`, so the preference was silently ignored).
    if not getattr(getattr(user, 'profile', None), 'notify_status_change', True):
        return

    STATUS_LABELS = {
        'OPEN':'Opened','IN_PROGRESS':'In Progress','PENDING':'Pending',
        'RESOLVED':'Resolved','CLOSED':'Closed','REOPENED':'Reopened',
    }
    new_label = STATUS_LABELS.get(ticket.status, ticket.status.replace('_',' ').title())
    level = 'success' if ticket.status == 'RESOLVED' else \
            'warning' if ticket.status == 'IN_PROGRESS' else 'info'
    icon = {'RESOLVED': '✅', 'CLOSED': '🔒', 'IN_PROGRESS': '🔧', 'REOPENED': '↩️'}.get(ticket.status, '🔄')

    notify(
        user,
        title=f'Request {ticket.ticket_id} — {new_label}',
        body=_preview(f'{icon} {new_label}: {ticket.title}'),
        url=_ticket_url(ticket, user),
        tag=f'status-{ticket.ticket_id}',
        level=level,
        sender=_name(changed_by, 'IT Team'), ticket=ticket, kind='status',
    )


def notify_new_comment(ticket, comment):
    """
    Notify the right people about a new comment — like a chat reply.

    * the assignee, when someone else wrote it
    * the requester, when IT replied (never for internal notes)
    * all IT staff, when a requester writes on a ticket nobody has picked up yet
    """
    author = comment.author
    sender = _name(author)
    preview = _preview(comment.body)
    sent_to = set()

    def send(user, kind):
        if not user or user.pk in sent_to or user == author:
            return
        sent_to.add(user.pk)
        notify(user,
               title=f'New comment — {ticket.ticket_id}' if kind == 'comment'
                     else f'IT Team replied — {ticket.ticket_id}',
               body=preview, url=_ticket_url(ticket, user),
               tag=f'{kind}-{ticket.ticket_id}', level='info',
               sender=sender, ticket=ticket, kind=kind)

    if ticket.assigned_to:
        send(ticket.assigned_to, 'comment')
    elif author and not author.is_staff:
        for staff in _active_staff(exclude=author):
            send(staff, 'comment')

    if ticket.requester and not getattr(comment, 'is_internal', False):
        send(ticket.requester, 'reply' if author and author.is_staff else 'comment')


def notify_sla_breach(ticket, level=None):
    """
    Notify assignee and broadcast to all IT staff on SLA warning/breach.
    `level` is the escalation level passed by the check_sla command
    (1 warning, 2 breached, 3 critical); the command previously called this
    with two arguments while it only accepted one, so it never sent anything.
    """
    url = f'/dashboard/tickets/{ticket.ticket_id}/'
    label = {1: 'warning', 2: 'breached', 3: 'CRITICAL'}.get(level, 'breached')
    msg = f'⚠ SLA {label}: {ticket.title}'

    if ticket.assigned_to:
        notify(
            ticket.assigned_to,
            title=f'⚠ SLA {label.title()} — {ticket.ticket_id}',
            body=_preview(msg),
            url=url,
            tag=f'sla-{ticket.ticket_id}',
            level='warning' if level == 1 else 'danger',
            sender='SLA Monitor', ticket=ticket, kind='sla',
        )

    # Broadcast to all IT staff
    ws_broadcast_staff(ticket.ticket_id, f'⚠ SLA {label.upper()}: {ticket.title}',
                       ticket.status, ticket.priority, url)


def build_conversations(items, limit=30):
    """
    Group notifications (newest first) into one "chat" per ticket.

    `items` are the dicts produced by the notifications API. Each result carries
    the latest message, the sender, an unread count and the ids it covers, and
    the list is ordered by most recent activity — like a WhatsApp chat list.
    """
    convs, order = {}, []
    for it in items:
        key = it.get('ticket_id') or it.get('tag') or f"n{it.get('id')}"
        c = convs.get(key)
        if c is None:
            c = convs[key] = {
                'key': key,
                'ticket_id': it.get('ticket_id', ''),
                'ticket_title': it.get('ticket_title', ''),
                'sender': it.get('sender', ''),
                'preview': it.get('body', ''),
                'title': it.get('title', ''),
                'kind': it.get('kind', ''),
                'level': it.get('level', 'info'),
                'tag': it.get('tag', ''),
                'url': it.get('url', '/dashboard/'),
                'last_at': it.get('created_at'),
                'last_id': it.get('id'),
                'unread': 0, 'count': 0, 'ids': [],
            }
            order.append(key)
        c['count'] += 1
        c['ids'].append(it.get('id'))
        if not it.get('read'):
            c['unread'] += 1
    return [convs[k] for k in order[:limit]]
