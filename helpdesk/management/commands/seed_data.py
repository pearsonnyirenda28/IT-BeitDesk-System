"""
Management command: python manage.py seed_data
Seeds the database with demo data (categories, IT assets, tickets, VoIP calls, T&C, tags).

No user accounts are created. Create your administrator with
    python manage.py createsuperuser
and add everyone else from Users > Create User.
"""
import random
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone


# ── IT assets: gadget inventory (source: Inventory_Gadgets.xlsx) ─────────────────
# (asset_tag, serial_number, name, asset_type, manufacturer, purchase_date, assigned_to,
#  imei, imei2, imei3, notes)
#   * asset tags MOB-GDG-### are placeholders — rename them to your own scheme if you prefer
#   * asset_type: phones are PHONE, iPads / Galaxy Tabs are TABLET
#   * purchase_date is the spreadsheet's "Date" column; blank where the sheet had none
#   * assigned_to is the "Owner" column exactly as written (names, initials, or a section)
#   * imei / imei2 / imei3 are the IMEI column split into up to three numbers, exactly as recorded
#     (some carry a "/01" software-version suffix)
#   * manufacturer is inferred from the device name
ASSET_SEED = [
    ('MOB-GDG-001', 'RFCRA1LHSRY', 'Galaxy Z fold 3'           , 'PHONE'  , 'Samsung', '2022-06-10', 'G. Nyoni'         , '351014293379976'   , '352191243379972'   , ''                  , ''),
    ('MOB-GDG-002', 'R5CT83CCTBT', 'Galaxy Z flip'             , 'PHONE'  , 'Samsung', '2022-11-11', 'S. Moyo'          , '358975992339896'   , ''                  , ''                  , ''),
    ('MOB-GDG-003', 'R5CT845SVWA', 'Galaxy Z flip'             , 'PHONE'  , 'Samsung', '2022-11-11', 'A. Mbedzi'        , '358975992853227'   , ''                  , ''                  , ''),
    ('MOB-GDG-004', 'R5CT9IESW8A', 'Galaxy Z flip'             , 'PHONE'  , 'Samsung', '2022-11-11', 'D. Siphuma'       , '358975993062349'   , ''                  , ''                  , ''),
    ('MOB-GDG-005', 'R5CT8392YCJ', 'Galaxy Z flip'             , 'PHONE'  , 'Samsung', '2022-11-11', 'TC'               , '358975992236753'   , ''                  , ''                  , ''),
    ('MOB-GDG-006', 'RZ8T216LTNV', 'Galaxy Phone'              , 'PHONE'  , 'Samsung', '2022-11-11', 'R. Ndou'          , '352353594241884'   , '356940514241881'   , ''                  , ''),
    ('MOB-GDG-007', '6Y7BML3'    , 'Dell laptop'               , 'LAPTOP' , 'Dell'   , '2022-11-11', 'C. Muleya'        , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-008', 'RFCRC0ARDQZ', 'Galaxy Z fold 3'           , 'PHONE'  , 'Samsung', '2022-01-14', 'T. Mahacih'       , '353249144529589'   , ''                  , ''                  , ''),
    ('MOB-GDG-009', 'RFCRC0DJQCK', 'Galaxy Z fold 3'           , 'PHONE'  , 'Samsung', '2022-01-14', 'A. Tore'          , '353249144596265'   , ''                  , ''                  , ''),
    ('MOB-GDG-010', 'RFCRC0BYNOP', 'Galaxy Z fold 3'           , 'PHONE'  , 'Samsung', '2022-01-14', 'D. Muleya'        , '353249144555113'   , ''                  , ''                  , ''),
    ('MOB-GDG-011', 'RFCRC0DJPSL', 'Galaxy Z fold 3'           , 'PHONE'  , 'Samsung', '2022-01-14', 'M. Chitsunge'     , '353249144595861'   , ''                  , ''                  , ''),
    ('MOB-GDG-012', 'RFCRBOG3MGX', 'Galaxy Z fold 3'           , 'PHONE'  , 'Samsung', '2022-06-09', 'M.Ncube'          , '351014293597858'   , '352191243597854'   , ''                  , ''),
    ('MOB-GDG-013', 'CND14827RC' , 'HP laptop'                 , 'LAPTOP' , 'HP'     , '2022-06-01', 'Mpala'            , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-014', 'LXY3QK43Y2' , 'Ipad pro 11-inch'          , 'TABLET' , 'Apple'  , '2023-05-26', 'G. Nyoni'         , '359817810240741'   , ''                  , ''                  , ''),
    ('MOB-GDG-015', '5CG32903KS' , 'HP laptop'                 , 'LAPTOP' , 'HP'     , '2023-08-29', 'T. Kadyamusuma'   , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-016', '6W7BML3'    , 'Laptop (name not recorded)', 'LAPTOP' , ''       , '2022-11-22', 'P. Muchena'       , ''                  , ''                  , ''                  , 'Device name was blank in the source inventory.'),
    ('MOB-GDG-017', 'R5CW60Z8J2Z', 'Galaxy Z flip 4'           , 'PHONE'  , 'Samsung', '2023-09-29', 'TC'               , '358975994351527'   , '359697854351525'   , ''                  , ''),
    ('MOB-GDG-018', 'RR8W20SHSDF', 'Galaxy A23'                , 'PHONE'  , 'Samsung', '2023-11-15', 'Fire'             , '359142451784208'   , '359440621784201'   , ''                  , ''),
    ('MOB-GDG-019', 'CND317333V' , 'HP Elitebook'              , 'LAPTOP' , 'HP'     , ''          , 'A. Mbedzi'        , '355444403245294'   , ''                  , ''                  , ''),
    ('MOB-GDG-020', 'F8Y9RV3'    , 'Dell Latitude 7440'        , 'LAPTOP' , 'Dell'   , ''          , 'M. Chirume'       , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-021', 'CND317333S' , 'HP Elitebook'              , 'LAPTOP' , 'HP'     , ''          , 'S. Moyo'          , '355444403245617'   , ''                  , ''                  , ''),
    ('MOB-GDG-022', 'CND317333T' , 'HP Elitebook'              , 'LAPTOP' , 'HP'     , ''          , 'D. M. Siphuma'    , '355444403246490'   , ''                  , ''                  , ''),
    ('MOB-GDG-023', '5CG3200WW0' , 'HP Elitebook'              , 'LAPTOP' , 'HP'     , ''          , 'TC L. Ramakgapola', '359869746739296'   , ''                  , ''                  , ''),
    ('MOB-GDG-024', '9F57TV3'    , 'Dell Latitude 7440'        , 'LAPTOP' , 'Dell'   , ''          , 'D. Moyo'          , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-025', 'F2C55X3'    , 'Dell Latitude 7440'        , 'LAPTOP' , 'Dell'   , ''          , 'F. Mkandla'       , '352201850133370'   , ''                  , ''                  , ''),
    ('MOB-GDG-026', 'DSSLCY3'    , 'Dell Latitude 7440'        , 'LAPTOP' , 'Dell'   , '2023-09-19', 'O. Munyai'        , '352201851682516'   , ''                  , ''                  , ''),
    ('MOB-GDG-027', 'R52W808YYNB', 'Galaxy Tab59 5G'           , 'TABLET' , 'Samsung', '2024-01-10', 'T. Mahacih'       , '358277320714563/01', '358922670714560/01', ''                  , ''),
    ('MOB-GDG-028', 'R52W808YZFM', 'Galaxy Tab59 5G'           , 'TABLET' , 'Samsung', '2024-01-10', 'P. Pirato'        , '35827732074829/01' , '358922670714826/01', ''                  , ''),
    ('MOB-GDG-029', 'R52W808ZOHM', 'Galaxy Tab59 5G'           , 'TABLET' , 'Samsung', '2024-01-10', 'E. Takutaku'      , '358277320715172/01', '358922670715179/01', '352771321069867/5' , ''),
    ('MOB-GDG-030', 'R52W062PSH' , 'Galaxy Tab59 5G'           , 'TABLET' , 'Samsung', '2024-01-10', 'A. Tauya'         , '35827732668675/01' , '358922670698672/01', '3582771321069867/5', ''),
    ('MOB-GDG-031', 'R52W8062QKN', 'Galaxy Tab59 5G'           , 'TABLET' , 'Samsung', '2024-01-10', 'P. Shumba'        , '358277326699145/01', '358922670699142/01', ''                  , ''),
    ('MOB-GDG-032', 'R52W8062QIT', 'Galaxy Tab59 5G'           , 'TABLET' , 'Samsung', '2024-01-10', 'B. Maworere'      , '358277320698964/01', '358922676698961/01', ''                  , ''),
    ('MOB-GDG-033', 'R52W808YZ3N', 'Galaxy Tab59 5G'           , 'TABLET' , 'Samsung', '2024-01-10', 'J. Manatsa'       , '358277320714904/01', '358922670714701/01', ''                  , ''),
    ('MOB-GDG-034', 'R52W808YY4F', 'Galaxy Tab59 5G'           , 'TABLET' , 'Samsung', '2024-01-10', 'Mary'             , '358277320714381/01', '358922670714388/01', ''                  , ''),
    ('MOB-GDG-035', 'RR8W205H5TF', 'Galaxy A23'                , 'PHONE'  , 'Samsung', '2024-02-14', 'G. Maseko'        , '359142451784208/01', '359440621784201/01', ''                  , ''),
    ('MOB-GDG-036', 'R5CX239XDWT', 'Galaxy S24 Ultra'          , 'PHONE'  , 'Samsung', '2024-06-04', 'S. Moyo'          , '353607430499801'   , '356299190499804'   , ''                  , ''),
    ('MOB-GDG-037', 'CW624KJD40' , 'Iphone15Pro Max'           , 'PHONE'  , 'Apple'  , '2024-06-04', 'A. Mbedzi'        , '350814295131148'   , '350814295202337'   , ''                  , ''),
    ('MOB-GDG-038', 'K9J6L97WWV' , 'Iphone 15 Pro Max'         , 'PHONE'  , 'Apple'  , '2024-06-04', 'D. M. Siphuma'    , '356642150159258'   , '356642150171758'   , ''                  , ''),
    ('MOB-GDG-039', '8PN7Z64'    , 'Dell Inspiron 7440'        , 'LAPTOP' , 'Dell'   , '2025-02-03', 'R. Ndou'          , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-040', '5CD4487K59' , 'HP Intel Core Ultra7'      , 'LAPTOP' , 'HP'     , '2025-02-03', 'A. Sibindi'       , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-041', 'D6K7S14'    , 'Dell Inspiron 7440'        , 'LAPTOP' , 'Dell'   , '2025-02-03', 'A. Nyoni'         , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-042', '8CC5270F8C' , 'HP All-IN-One'             , 'DESKTOP', 'HP'     , '2025-03-03', 'V. Moyo'          , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-043', 'VNY522035W' , 'HP Omnibook X FLIP'        , 'LAPTOP' , 'HP'     , '2025-12-19', 'S. Moyo'          , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-044', 'VNY5199WPR' , 'HP Omnibook X FLIP'        , 'LAPTOP' , 'HP'     , '2025-12-19', 'D. M. Siphuma'    , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-045', 'VNY53604DQ' , 'HP Omnibook X FLIP'        , 'LAPTOP' , 'HP'     , '2025-12-19', 'D. Moyo'          , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-046', '8CC52270D9R', 'HP All-IN-One'             , 'DESKTOP', 'HP'     , '2026-01-13', 'Procurement'      , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-047', 'CNCRT2P0N3' , 'HP LaserJet Pro MFP4103dw' , 'PRINTER', 'HP'     , '2026-01-13', 'Procurement'      , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-048', 'IHF5360GMP' , 'HP Desktop All-IN-One'     , 'DESKTOP', 'HP'     , '2026-04-16', 'Accounts_Clerk'   , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-049', 'IHF5340NMD' , 'HP Desktop All-IN-One'     , 'DESKTOP', 'HP'     , '2026-04-16', 'Accounts_Clerk'   , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-050', '8CC5122SY4' , 'HP Desktop All-IN-One'     , 'DESKTOP', 'HP'     , '2026-04-16', 'Accounts_Clerk'   , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-051', 'IH86102P39' , 'HP Laptop Ultra7'          , 'LAPTOP' , 'HP'     , '2026-05-06', 'GIS'              , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-052', '5CG6102173' , 'HP Xflip Ultra 7'          , 'LAPTOP' , 'HP'     , '2026-05-06', 'TC L. Ramakgapola', ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-053', 'RRGL404C02V', 'Galaxy Z Fold 7'           , 'PHONE'  , 'Samsung', '2025-06-23', 'D. M. Siphuma'    , '356335510529910'   , '356696140529913'   , ''                  , ''),
    ('MOB-GDG-054', 'RRGL30EDCKM', 'Galaxy Z Fold 7'           , 'PHONE'  , 'Samsung', '2025-06-23', 'TC L. Ramakgapola', '356335510513120'   , '356696140513123'   , ''                  , ''),
    ('MOB-GDG-055', 'RRGL209YOCZ', 'Galaxy Z Fold 7'           , 'PHONE'  , 'Samsung', '2025-06-23', 'S. Moyo'          , '356335510463342'   , '356696140463345'   , ''                  , ''),
    ('MOB-GDG-056', 'RRGL30EDFKA', 'Galaxy Z Fold 7'           , 'PHONE'  , 'Samsung', '2025-06-23', ''                 , '356335510514110'   , '356696140514113'   , ''                  , ''),
    ('MOB-GDG-057', '8CG3411WY7' , 'HP Pavilion'               , 'LAPTOP' , 'HP'     , '2024-04-09', 'A. Mbedzi'        , ''                  , ''                  , ''                  , ''),
    ('MOB-GDG-058', 'K73K3HT661' , 'Iphone 16 Pro max'         , 'PHONE'  , 'Apple'  , '2024-11-15', 'TC'               , '358798968528675'   , '358798962168370'   , ''                  , ''),
    ('MOB-GDG-059', '1HF5260X77' , 'HP All-IN-One'             , 'DESKTOP', 'HP'     , '2026-04-16', 'Housing_Revenue'  , ''                  , ''                  , ''                  , ''),
]


class Command(BaseCommand):
    help = 'Seeds BeitDesk with categories, IT assets, demo tickets, VoIP calls, T&C and tags (no users)'

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('\n🚀  Seeding NexusDesk demo data...\n'))
        self._create_categories()
        self._create_assets()
        self._create_voip_providers()
        self._create_extensions()
        self._create_tickets()
        self._create_voip_calls()
        self.stdout.write(self.style.SUCCESS(
            '\n✅  Seed complete! No users were created — run  python manage.py createsuperuser  '
            'to make your administrator.\n'))

    # ── IT assets ────────────────────────────────────────────────────────────
    def _create_assets(self):
        from datetime import date
        from helpdesk.models import Asset

        created = skipped = 0
        for tag, serial, name, atype, maker, bought, owner, imei, imei2, imei3, notes in ASSET_SEED:
            # Safe to re-run: skip anything whose serial number or tag is already registered
            if (Asset.objects.filter(serial_number__iexact=serial).exists()
                    or Asset.objects.filter(asset_tag__iexact=tag).exists()):
                skipped += 1
                continue
            Asset.objects.create(
                asset_tag=tag, name=name, asset_type=atype, status='ACTIVE',
                serial_number=serial, imei=imei, imei2=imei2, imei3=imei3,
                manufacturer=maker, assigned_to_name=owner,
                purchase_date=date.fromisoformat(bought) if bought else None,
                notes=notes,
            )
            created += 1
        msg = f'  ✓ {created} assets created'
        if skipped:
            msg += f' ({skipped} already registered, skipped)'
        self.stdout.write(self.style.SUCCESS(msg))

    # ── Categories ───────────────────────────────────────────────────────────
    def _create_categories(self):
        from helpdesk.models import Category
        cats = [
            ('Network / Connectivity', 'fas fa-network-wired', '#1f6feb'),
            ('Hardware',               'fas fa-desktop',       '#3fb950'),
            ('Software / Applications','fas fa-code',          '#bc8cff'),
            ('Email / Communication',  'fas fa-envelope',      '#58a6ff'),
            ('Security / Access',      'fas fa-shield-halved', '#f85149'),
            ('Printers / Peripherals', 'fas fa-print',         '#d29922'),
            ('VoIP / Telephony',       'fas fa-phone',         '#79c0ff'),
            ('User Account',           'fas fa-user-circle',   '#ffa657'),
        ]
        for name, icon, color in cats:
            Category.objects.get_or_create(name=name, defaults={'icon': icon, 'color': color})
        self.stdout.write(self.style.SUCCESS('  ✓ Categories done'))

    # ── VoIP ─────────────────────────────────────────────────────────────────
    def _create_voip_providers(self):
        from voip.models import VoIPProvider
        VoIPProvider.objects.get_or_create(
            name='Asterisk PBX',
            defaults={'host': '192.168.1.10', 'port': 5060, 'protocol': 'SIP', 'is_active': True}
        )
        self.stdout.write(self.style.SUCCESS('  ✓ VoIP provider done'))

    def _create_extensions(self):
        from voip.models import Extension, VoIPProvider
        provider = VoIPProvider.objects.first()
        agents = User.objects.filter(is_staff=True)
        ext_num = 4001
        for agent in agents:
            if not hasattr(agent, 'extension'):
                Extension.objects.get_or_create(
                    extension_number=str(ext_num),
                    defaults={
                        'user': agent,
                        'display_name': agent.get_full_name() or agent.username,
                        'department': 'IT Help Desk',
                        'voip_provider': provider,
                    }
                )
                ext_num += 1
        self.stdout.write(self.style.SUCCESS('  ✓ Extensions done'))

    # ── Tickets ───────────────────────────────────────────────────────────────
    def _create_tickets(self):
        from helpdesk.models import Ticket, Category

        if Ticket.objects.count() > 5:
            self.stdout.write('  ℹ  Tickets already exist, skipping')
            return

        agents  = list(User.objects.filter(is_staff=True))
        cats    = list(Category.objects.all())
        now     = timezone.now()

        samples = [
            ('Cannot connect to VPN from home',             'HIGH',     'OPEN',        'NETWORK / CONNECTIVITY'),
            ('Outlook crashes on startup',                  'MEDIUM',   'IN_PROGRESS', 'SOFTWARE / APPLICATIONS'),
            ('Laptop screen flickering after update',       'HIGH',     'PENDING',     'HARDWARE'),
            ('Printer offline — Finance floor',             'MEDIUM',   'OPEN',        'PRINTERS / PERIPHERALS'),
            ('New user account setup for Chipo Mhuri',     'LOW',      'RESOLVED',    'USER ACCOUNT'),
            ('Wi-Fi drops every 30 minutes in boardroom',   'CRITICAL', 'IN_PROGRESS', 'NETWORK / CONNECTIVITY'),
            ('Microsoft Teams audio not working',           'MEDIUM',   'OPEN',        'VOIP / TELEPHONY'),
            ('Ransomware alert on workstation WS-042',      'CRITICAL', 'IN_PROGRESS', 'SECURITY / ACCESS'),
            ('Password reset for nduna@company.co.zw',     'LOW',      'RESOLVED',    'USER ACCOUNT'),
            ('Email delivery delays to external domains',   'HIGH',     'PENDING',     'EMAIL / COMMUNICATION'),
            ('Monitor not detected after desk move',        'LOW',      'RESOLVED',    'HARDWARE'),
            ('ERP login page showing 503 error',            'CRITICAL', 'OPEN',        'SOFTWARE / APPLICATIONS'),
            ('USB ports disabled on reception PC',          'LOW',      'CLOSED',      'HARDWARE'),
            ('Need Adobe Acrobat licence for new hire',     'LOW',      'PENDING',     'SOFTWARE / APPLICATIONS'),
            ('Internet very slow — ops department',         'HIGH',     'IN_PROGRESS', 'NETWORK / CONNECTIVITY'),
        ]

        requesters = [
            ('Tendai Moyo',     'tendai@company.co.zw',     '+263773001001', 'Finance'),
            ('Rutendo Chikwava', 'rutendo@company.co.zw',   '+263773001002', 'Operations'),
            ('Farai Dube',      'farai@company.co.zw',      '+263773001003', 'HR'),
            ('Blessed Mutema',  'blessed@company.co.zw',    '+263773001004', 'Management'),
            ('Simba Ncube',     'simba@company.co.zw',      '+263773001005', 'Sales'),
        ]
        sources = ['WEB', 'EMAIL', 'PHONE', 'VOIP', 'WALK_IN']

        for i, (title, priority, status, cat_name) in enumerate(samples):
            cat = next((c for c in cats if cat_name.lower() in c.name.lower()), cats[0])
            req = requesters[i % len(requesters)]
            created_at = now - timedelta(days=random.randint(0, 30), hours=random.randint(0, 23))
            t = Ticket(
                title=title,
                description=f'Issue reported by {req[0]}.\n\nDetails: User is experiencing "{title.lower()}". '
                             f'Please investigate and resolve as soon as possible.\n\nEnvironment: Windows 11, Office 365.',
                category=cat,
                priority=priority,
                status=status,
                source=sources[i % len(sources)],
                requester_name=req[0],
                requester_email=req[1],
                requester_phone=req[2],
                requester_department=req[3],
                assigned_to=agents[i % len(agents)] if agents else None,
            )
            t.save()
            # Back-date created_at via update to bypass auto_now_add
            Ticket.objects.filter(pk=t.pk).update(created_at=created_at)

        self.stdout.write(self.style.SUCCESS(f'  ✓ {len(samples)} tickets created'))

    # ── VoIP Calls ────────────────────────────────────────────────────────────
    def _create_voip_calls(self):
        from voip.models import VoIPCall, Extension, CallerProfile

        if VoIPCall.objects.count() > 5:
            self.stdout.write('  ℹ  VoIP calls already exist, skipping')
            return

        agents = list(User.objects.filter(is_staff=True))
        extensions = list(Extension.objects.all())
        now = timezone.now()

        caller_data = [
            ('+263771100001', 'Tendai Moyo'),
            ('+263771100002', 'Rutendo Chikwava'),
            ('+263772200001', 'Farai Dube'),
            ('+263772200002', 'Blessed Mutema'),
            ('+263773300001', 'Simba Ncube'),
            ('+263773300002', 'Chipo Mhuri'),
            ('+263771100003', None),
            ('+263712345678', 'External Caller'),
        ]

        statuses = ['COMPLETED', 'COMPLETED', 'COMPLETED', 'MISSED', 'COMPLETED', 'VOICEMAIL']
        directions = ['INBOUND', 'INBOUND', 'OUTBOUND', 'INBOUND', 'INTERNAL', 'INBOUND']

        calls_created = 0
        for i in range(20):
            caller_num, caller_name = caller_data[i % len(caller_data)]
            status = statuses[i % len(statuses)]
            direction = directions[i % len(directions)]
            talk_secs = random.randint(30, 900) if status == 'COMPLETED' else 0
            initiated = now - timedelta(days=random.randint(0, 7), hours=random.randint(0, 12), minutes=random.randint(0, 59))
            answered = initiated + timedelta(seconds=random.randint(3, 20)) if status not in ['MISSED', 'VOICEMAIL'] else None
            ended = answered + timedelta(seconds=talk_secs) if answered else None
            dest_ext = extensions[i % len(extensions)] if extensions else None

            call = VoIPCall.objects.create(
                caller_number=caller_num,
                caller_name=caller_name or '',
                destination_number=dest_ext.extension_number if dest_ext else '4001',
                destination_extension=dest_ext,
                direction=direction,
                status=status,
                initiated_at=initiated,
                answered_at=answered,
                ended_at=ended,
                answered_by=agents[i % len(agents)] if agents and answered else None,
                talk_duration_seconds=talk_secs,
                notes='Demo call entry' if i % 3 == 0 else '',
            )

            # Update/create caller profile
            profile, _ = CallerProfile.objects.get_or_create(phone_number=caller_num)
            if caller_name and not profile.name:
                profile.name = caller_name
            profile.total_calls += 1
            profile.last_call = initiated
            profile.save()
            calls_created += 1

        self.stdout.write(self.style.SUCCESS(f'  ✓ {calls_created} VoIP calls created'))

        # ── Terms & Conditions ────────────────────────────────────────────────
        from accounts.models import TermsVersion
        if not TermsVersion.objects.filter(is_current=True).exists():
            from django.utils import timezone as tz
            admin_user = User.objects.filter(is_superuser=True).first()
            today = tz.now().strftime('%d %B %Y')
            TermsVersion.objects.create(
                version='1.0',
                title='BeitDesk System Terms & Conditions',
                is_current=True,
                effective_date=tz.now().date(),
                created_by=admin_user,
                content=f"""MUNICIPALITY OF BEITBRIDGE — IT HELP DESK SYSTEM
TERMS AND CONDITIONS OF USE — Version 1.0
Effective Date: {today}

1. SYSTEM OVERVIEW
BeitDesk is the official IT Help Desk and Asset Management System of the Municipality of Beitbridge. By logging in you agree to these Terms and Conditions.

2. AUTHORISED USE
2.1 Access is granted exclusively to authorised employees, contractors, and agents of the Municipality of Beitbridge.
2.2 You must use only your own login credentials. Sharing your username or password is strictly prohibited.
2.3 This system must be used only for legitimate municipal IT support and asset management purposes.
2.4 Unauthorised access or misuse constitutes a disciplinary offence and may result in criminal prosecution under the Zimbabwean Cyber and Data Protection Act [Chapter 12:07].

3. DATA PRIVACY AND PROTECTION
3.1 This system collects personal data (names, email addresses, phone numbers, department information) solely for IT support purposes.
3.2 All data is processed in accordance with the Cyber and Data Protection Act [Chapter 12:07] of Zimbabwe.
3.3 Your activity within this system is fully logged and audited. Every action you perform — including ticket creation, status changes, comments, and logins — is recorded with a timestamp and IP address.
3.4 Data stored in this system is the property of the Municipality of Beitbridge and must not be shared externally without authorisation from the IT Manager or Municipal Director.

4. SENSITIVE DATA
4.1 Do NOT include classified government information, financial account numbers, banking details, or national ID numbers in ticket descriptions or comments.
4.2 Ticket data may be reviewed by IT staff and administrators for support, reporting, and audit purposes.
4.3 Support tickets may be used for IT trend analysis and service improvement planning.

5. MULTI-FACTOR AUTHENTICATION (MFA)
5.1 The Municipality of Beitbridge strongly recommends that all users enable Multi-Factor Authentication (MFA) to protect their accounts and municipal data.
5.2 Two MFA methods are available: Face Recognition (browser-based, no data leaves your device) and Time-Based One-Time Password (TOTP) via Google Authenticator, Authy, or Microsoft Authenticator.
5.3 Users may enable or disable MFA at any time from the Security section in their account settings.
5.4 Face biometric embeddings are stored securely on the municipal server only and are never transmitted to or shared with any third party.
5.5 The Municipality of Beitbridge accepts no liability for unauthorised account access where MFA was available but not enabled by the user.
5.6 After 5 consecutive failed face recognition attempts, your account will be temporarily locked for 15 minutes. Contact the IT Help Desk to unlock immediately.

6. IT ASSET REGISTRY
6.1 IT assets assigned to you remain the property of the Municipality of Beitbridge at all times.
6.2 You are personally responsible for the care, security, and safekeeping of any IT equipment assigned to you.
6.3 Loss, damage, theft, or unauthorised transfer of municipal IT assets must be reported immediately to the IT Help Desk by raising a ticket.
6.4 Asset tags must not be removed or defaced.

7. KNOWLEDGE BASE
7.1 Knowledge Base articles are internal IT guidance documents provided to help staff resolve common IT issues.
7.2 Do not share Knowledge Base content externally without IT Manager authorisation.
7.3 Only authorised IT staff may create, edit, or delete Knowledge Base articles.

8. SYSTEM AVAILABILITY
8.1 BeitDesk is maintained on a best-effort basis during business hours: 08:00–17:00, Monday to Friday (excluding public holidays).
8.2 Planned maintenance will be communicated in advance where possible. Emergency maintenance may occur without prior notice.

9. PROHIBITED ACTIONS
You must NOT:
- Attempt to access other users' accounts, tickets, or data without authorisation
- Store, share, or distribute offensive, illegal, or inappropriate content through this system
- Attempt to circumvent, disable, or bypass security controls or audit logging
- Use automated scripts, bots, or tools to interact with this system without IT Manager approval
- Extract, copy, or share bulk data from the system for any unauthorised purpose
- Install unauthorised software using IT department resources

10. CONSEQUENCES OF VIOLATION
Violations of these Terms may result in:
- Immediate suspension or permanent revocation of system access
- Disciplinary action in accordance with the Municipal Human Resources Policy
- Civil or criminal prosecution where applicable under Zimbabwean law, including the Cyber and Data Protection Act [Chapter 12:07]

11. UPDATES TO THESE TERMS
The Municipality of Beitbridge reserves the right to update these Terms and Conditions at any time. When a new version is published, all users will be required to read and re-accept the updated Terms on their next login before accessing the system.

12. GOVERNING LAW
These Terms are governed by the laws of Zimbabwe. Any disputes shall be subject to the jurisdiction of the courts of Zimbabwe.

13. CONTACT
For questions about these Terms and Conditions, contact:
IT Department — Municipality of Beitbridge
Beitbridge, Matabeleland South, Zimbabwe

By clicking "I Accept — Enter BeitDesk" you confirm that you have read, fully understood, and agree to be legally bound by these Terms and Conditions of Use."""
            )
            self.stdout.write(self.style.SUCCESS('  ✓ T&C v1.0 seeded'))
        else:
            self.stdout.write('  ℹ  T&C already exists, skipping')

        # ── Tags ─────────────────────────────────────────────────────────────
        from helpdesk.models import Tag
        DEFAULT_TAGS = [
            ('waiting-on-user',  '#f59e0b','Waiting for user to respond'),
            ('waiting-on-vendor','#f97316','Waiting for external vendor'),
            ('network-outage',   '#ef4444','Affects network connectivity'),
            ('vip',             '#c9a227','High-priority user — escalate'),
            ('recurring',       '#8b5cf6','Issue happens repeatedly'),
            ('hardware-fault',  '#64748b','Physical hardware failing'),
            ('software-bug',    '#3b82f6','Software defect or bug'),
            ('access-rights',   '#06b6d4','Permissions change needed'),
            ('planned-work',    '#22c55e','Scheduled maintenance'),
            ('training',        '#10b981','User needs guidance'),
            ('finance-dept',    '#a78bfa','Finance Department request'),
            ('revenue-dept',    '#f472b6','Revenue Department request'),
            ('data-loss',       '#dc2626','Risk or actual data loss'),
            ('workaround',      '#6b7280','Temporary fix applied'),
        ]
        tag_count = 0
        for name, color, desc in DEFAULT_TAGS:
            _, made = Tag.objects.get_or_create(
                name=name, defaults={'color':color,'description':desc})
            if made:
                tag_count += 1
        if tag_count:
            self.stdout.write(self.style.SUCCESS(f'  ✓ {tag_count} tags seeded'))
        else:
            self.stdout.write('  ℹ  Tags already exist, skipping')

        # Apply some demo tags to existing tickets
        try:
            tickets = Ticket.objects.all()[:6]
            tag_names = ['waiting-on-user','hardware-fault','software-bug',
                         'vip','network-outage','training']
            for i, ticket in enumerate(tickets):
                tname = tag_names[i % len(tag_names)]
                tag = Tag.objects.filter(name=tname).first()
                if tag:
                    ticket.tags.add(tag)
        except Exception:
            pass
