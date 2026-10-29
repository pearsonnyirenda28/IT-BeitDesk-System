"""
IT HelpDesk WebSocket Consumer — Real-Time Notifications

Each logged-in user gets their own WebSocket connection at
ws://host/ws/notifications/

The server pushes JSON messages instantly when:
  • A ticket is assigned to this user
  • A ticket this user owns changes status
  • A comment is added to one of their tickets
  • A new ticket is raised (for IT staff — live ticker)
  • An SLA breach occurs (for IT staff)

Message format (server → client):
{
    "type":  "notification",
    "title": "Ticket Assigned — ND000042",
    "body":  "Printer not working — Finance Office",
    "url":   "/dashboard/tickets/ND000042/",
    "tag":   "ticket-ND000042",
    "icon":  "/static/pwa/icon-192.png",
    "level": "info"   // info | warning | danger
}

Group naming:
  user_{user_id}   — user-specific channel (notifications for that person)
  it_staff         — broadcast group for all IT staff (new tickets, SLA alerts)
"""
import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)


class NotificationConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        user = self.scope.get('user')

        # Reject unauthenticated connections
        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        self.user_id    = user.pk
        self.user_group = f'user_{user.pk}'

        # Join personal channel group
        await self.channel_layer.group_add(self.user_group, self.channel_name)

        # IT staff also join the broadcast group for new-ticket / SLA alerts
        if user.is_staff:
            await self.channel_layer.group_add('it_staff', self.channel_name)

        await self.accept()
        logger.debug('WS connect: user=%s group=%s', user.username, self.user_group)

        # Send any pending notifications that arrived while disconnected
        await self.flush_pending_notifications(user)

    async def disconnect(self, close_code):
        if hasattr(self, 'user_group'):
            await self.channel_layer.group_discard(self.user_group, self.channel_name)
            await self.channel_layer.group_discard('it_staff', self.channel_name)
        logger.debug('WS disconnect: user=%s code=%s', getattr(self,'user_id','?'), close_code)

    async def receive(self, text_data):
        """Handle messages from the client (heartbeat / ack)."""
        try:
            data = json.loads(text_data)
            if data.get('type') == 'ping':
                await self.send(text_data=json.dumps({'type': 'pong'}))
            elif data.get('type') == 'ack':
                # Client acknowledged notification — mark delivered
                notif_id = data.get('id')
                if notif_id:
                    await self.mark_delivered(notif_id)
        except Exception:
            pass

    # ── Handlers for messages pushed from server code ─────────────────────────

    async def notification(self, event):
        """Receive a notification event from channel layer and forward to WS."""
        await self.send(text_data=json.dumps({
            'type':         'notification',
            'id':           event.get('id'),
            'title':        event.get('title', 'BeitDesk'),
            'body':         event.get('body', ''),
            'url':          event.get('url', '/dashboard/'),
            'tag':          event.get('tag', 'beitdesk'),
            'icon':         event.get('icon', '/static/pwa/icon-192.png'),
            'level':        event.get('level', 'info'),
            # chat-style fields
            'sender':       event.get('sender', ''),
            'ticket_id':    event.get('ticket_id', ''),
            'ticket_title': event.get('ticket_title', ''),
            'kind':         event.get('kind', ''),
            'created_at':   event.get('created_at', ''),
        }))

    async def ticket_update(self, event):
        """Receive a ticket-update broadcast for IT staff live feed."""
        await self.send(text_data=json.dumps({
            'type':      'ticket_update',
            'ticket_id': event.get('ticket_id'),
            'title':     event.get('title'),
            'status':    event.get('status'),
            'priority':  event.get('priority'),
            'url':       event.get('url'),
        }))

    # ── Database helpers ──────────────────────────────────────────────────────

    @database_sync_to_async
    def _load_pending(self, user):
        from helpdesk.models import PendingNotification
        rows = PendingNotification.objects.filter(
            user=user, delivered=False
        ).order_by('created_at')[:20]
        return [(p.pk, p.payload) for p in rows]

    async def flush_pending_notifications(self, user):
        """
        Send notifications that were queued while the user was offline.

        They are flagged `queued` so the browser adds them to the list and the
        unread badge without popping a toast or playing a sound for each one.

        (The previous version evaluated its queryset and saved rows from inside
        the event loop; Django refuses that, the error was swallowed, and the
        backlog was therefore never pushed over the WebSocket at all.)
        """
        try:
            pending = await self._load_pending(user)
        except Exception:
            return
        for pk, raw in pending:
            try:
                payload = json.loads(raw)
                payload['id'] = pk
                payload['queued'] = True
                await self.send(text_data=json.dumps({'type': 'notification', **payload}))
                await self.mark_delivered(pk)
            except Exception:
                pass

    @database_sync_to_async
    def mark_delivered(self, notif_id):
        from helpdesk.models import PendingNotification
        PendingNotification.objects.filter(pk=notif_id, user_id=self.user_id).update(
            delivered=True)
