"""
Load the gadget inventory (Inventory_Gadgets.xlsx) into the IT asset registry.

* Runs once, with `python manage.py migrate`, and only against the main database.
* Never adds a duplicate: a gadget is skipped when its serial number is already registered.
* Gadgets that an earlier version of the seed data created (tags MOB-GDG-###) are brought up to
  date: IMEI numbers move into the new IMEI columns, and tablets get the Tablet type.
* The rows are frozen in this file on purpose, so later edits to seed_data.py cannot change it.
* Skipped automatically while the test suite is running, so test databases stay empty.
* Reversing it leaves the assets in place — it never deletes registry data.
"""
import os
import sys
from datetime import date

from django.db import migrations

# (asset_tag, serial_number, name, asset_type, manufacturer, purchase_date, assigned_to,
#  imei, imei2, imei3, notes)
GADGETS = [
    ('MOB-GDG-001', 'RFCRA1LHSRY', 'Galaxy Z fold 3', 'PHONE', 'Samsung', '2022-06-10', 'G. Nyoni', '351014293379976', '352191243379972', '', ''),
    ('MOB-GDG-002', 'R5CT83CCTBT', 'Galaxy Z flip', 'PHONE', 'Samsung', '2022-11-11', 'S. Moyo', '358975992339896', '', '', ''),
    ('MOB-GDG-003', 'R5CT845SVWA', 'Galaxy Z flip', 'PHONE', 'Samsung', '2022-11-11', 'A. Mbedzi', '358975992853227', '', '', ''),
    ('MOB-GDG-004', 'R5CT9IESW8A', 'Galaxy Z flip', 'PHONE', 'Samsung', '2022-11-11', 'D. Siphuma', '358975993062349', '', '', ''),
    ('MOB-GDG-005', 'R5CT8392YCJ', 'Galaxy Z flip', 'PHONE', 'Samsung', '2022-11-11', 'TC', '358975992236753', '', '', ''),
    ('MOB-GDG-006', 'RZ8T216LTNV', 'Galaxy Phone', 'PHONE', 'Samsung', '2022-11-11', 'R. Ndou', '352353594241884', '356940514241881', '', ''),
    ('MOB-GDG-007', '6Y7BML3', 'Dell laptop', 'LAPTOP', 'Dell', '2022-11-11', 'C. Muleya', '', '', '', ''),
    ('MOB-GDG-008', 'RFCRC0ARDQZ', 'Galaxy Z fold 3', 'PHONE', 'Samsung', '2022-01-14', 'T. Mahacih', '353249144529589', '', '', ''),
    ('MOB-GDG-009', 'RFCRC0DJQCK', 'Galaxy Z fold 3', 'PHONE', 'Samsung', '2022-01-14', 'A. Tore', '353249144596265', '', '', ''),
    ('MOB-GDG-010', 'RFCRC0BYNOP', 'Galaxy Z fold 3', 'PHONE', 'Samsung', '2022-01-14', 'D. Muleya', '353249144555113', '', '', ''),
    ('MOB-GDG-011', 'RFCRC0DJPSL', 'Galaxy Z fold 3', 'PHONE', 'Samsung', '2022-01-14', 'M. Chitsunge', '353249144595861', '', '', ''),
    ('MOB-GDG-012', 'RFCRBOG3MGX', 'Galaxy Z fold 3', 'PHONE', 'Samsung', '2022-06-09', 'M.Ncube', '351014293597858', '352191243597854', '', ''),
    ('MOB-GDG-013', 'CND14827RC', 'HP laptop', 'LAPTOP', 'HP', '2022-06-01', 'Mpala', '', '', '', ''),
    ('MOB-GDG-014', 'LXY3QK43Y2', 'Ipad pro 11-inch', 'TABLET', 'Apple', '2023-05-26', 'G. Nyoni', '359817810240741', '', '', ''),
    ('MOB-GDG-015', '5CG32903KS', 'HP laptop', 'LAPTOP', 'HP', '2023-08-29', 'T. Kadyamusuma', '', '', '', ''),
    ('MOB-GDG-016', '6W7BML3', 'Laptop (name not recorded)', 'LAPTOP', '', '2022-11-22', 'P. Muchena', '', '', '', 'Device name was blank in the source inventory.'),
    ('MOB-GDG-017', 'R5CW60Z8J2Z', 'Galaxy Z flip 4', 'PHONE', 'Samsung', '2023-09-29', 'TC', '358975994351527', '359697854351525', '', ''),
    ('MOB-GDG-018', 'RR8W20SHSDF', 'Galaxy A23', 'PHONE', 'Samsung', '2023-11-15', 'Fire', '359142451784208', '359440621784201', '', ''),
    ('MOB-GDG-019', 'CND317333V', 'HP Elitebook', 'LAPTOP', 'HP', '', 'A. Mbedzi', '355444403245294', '', '', ''),
    ('MOB-GDG-020', 'F8Y9RV3', 'Dell Latitude 7440', 'LAPTOP', 'Dell', '', 'M. Chirume', '', '', '', ''),
    ('MOB-GDG-021', 'CND317333S', 'HP Elitebook', 'LAPTOP', 'HP', '', 'S. Moyo', '355444403245617', '', '', ''),
    ('MOB-GDG-022', 'CND317333T', 'HP Elitebook', 'LAPTOP', 'HP', '', 'D. M. Siphuma', '355444403246490', '', '', ''),
    ('MOB-GDG-023', '5CG3200WW0', 'HP Elitebook', 'LAPTOP', 'HP', '', 'TC L. Ramakgapola', '359869746739296', '', '', ''),
    ('MOB-GDG-024', '9F57TV3', 'Dell Latitude 7440', 'LAPTOP', 'Dell', '', 'D. Moyo', '', '', '', ''),
    ('MOB-GDG-025', 'F2C55X3', 'Dell Latitude 7440', 'LAPTOP', 'Dell', '', 'F. Mkandla', '352201850133370', '', '', ''),
    ('MOB-GDG-026', 'DSSLCY3', 'Dell Latitude 7440', 'LAPTOP', 'Dell', '2023-09-19', 'O. Munyai', '352201851682516', '', '', ''),
    ('MOB-GDG-027', 'R52W808YYNB', 'Galaxy Tab59 5G', 'TABLET', 'Samsung', '2024-01-10', 'T. Mahacih', '358277320714563/01', '358922670714560/01', '', ''),
    ('MOB-GDG-028', 'R52W808YZFM', 'Galaxy Tab59 5G', 'TABLET', 'Samsung', '2024-01-10', 'P. Pirato', '35827732074829/01', '358922670714826/01', '', ''),
    ('MOB-GDG-029', 'R52W808ZOHM', 'Galaxy Tab59 5G', 'TABLET', 'Samsung', '2024-01-10', 'E. Takutaku', '358277320715172/01', '358922670715179/01', '352771321069867/5', ''),
    ('MOB-GDG-030', 'R52W062PSH', 'Galaxy Tab59 5G', 'TABLET', 'Samsung', '2024-01-10', 'A. Tauya', '35827732668675/01', '358922670698672/01', '3582771321069867/5', ''),
    ('MOB-GDG-031', 'R52W8062QKN', 'Galaxy Tab59 5G', 'TABLET', 'Samsung', '2024-01-10', 'P. Shumba', '358277326699145/01', '358922670699142/01', '', ''),
    ('MOB-GDG-032', 'R52W8062QIT', 'Galaxy Tab59 5G', 'TABLET', 'Samsung', '2024-01-10', 'B. Maworere', '358277320698964/01', '358922676698961/01', '', ''),
    ('MOB-GDG-033', 'R52W808YZ3N', 'Galaxy Tab59 5G', 'TABLET', 'Samsung', '2024-01-10', 'J. Manatsa', '358277320714904/01', '358922670714701/01', '', ''),
    ('MOB-GDG-034', 'R52W808YY4F', 'Galaxy Tab59 5G', 'TABLET', 'Samsung', '2024-01-10', 'Mary', '358277320714381/01', '358922670714388/01', '', ''),
    ('MOB-GDG-035', 'RR8W205H5TF', 'Galaxy A23', 'PHONE', 'Samsung', '2024-02-14', 'G. Maseko', '359142451784208/01', '359440621784201/01', '', ''),
    ('MOB-GDG-036', 'R5CX239XDWT', 'Galaxy S24 Ultra', 'PHONE', 'Samsung', '2024-06-04', 'S. Moyo', '353607430499801', '356299190499804', '', ''),
    ('MOB-GDG-037', 'CW624KJD40', 'Iphone15Pro Max', 'PHONE', 'Apple', '2024-06-04', 'A. Mbedzi', '350814295131148', '350814295202337', '', ''),
    ('MOB-GDG-038', 'K9J6L97WWV', 'Iphone 15 Pro Max', 'PHONE', 'Apple', '2024-06-04', 'D. M. Siphuma', '356642150159258', '356642150171758', '', ''),
    ('MOB-GDG-039', '8PN7Z64', 'Dell Inspiron 7440', 'LAPTOP', 'Dell', '2025-02-03', 'R. Ndou', '', '', '', ''),
    ('MOB-GDG-040', '5CD4487K59', 'HP Intel Core Ultra7', 'LAPTOP', 'HP', '2025-02-03', 'A. Sibindi', '', '', '', ''),
    ('MOB-GDG-041', 'D6K7S14', 'Dell Inspiron 7440', 'LAPTOP', 'Dell', '2025-02-03', 'A. Nyoni', '', '', '', ''),
    ('MOB-GDG-042', '8CC5270F8C', 'HP All-IN-One', 'DESKTOP', 'HP', '2025-03-03', 'V. Moyo', '', '', '', ''),
    ('MOB-GDG-043', 'VNY522035W', 'HP Omnibook X FLIP', 'LAPTOP', 'HP', '2025-12-19', 'S. Moyo', '', '', '', ''),
    ('MOB-GDG-044', 'VNY5199WPR', 'HP Omnibook X FLIP', 'LAPTOP', 'HP', '2025-12-19', 'D. M. Siphuma', '', '', '', ''),
    ('MOB-GDG-045', 'VNY53604DQ', 'HP Omnibook X FLIP', 'LAPTOP', 'HP', '2025-12-19', 'D. Moyo', '', '', '', ''),
    ('MOB-GDG-046', '8CC52270D9R', 'HP All-IN-One', 'DESKTOP', 'HP', '2026-01-13', 'Procurement', '', '', '', ''),
    ('MOB-GDG-047', 'CNCRT2P0N3', 'HP LaserJet Pro MFP4103dw', 'PRINTER', 'HP', '2026-01-13', 'Procurement', '', '', '', ''),
    ('MOB-GDG-048', 'IHF5360GMP', 'HP Desktop All-IN-One', 'DESKTOP', 'HP', '2026-04-16', 'Accounts_Clerk', '', '', '', ''),
    ('MOB-GDG-049', 'IHF5340NMD', 'HP Desktop All-IN-One', 'DESKTOP', 'HP', '2026-04-16', 'Accounts_Clerk', '', '', '', ''),
    ('MOB-GDG-050', '8CC5122SY4', 'HP Desktop All-IN-One', 'DESKTOP', 'HP', '2026-04-16', 'Accounts_Clerk', '', '', '', ''),
    ('MOB-GDG-051', 'IH86102P39', 'HP Laptop Ultra7', 'LAPTOP', 'HP', '2026-05-06', 'GIS', '', '', '', ''),
    ('MOB-GDG-052', '5CG6102173', 'HP Xflip Ultra 7', 'LAPTOP', 'HP', '2026-05-06', 'TC L. Ramakgapola', '', '', '', ''),
    ('MOB-GDG-053', 'RRGL404C02V', 'Galaxy Z Fold 7', 'PHONE', 'Samsung', '2025-06-23', 'D. M. Siphuma', '356335510529910', '356696140529913', '', ''),
    ('MOB-GDG-054', 'RRGL30EDCKM', 'Galaxy Z Fold 7', 'PHONE', 'Samsung', '2025-06-23', 'TC L. Ramakgapola', '356335510513120', '356696140513123', '', ''),
    ('MOB-GDG-055', 'RRGL209YOCZ', 'Galaxy Z Fold 7', 'PHONE', 'Samsung', '2025-06-23', 'S. Moyo', '356335510463342', '356696140463345', '', ''),
    ('MOB-GDG-056', 'RRGL30EDFKA', 'Galaxy Z Fold 7', 'PHONE', 'Samsung', '2025-06-23', '', '356335510514110', '356696140514113', '', ''),
    ('MOB-GDG-057', '8CG3411WY7', 'HP Pavilion', 'LAPTOP', 'HP', '2024-04-09', 'A. Mbedzi', '', '', '', ''),
    ('MOB-GDG-058', 'K73K3HT661', 'Iphone 16 Pro max', 'PHONE', 'Apple', '2024-11-15', 'TC', '358798968528675', '358798962168370', '', ''),
    ('MOB-GDG-059', '1HF5260X77', 'HP All-IN-One', 'DESKTOP', 'HP', '2026-04-16', 'Housing_Revenue', '', '', '', ''),
]

