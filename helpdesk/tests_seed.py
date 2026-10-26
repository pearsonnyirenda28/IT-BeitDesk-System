from datetime import date
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from helpdesk.management.commands import seed_data
from helpdesk.models import Asset


class SeedDataTests(TestCase):
    def seed(self):
        call_command('seed_data', verbosity=0)

    def test_no_user_accounts_are_created(self):
        self.seed()
        self.assertEqual(User.objects.count(), 0)

    def test_no_default_credentials_remain_in_the_seed_file(self):
        src = Path(seed_data.__file__).read_text(encoding='utf-8')
        for needle in ('admin123', 'pass1234', 'create_user', '_create_users'):
            self.assertNotIn(needle, src)

    def test_gadget_inventory_is_loaded(self):
        self.seed()
        self.assertEqual(Asset.objects.count(), 59)
        by_type = {t: Asset.objects.filter(asset_type=t).count() for t, _ in Asset.TYPE_CHOICES}
        self.assertEqual(by_type, {'DESKTOP': 6, 'LAPTOP': 21, 'PRINTER': 1, 'PHONE': 22, 'NETWORK': 0,
                                   'SERVER': 0, 'MONITOR': 0, 'TABLET': 9, 'OTHER': 0})
        a = Asset.objects.get(serial_number='RFCRA1LHSRY')
        self.assertEqual((a.name, a.asset_type, a.manufacturer, a.status, a.assigned_to_name, a.purchase_date),
                         ('Galaxy Z fold 3', 'PHONE', 'Samsung', 'ACTIVE', 'G. Nyoni', date(2022, 6, 10)))
        self.assertEqual((a.imei, a.imei2, a.imei3, a.notes), ('351014293379976', '352191243379972', '', ''))

    def test_every_imei_in_the_sheet_has_its_own_column(self):
        self.seed()
        self.assertEqual(Asset.objects.exclude(imei='').count(), 37)
        self.assertEqual(Asset.objects.exclude(imei2='').count(), 22)
        self.assertEqual(Asset.objects.exclude(imei3='').count(), 2)
        self.assertFalse(Asset.objects.filter(notes__contains='IMEI').exists())          # nothing left squeezed into notes
        self.assertEqual(Asset.objects.get(serial_number='RRGL30EDFKA').imei_list,
                         [n for n in ('356335510514110', '356696140514113')])

    def test_tablets_are_not_filed_as_phones(self):
        self.seed()
        self.assertEqual(Asset.objects.get(name='Ipad pro 11-inch').asset_type, 'TABLET')
        self.assertEqual(Asset.objects.filter(name__startswith='Galaxy Tab', asset_type='TABLET').count(), 8)
        self.assertEqual(Asset.objects.filter(asset_type='PHONE').count(), 22)

    def test_incomplete_rows_are_kept_and_flagged_not_invented(self):
        self.seed()
        blank = Asset.objects.get(serial_number='6W7BML3')
        self.assertEqual(blank.name, 'Laptop (name not recorded)')
        self.assertEqual((blank.manufacturer, blank.assigned_to_name), ('', 'P. Muchena'))
        self.assertIn('blank', blank.notes)
        self.assertEqual(Asset.objects.get(serial_number='RRGL30EDFKA').assigned_to_name, '')
        self.assertIsNone(Asset.objects.get(serial_number='CND317333V').purchase_date)

    def test_rerunning_does_not_duplicate_and_respects_existing_serials(self):
        Asset.objects.create(asset_tag='MY-OWN-1', name='Already here', serial_number='rfcra1lhsry')   # different case
        self.seed()
        self.assertEqual(Asset.objects.count(), 59)               # 58 new + the one that already existed
        self.assertFalse(Asset.objects.filter(asset_tag='MOB-GDG-001').exists())
        self.seed()
        self.assertEqual(Asset.objects.count(), 59)

    def test_seed_table_is_internally_consistent(self):
        rows = seed_data.ASSET_SEED
        self.assertEqual(len(rows), 59)
        self.assertEqual(len({r[0] for r in rows}), 59)                       # unique tags
        self.assertEqual(len({r[1].lower() for r in rows}), 59)               # unique serials
        valid = {t for t, _ in Asset.TYPE_CHOICES}
        for tag, serial, name, atype, maker, bought, owner, imei, imei2, imei3, notes in rows:
            self.assertIn(atype, valid, tag)
            for number in (imei, imei2, imei3):
                self.assertLessEqual(len(number), 40)
            self.assertTrue(serial and name, tag)
            self.assertLessEqual(len(tag), 30); self.assertLessEqual(len(serial), 100)
            if bought:
                date.fromisoformat(bought)

    def test_later_added_staff_still_get_extensions(self):
        from voip.models import Extension
        self.seed()
        User.objects.create_user('agent', password='x', is_staff=True)
        self.seed()
        self.assertEqual(Extension.objects.count(), 1)
