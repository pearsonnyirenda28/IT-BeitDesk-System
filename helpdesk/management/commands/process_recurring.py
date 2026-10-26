"""
Management command: process_recurring
Creates tickets from active RecurringTicket schedules that are due.
Schedule with Windows Task Scheduler or cron to run daily at 06:00.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta


class Command(BaseCommand):
    help = 'Create tickets from recurring schedules that are due today'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Preview tickets without creating them')

    def handle(self, *args, **options):
        from helpdesk.models import RecurringTicket, Ticket, AuditLog
        dry = options['dry_run']
        now = timezone.now()
        today = now.date()
        created = 0
        skipped = 0

        schedules = RecurringTicket.objects.filter(
            is_active=True, next_due_at__date__lte=today
        ).select_related('category', 'assigned_to', 'created_by')

        if not schedules.exists():
            self.stdout.write('  ℹ  No recurring tickets due today.')
            return

        for sched in schedules:
            # Build ticket ID
            date_str = today.strftime('%Y%m%d')
            tid = f"REC-{sched.pk}-{date_str}"

            # Skip if already created today
            if Ticket.objects.filter(ticket_id=tid).exists():
                skipped += 1
                continue

            title = sched.title_template.replace(
                '{date}', today.strftime('%d %B %Y')).replace(
                '{week}', today.strftime('Week %W')).replace(
                '{month}', today.strftime('%B %Y'))
            desc = sched.description_template.replace(
                '{date}', today.strftime('%d %B %Y')).replace(
                '{week}', today.strftime('Week %W')).replace(
                '{month}', today.strftime('%B %Y'))

            if dry:
                self.stdout.write(
                    f"  [DRY RUN] Would create: [{tid}] {title}")
            else:
                ticket = Ticket.objects.create(
                    ticket_id=tid,
                    title=title,
                    description=desc,
                    category=sched.category,
                    priority=sched.priority,
                    assigned_to=sched.assigned_to,
                    requester=sched.created_by,
                    status='OPEN',
                    source='RECURRING',
                )
                # Compute next due date
                sched.last_created_at = now
                freq = sched.frequency
                if freq == 'DAILY':
                    sched.next_due_at = now + timedelta(days=1)
                elif freq == 'WEEKLY':
                    sched.next_due_at = now + timedelta(weeks=1)
                elif freq == 'BIWEEKLY':
                    sched.next_due_at = now + timedelta(weeks=2)
                elif freq == 'MONTHLY':
                    # Same day next month
                    m = today.month + 1
                    y = today.year + (m > 12)
                    m = m if m <= 12 else m - 12
                    import calendar
                    day = min(sched.day_of_month or today.day,
                              calendar.monthrange(y, m)[1])
                    sched.next_due_at = timezone.datetime(
                        y, m, day, 6, 0, tzinfo=timezone.get_current_timezone())
                else:
                    sched.next_due_at = now + timedelta(days=7)
                sched.save(update_fields=['last_created_at', 'next_due_at'])

                AuditLog.objects.create(
                    user=sched.created_by,
                    action=AuditLog.ACTION_CREATE,
                    model_name='Ticket',
                    object_id=ticket.ticket_id,
                    object_repr=ticket.title,
                    notes=f'Auto-created from recurring schedule: {sched.name}')

                self.stdout.write(self.style.SUCCESS(
                    f"  ✓ Created [{tid}] {title}"))
                created += 1

        if not dry:
            self.stdout.write(self.style.SUCCESS(
                f'\n✅  Done — {created} ticket(s) created, {skipped} already exist.'))
        else:
            self.stdout.write(f'\n[DRY RUN] {len(schedules)} schedule(s) due, {skipped} already exist.')