PREFIX = 'MOB-GDG'


def _running_tests():
    argv = [a.lower() for a in sys.argv]
    return 'test' in argv[1:2] or 'pytest' in argv[0] or 'PYTEST_CURRENT_TEST' in os.environ


def load_gadgets(apps, schema_editor):
    if schema_editor.connection.alias != 'default' or _running_tests():
        return
    Asset = apps.get_model('helpdesk', 'Asset')
    db = 'default'

    by_serial = {a.serial_number.lower(): a for a in Asset.objects.using(db).exclude(serial_number='')}
    tags = {t.lower() for t in Asset.objects.using(db).values_list('asset_tag', flat=True)}
    top = 0
    for t in tags:
        if t.startswith(PREFIX.lower() + '-') and t.rsplit('-', 1)[1].isdigit():
            top = max(top, int(t.rsplit('-', 1)[1]))

    created = updated = 0
    for tag, serial, name, atype, maker, bought, owner, imei, imei2, imei3, notes in GADGETS:
        current = by_serial.get(serial.lower())
        if current is not None:
            # Only gadgets the earlier seed created are touched, and only to fill gaps
            if current.asset_tag.upper().startswith(PREFIX + '-'):
                changed = []
                if not (current.imei or current.imei2 or current.imei3) and imei:
                    current.imei, current.imei2, current.imei3 = imei, imei2, imei3
                    changed += ['imei', 'imei2', 'imei3']
                if current.asset_type == 'OTHER' and atype == 'TABLET':
                    current.asset_type = 'TABLET'
                    changed.append('asset_type')
                if changed:
                    current.save(update_fields=changed)
                    updated += 1
            continue
        if tag.lower() in tags:                       # the tag is taken by something else: next free number
            top = max(top, 0)
            while f'{PREFIX}-{top + 1:03d}'.lower() in tags:
                top += 1
            top += 1
            tag = f'{PREFIX}-{top:03d}'
        tags.add(tag.lower())
        Asset.objects.using(db).create(
            asset_tag=tag, name=name, asset_type=atype, status='ACTIVE', serial_number=serial,
            imei=imei, imei2=imei2, imei3=imei3, manufacturer=maker, assigned_to_name=owner,
            purchase_date=date.fromisoformat(bought) if bought else None, notes=notes)
        created += 1

    if created or updated:
        try:
            AuditLog = apps.get_model('helpdesk', 'AuditLog')
            AuditLog.objects.using(db).create(
                action='CREATE', model_name='Asset', object_id='', object_repr='Gadget inventory',
                notes=f'Gadget inventory loaded by migration 0014: {created} asset(s) added, {updated} brought up to date')
        except Exception:
            pass                                      # the audit trail must never block a migration


class Migration(migrations.Migration):

    dependencies = [
        ('helpdesk', '0013_asset_imei_tablet'),
    ]

    operations = [
        migrations.RunPython(load_gadgets, migrations.RunPython.noop),
    ]
