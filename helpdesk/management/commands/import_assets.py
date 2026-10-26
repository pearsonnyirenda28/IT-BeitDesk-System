from django.core.management.base import BaseCommand, CommandError

from helpdesk import assetimport
from helpdesk.tabular import ImportProblem


class Command(BaseCommand):
    help = ('Import IT assets from a CSV / Excel inventory into the asset registry. '
            'Shows what WOULD be created unless --apply is given. Assets whose serial number or tag '
            'already exists are skipped; nothing already in the registry is changed.')

    def add_arguments(self, parser):
        parser.add_argument('file', help='Path to the .xlsx or .csv inventory.')
        parser.add_argument('--prefix', default='MOB-AST',
                            help='Prefix for generated asset tags, e.g. MOB-GDG -> MOB-GDG-001 (default MOB-AST).')
        parser.add_argument('--apply', action='store_true', help='Actually create the assets.')

    def handle(self, *args, **opts):
        try:
            with open(opts['file'], 'rb') as fh:
                raw = fh.read()
        except OSError as e:
            raise CommandError(f'Cannot read {opts["file"]}: {e}')
        try:
            rows, _cols, notices = assetimport.parse_inventory(opts['file'], raw)
            plan = assetimport.plan_import(rows, opts['prefix'])
        except ImportProblem as e:
            raise CommandError(str(e))

        for n in notices:
            self.stdout.write(f'  ! {n}')
        for e in plan['errors']:
            self.stdout.write(self.style.ERROR(f'  x {e}'))
        for d in plan['duplicates']:
            self.stdout.write(f"  - row {d['row']['line']} {d['row']['serial'] or d['row']['name']}: {d['reason']} (skipped)")
        for r in plan['new']:
            for w in r['warnings']:
                self.stdout.write(f"  ! row {r['line']} {r['serial']}: {w}")

        types = ', '.join(f'{n} {t}' for t, n in sorted(plan['by_type'].items())) or 'none'
        verb = 'Created' if opts['apply'] else 'Would create'
        if opts['apply']:
            created = assetimport.apply_import(plan)
            count = len(created)
        else:
            count = len(plan['new'])
        self.stdout.write(self.style.SUCCESS(
            f"\n{verb} {count} asset(s) ({types}); {len(plan['duplicates'])} skipped as already registered, "
            f"{len(plan['errors'])} invalid."))
        if not opts['apply'] and count:
            self.stdout.write(self.style.WARNING('Dry run only — re-run with --apply to create them.'))
