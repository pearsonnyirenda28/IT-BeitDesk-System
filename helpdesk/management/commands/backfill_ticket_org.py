from django.core.management.base import BaseCommand

from helpdesk import orgdata
from helpdesk.db_router import register_year_alias


class Command(BaseCommand):
    help = ('Fill the office / department / section on older tickets from each requester\'s profile. '
            'Only blank values are filled; nothing already recorded is changed. '
            'Shows what WOULD change unless --apply is given.')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually write the changes.')
        parser.add_argument('--database', action='append', dest='databases',
                            help='Database alias to process (repeatable). Default: the main database '
                                 'plus every active year database.')

    def handle(self, *args, **opts):
        aliases = opts['databases']
        if not aliases:
            aliases = ['default']
            try:
                from helpdesk.models import DatabaseYear
                for y in DatabaseYear.objects.using('default').filter(is_active=True):
                    aliases.append(register_year_alias(y.year, y.db_name))
            except Exception as e:                       # registry table missing, etc.
                self.stderr.write(f'  (could not list year databases: {e})')

        verb = 'Updated' if opts['apply'] else 'Would update'
        grand = 0
        for alias in aliases:
            try:
                r = orgdata.backfill_ticket_org(alias, apply=opts['apply'])
            except Exception as e:
                self.stderr.write(self.style.ERROR(f'  {alias}: skipped — {e}'))
                continue
            grand += r['tickets']
            self.stdout.write(f"  {alias}: {verb} {r['tickets']} ticket(s) for {r['users']} requester(s)"
                              + (f", {r['fields']} value(s) written" if opts['apply'] else ''))
        if not opts['apply'] and grand:
            self.stdout.write(self.style.WARNING('\nDry run only — re-run with --apply to make these changes.'))
        elif opts['apply']:
            self.stdout.write(self.style.SUCCESS('\nDone.'))
