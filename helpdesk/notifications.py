import json
import logging
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from django.contrib.auth.models import User

logger = logging.getLogger('beitdesk.notifications')

def _get_profile(user):
    try: return user.profile
    except Exception:
        from accounts.models import UserProfile
        p,_ = UserProfile.objects.get_or_create(user=user); return p

def send_email_notification(to_user, subject, message_text):
    if not to_user.email: return False
    try:
        send_mail(f'[BeitDesk] {subject}', message_text,
                  getattr(settings,'DEFAULT_FROM_EMAIL','beitdesk@beitbridge.gov.zw'),
                  [to_user.email], fail_silently=True)
        return True
    except Exception as e:
        logger.warning(f'Email failed: {e}'); return False

def notify_ticket_assigned(ticket, assigned_to_user, assigned_by_user):
    profile = _get_profile(assigned_to_user)
    if not profile.notify_assigned: return
    send_email_notification(assigned_to_user,
        f'Ticket {ticket.ticket_id} assigned to you',
        f'Ticket {ticket.ticket_id} assigned by {assigned_by_user.username}.\n'
        f'Title: {ticket.title}\nPriority: {ticket.priority}')
    send_push_notification(assigned_to_user,
        title=f'Ticket Assigned — {ticket.ticket_id}',
        body=f'{ticket.title} · {ticket.priority}',
        url=f'/dashboard/tickets/{ticket.ticket_id}/',
        tag=f'assign-{ticket.ticket_id}')

def notify_status_change(ticket, old_status, new_status, changed_by_user):
    labels = {'OPEN':'Open','IN_PROGRESS':'In Progress','PENDING':'Pending',
               'RESOLVED':'Resolved','CLOSED':'Closed','REOPENED':'Reopened'}
    new_label = labels.get(new_status, new_status)
    if ticket.assigned_to and ticket.assigned_to != changed_by_user:
        profile = _get_profile(ticket.assigned_to)
        if profile.notify_status_change:
            send_email_notification(ticket.assigned_to,
                f'Ticket {ticket.ticket_id} → {new_label}',
                f'Ticket {ticket.ticket_id} status changed to {new_label}.\nTitle: {ticket.title}')
            send_push_notification(ticket.assigned_to,
                title=f'{ticket.ticket_id} → {new_label}',
                body=ticket.title, url=f'/dashboard/tickets/{ticket.ticket_id}/',
                tag=f'status-{ticket.ticket_id}')

def notify_sla_escalation(ticket, level):
    labels = {1:'⚠️ SLA Warning',2:'🔴 SLA Breached',3:'🚨 Critical Overdue'}
    label = labels.get(level,'SLA Alert')
    hours = int((timezone.now()-ticket.due_date).total_seconds()/3600) if ticket.due_date else 0
    for admin in User.objects.filter(is_superuser=True, is_active=True):
        send_email_notification(admin, f'{label} — {ticket.ticket_id}',
            f'{label}\nTicket {ticket.ticket_id} is {hours}h overdue.\nTitle: {ticket.title}')
        send_push_notification(admin, title=f'{label} — {ticket.ticket_id}',
            body=f'{ticket.title} · {hours}h overdue',
            url=f'/dashboard/tickets/{ticket.ticket_id}/', tag=f'sla-{ticket.ticket_id}')

def send_push_notification(user, title, body, url='/', icon='/static/pwa/icon-192.png',
                            badge='/static/pwa/icon-72.png', tag='beitdesk'):
    profile = _get_profile(user)
    if not profile.push_enabled or not profile.push_subscription: return False
    payload = json.dumps({'title':title,'body':body,'url':url,'icon':icon,
                          'badge':badge,'tag':tag,
                          'timestamp':int(timezone.now().timestamp()*1000)})
    try:
        from pywebpush import webpush
        subscription_info = json.loads(profile.push_subscription)
        vapid_key = getattr(settings,'VAPID_PRIVATE_KEY',None)
        vapid_claims = getattr(settings,'VAPID_CLAIMS',None)
        if vapid_key:
            webpush(subscription_info=subscription_info, data=payload,
                    vapid_private_key=vapid_key, vapid_claims=vapid_claims)
            return True
    except ImportError: pass
    except Exception as e:
        if '410' in str(e) or 'expired' in str(e).lower():
            profile.push_subscription=''; profile.push_enabled=False
            profile.save(update_fields=['push_subscription','push_enabled'])
        return False
    # Fallback: store as pending for polling
    try:
        from helpdesk.models import PendingNotification
        PendingNotification.objects.create(user=user, payload=payload)
    except Exception: pass
    return False
