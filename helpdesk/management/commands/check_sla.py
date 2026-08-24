from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

class Command(BaseCommand):
    help = 'Check SLA breaches and send escalation alerts'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--warning-hours', type=int, default=2)

    def handle(self, *args, **options):
        from helpdesk.models import Ticket
        from helpdesk.notifications import notify_sla_escalation
        dry_run = options['dry_run']
        warning_hrs = options['warning_hours']
        now = timezone.now()
        tickets = Ticket.objects.filter(
            status__in=['OPEN','IN_PROGRESS','PENDING','REOPENED'],
            due_date__isnull=False
        ).select_related('assigned_to','category')

        warned=breached=critical=unchanged=0
        for ticket in tickets:
            hours_over = (now-ticket.due_date).total_seconds()/3600
            hours_to   = (ticket.due_date-now).total_seconds()/3600
            if hours_over >= 24: new_level=3
            elif hours_over >= 0: new_level=2
            elif hours_to <= warning_hrs: new_level=1
            else: new_level=0

            if new_level==0:
                if ticket.escalation_level>0:
                    ticket.escalation_level=0; ticket.sla_breach=False
                    if not dry_run: ticket.save(update_fields=['escalation_level','sla_breach'])
                unchanged+=1; continue

            should_notify = (new_level>ticket.escalation_level or
                ticket.escalation_notified_at is None or
                (now-ticket.escalation_notified_at).total_seconds()>14400)

            if should_notify:
                lbl={1:'WARNING',2:'BREACHED',3:'CRITICAL'}[new_level]
                if dry_run:
                    style = self.style.WARNING if new_level==1 else self.style.ERROR
                    self.stdout.write(style(f'  [DRY] {lbl} {ticket.ticket_id}: {ticket.title[:45]}'))
                else:
                    ticket.escalation_level=new_level
                    ticket.sla_breach=new_level>=2
                    ticket.escalation_notified_at=now
                    ticket.save(update_fields=['escalation_level','sla_breach','escalation_notified_at'])
                    notify_sla_escalation(ticket, new_level)
            if new_level==1: warned+=1
            elif new_level==2: breached+=1
            elif new_level==3: critical+=1

        self.stdout.write(self.style.SUCCESS(f'SLA Check {now:%d %b %Y %H:%M}'))
        self.stdout.write(f'  ⚠️  Warning:{warned}  🔴 Breached:{breached}  🚨 Critical:{critical}  ✓ OK:{unchanged}')
        if dry_run: self.stdout.write(self.style.WARNING('  (Dry run — no notifications sent)'))
