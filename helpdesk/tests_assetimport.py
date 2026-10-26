"""Asset registry import (CSV / Excel)."""
import importlib.util
import io
import sys
from datetime import date
from decimal import Decimal
from unittest import mock, skipUnless

from django.contrib.auth.models import User
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from . import assetimport as ai
from .management.commands.seed_data import ASSET_SEED
from .models import Asset, AuditLog
from .tabular import ImportProblem

FAST = override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
HAS_OPENPYXL = importlib.util.find_spec('openpyxl') is not None


def csv_file(text, enc='utf-8'):
    return text.encode(enc)


def parse(text, name='inv.csv'):
    return ai.parse_inventory(name, csv_file(text))


class ValueUnderstandingTests(TestCase):
    def test_types_from_the_spreadsheet_word(self):
        cases = {'Laptop': 'LAPTOP', ' Desktop': 'DESKTOP', 'printer': 'PRINTER', 'Mobile': 'PHONE', 'Smartphone': 'PHONE',
                 'Router': 'NETWORK', 'switch': 'NETWORK', 'Server': 'SERVER', 'Monitor': 'MONITOR', 'PC': 'DESKTOP',
                 'DESKTOP': 'DESKTOP', 'Phone/VoIP': 'PHONE', 'Network Equipment': 'NETWORK', 'Notebook': 'LAPTOP'}
        for word, code in cases.items():
            self.assertEqual(ai.map_type(word), (code, True), word)

    def test_tablets_are_not_phones_even_when_the_sheet_says_mobile(self):
        for name in ('Ipad pro 11-inch', 'Galaxy Tab S8', 'Galaxy Tab59', 'Android tablet'):
            self.assertEqual(ai.map_type('Mobile', name), ('TABLET', True), name)
        self.assertEqual(ai.map_type('Tablet'), ('TABLET', True))
        self.assertEqual(ai.map_type('Mobile', 'Galaxy Z fold 3'), ('PHONE', True))
        self.assertEqual(ai.map_type('Mobile', 'Iphone15Pro Max'), ('PHONE', True))

    def test_type_inferred_from_the_name_when_blank_and_unknown_flagged(self):
        self.assertEqual(ai.map_type('', 'HP LaserJet Pro'), ('PRINTER', True))
        self.assertEqual(ai.map_type('', 'Dell Latitude laptop'), ('LAPTOP', True))
        self.assertEqual(ai.map_type('', 'Mystery box'), ('OTHER', False))
        self.assertEqual(ai.map_type('Flux capacitor', 'x'), ('OTHER', False))

    def test_status(self):
        for word, code in {'': 'ACTIVE', 'In use': 'ACTIVE', 'Under repair': 'REPAIR', 'faulty': 'REPAIR', 'Disposed': 'RETIRED',
                           'Lost': 'MISSING', 'retired': 'RETIRED', 'ACTIVE': 'ACTIVE'}.items():
            self.assertEqual(ai.map_status(word), (code, True), word)
        self.assertEqual(ai.map_status('who knows'), ('ACTIVE', False))

    def test_manufacturer_inference(self):
        for name, maker in {'Galaxy Z fold 3': 'Samsung', 'Dell Latitude 5420': 'Dell', 'HP Omnibook x360': 'HP', 'HP all-in-one': 'HP',
                            'Ipad pro 11-inch': 'Apple', 'Iphone15Pro Max': 'Apple', 'ThinkPad T14': 'Lenovo', 'Mystery': ''}.items():
            self.assertEqual(ai.infer_manufacturer(name), maker, name)

    def test_dates(self):
        from datetime import datetime
        d = lambda v: ai.parse_date(v)
        self.assertEqual(d(datetime(2024, 3, 15, 0, 0)), (date(2024, 3, 15), True))
        self.assertEqual(d(date(2024, 3, 15)), (date(2024, 3, 15), True))
        self.assertEqual(d('2024-03-15'), (date(2024, 3, 15), True))
        self.assertEqual(d('15/03/2024'), (date(2024, 3, 15), True))            # day first
        self.assertEqual(d('05/06/2024'), (date(2024, 6, 5), True))             # 5 June, not 6 May
        self.assertEqual(d('15-03-2024'), (date(2024, 3, 15), True))
        self.assertEqual(d('15 Mar 2024'), (date(2024, 3, 15), True))
        self.assertEqual(d('2024-03-15 00:00:00'), (date(2024, 3, 15), True))
        self.assertEqual(d(45366), (date(2024, 3, 15), True))                   # Excel serial number
        self.assertEqual(d(''), (None, True))
        self.assertEqual(d(None), (None, True))
        self.assertEqual(d('31/31/2024'), (None, False))
        self.assertEqual(d('sometime'), (None, False))

    def test_costs(self):
        self.assertEqual(ai.parse_cost('$1,250.5'), ('1250.50', True))
        self.assertEqual(ai.parse_cost(850), ('850.00', True))
        self.assertEqual(ai.parse_cost(''), ('', True))
        self.assertEqual(ai.parse_cost('-5'), ('', False))
        self.assertEqual(ai.parse_cost('abc'), ('', False))
        self.assertEqual(ai.parse_cost('999999999'), ('', False))


class ParseInventoryTests(TestCase):
    HEAD = 'Serial number,Device Name,Type,Date,IMEI,Owner\n'

    def test_the_gadget_sheet_layout(self):
        rows, cols, notices = parse(self.HEAD + 'RFCRA1LHSRY,Galaxy Z fold 3,Mobile,2022-06-10,351014293379976  352191243379972,G. Nyoni\n')
        r = rows[0]
        self.assertEqual(cols, ['serial', 'name', 'type', 'date', 'imei', 'owner'])
        self.assertEqual((r['serial'], r['name'], r['type'], r['manufacturer'], r['date'], r['owner'], r['status']),
                         ('RFCRA1LHSRY', 'Galaxy Z fold 3', 'PHONE', 'Samsung', '2022-06-10', 'G. Nyoni', 'ACTIVE'))
        self.assertEqual((r['imei'], r['imei2'], r['imei3'], r['notes']), ('351014293379976', '352191243379972', '', ''))
        self.assertEqual((notices, r['warnings']), ([], []))

    def test_a_sheet_rebuilt_from_the_seed_data_imports_back_to_the_identical_seed_data(self):
        """
        Round trip over all 59 gadgets: lay the seed table out the way the original spreadsheet was
        (Serial number, Device Name, Type, Date, IMEI, Owner), import it, and require every field of
        every row to come back exactly as seed_data holds it. Keeps the two paths in agreement.
        """
        import csv
        word = {'LAPTOP': 'Laptop', 'DESKTOP': 'Desktop', 'PRINTER': 'Printer', 'PHONE': 'Mobile', 'TABLET': 'Mobile'}
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['Serial number', 'Device Name', 'Type', 'Date', 'IMEI', 'Owner'])
        for tag, serial, name, atype, maker, bought, owner, i1, i2, i3, notes in ASSET_SEED:
            blank_name = 'Device name was blank' in notes
            w.writerow([serial, '' if blank_name else name, word[atype], bought, '  '.join(x for x in (i1, i2, i3) if x), owner])
        rows, _cols, notices = parse(buf.getvalue())
        plan = ai.plan_import(rows, 'MOB-GDG')
        self.assertEqual((notices, plan['duplicates'], plan['errors']), ([], [], []))
        got = [(r['tag'], r['serial'], r['name'], r['type'], r['manufacturer'], r['date'], r['owner'],
                r['imei'], r['imei2'], r['imei3'], r['notes']) for r in plan['new']]
        self.assertEqual(len(got), 59)
        self.assertEqual(got, list(ASSET_SEED))

    def test_blank_name_gets_a_flagged_placeholder(self):
        r = parse(self.HEAD + '6W7BML3,,Laptop,,,P. Muchena\n')[0][0]
        self.assertEqual((r['name'], r['type']), ('Laptop (name not recorded)', 'LAPTOP'))
        self.assertIn('Device name was blank', r['notes'])

    def test_numbers_from_excel_do_not_grow_a_trailing_point_zero(self):
        self.assertEqual(ai.norm(5000123456.0), '5000123456')
        self.assertEqual(ai.norm(12.5), '12.5')

    def test_headers_are_flexible(self):
        rows, cols, _ = parse('S/N;Description;Asset Type;Purchased;Assigned To;Make;Cost;Status\nA1;Dell Latitude;Laptop;15/03/2024;T. Moyo;Dell;$850;In use\n')
        r = rows[0]
        self.assertEqual((r['serial'], r['name'], r['date'], r['owner'], r['cost'], r['status']),
                         ('A1', 'Dell Latitude', '2024-03-15', 'T. Moyo', '850.00', 'ACTIVE'))

    def test_row_problems_are_reported_with_real_line_numbers(self):
        rows, _, notices = parse(self.HEAD + '\nA1,Laptop one,Laptop,,,\n,,,,,\n,,Laptop,,,nobody\nA2,Two,Mobile,31/31/2024,,\n')
        self.assertEqual([r['serial'] for r in rows], ['A1', 'A2'])
        self.assertEqual(len(notices), 1)
        self.assertIn('Row 5', notices[0])
        self.assertIn("date '31/31/2024' was not understood", rows[1]['warnings'][0])

    def test_unrecognised_type_is_flagged_but_kept(self):
        r = parse(self.HEAD + 'A1,Widget,Gizmo,,,\n')[0][0]
        self.assertEqual(r['type'], 'OTHER')
        self.assertIn('not recognised', r['warnings'][0])

    def test_files_with_no_useful_columns_are_rejected_with_advice(self):
        for text in ('colour,size\nred,big\n', 'owner,date\nme,today\n'):
            with self.assertRaises(ImportProblem) as cm:
                parse(text)
            self.assertIn('template', str(cm.exception).lower())

    def test_limits(self):
        with self.assertRaises(ImportProblem):
            ai.parse_inventory('x.csv', b'')
        big = 'Serial number\n' + ''.join(f'S{i}\n' for i in range(ai.MAX_ROWS + 1))
        with self.assertRaises(ImportProblem) as cm:
            parse(big)
        self.assertIn('Too many rows', str(cm.exception))

    @skipUnless(HAS_OPENPYXL, 'openpyxl not installed')
    def test_excel_file_with_real_dates_and_numbers(self):
        import openpyxl
        from datetime import datetime
        wb = openpyxl.Workbook(); ws = wb.active
        ws.append(['Serial number', 'Device Name', 'Type', 'Date', 'IMEI', 'Owner'])
        ws.append([5000123456, 'Dell Latitude', ' Laptop', datetime(2024, 3, 15), None, 'T. Moyo'])
        ws.append(['AB12', 'Iphone15Pro Max', 'Mobile', None, 3510142933799, 'R. Dube'])
        buf = io.BytesIO(); wb.save(buf)
        rows, _, _ = ai.parse_inventory('inv.xlsx', buf.getvalue())
        self.assertEqual((rows[0]['serial'], rows[0]['type'], rows[0]['date'], rows[0]['manufacturer']), ('5000123456', 'LAPTOP', '2024-03-15', 'Dell'))
        self.assertEqual((rows[1]['type'], rows[1]['manufacturer'], rows[1]['imei'], rows[1]['notes'], rows[1]['date']),
                         ('PHONE', 'Apple', '3510142933799', '', ''))

    def test_excel_without_openpyxl_gets_a_helpful_message(self):
        with mock.patch.dict(sys.modules, {'openpyxl': None}):
            with self.assertRaises(ImportProblem) as cm:
                ai.parse_inventory('inv.xlsx', b'PK\x03\x04..')
        self.assertIn('Save As', str(cm.exception))


class ImeiParsingTests(TestCase):
    def test_one_combined_column_or_three_separate_ones(self):
        rows, cols, _ = parse('Serial number,IMEI\nA,111111111111111  222222222222222  333333333333333\n')
        self.assertEqual((rows[0]['imei'], rows[0]['imei2'], rows[0]['imei3']), ('111111111111111', '222222222222222', '333333333333333'))
        rows, cols, _ = parse('Serial number,IMEI 1,IMEI 2,IMEI 3\nA,111,222,\n')
        self.assertEqual((rows[0]['imei'], rows[0]['imei2'], rows[0]['imei3'], 'imei2' in cols), ('111', '222', '', True))

    def test_suffixes_are_kept_exactly_as_recorded(self):
        r = parse('Serial number,IMEI\nA,358277320714563/01 358922670714560/01\n')[0][0]
        self.assertEqual((r['imei'], r['imei2']), ('358277320714563/01', '358922670714560/01'))

    def test_more_than_three_numbers_are_not_lost(self):
        r = parse('Serial number,Device name,IMEI\nA,Galaxy,1 2 3 4 5\n')[0][0]
        self.assertEqual((r['imei3'], r['notes']), ('3', 'More IMEIs: 4 / 5'))

    def test_the_download_template_has_three_imei_columns_and_imports_cleanly(self):
        rows, cols, _ = ai.parse_inventory('t.csv', (','.join(ai.TEMPLATE_HEADER) + '\n' +
                                                        '\n'.join(','.join(x) for x in ai.TEMPLATE_EXAMPLES)).encode())
        self.assertTrue({'imei', 'imei2', 'imei3'} <= set(cols))
        self.assertEqual((rows[1]['imei'], rows[1]['imei2'], rows[1]['type']), ('351014293379976', '352191243379972', 'PHONE'))


@FAST
class ImeiPlanTests(TestCase):
    def setUp(self):
        Asset.objects.create(asset_tag='MOB-GDG-001', name='Existing phone', serial_number='S0', imei='351014293379976',
                             imei2='352191243379972')

    def rows(self, *specs):
        base = dict(tag='', serial='', name='n', type='PHONE', status='ACTIVE', imei='', imei2='', imei3='', manufacturer='',
                    model='', department='', section='', location='', owner='', date='', cost='', warranty='', notes='', warnings=[])
        return [{**base, 'line': i + 2, **s} for i, s in enumerate(specs)]

    def test_an_imei_already_registered_is_skipped_and_names_the_asset(self):
        plan = ai.plan_import(self.rows({'serial': 'A', 'imei': '352191243379972'}, {'serial': 'B', 'imei': '999999999999999'}), 'MOB-GDG')
        self.assertEqual([r['serial'] for r in plan['new']], ['B'])
        self.assertIn('IMEI 352191243379972 is already registered to MOB-GDG-001', plan['duplicates'][0]['reason'])

    def test_the_same_imei_twice_in_one_file_is_skipped(self):
        plan = ai.plan_import(self.rows({'serial': 'A', 'imei': '111'}, {'serial': 'B', 'imei2': '111'}), 'MOB-GDG')
        self.assertEqual(([r['serial'] for r in plan['new']], len(plan['duplicates'])), (['A'], 1))

    def test_numbers_that_differ_only_by_the_suffix_are_flagged_not_blocked(self):
        plan = ai.plan_import(self.rows({'serial': 'A', 'imei': '351014293379976/01'}), 'MOB-GDG')
        self.assertEqual(len(plan['new']), 1)
        self.assertIn('matches MOB-GDG-001 apart from its suffix', plan['new'][0]['warnings'][0])
        again = ai.plan_import(self.rows({'serial': 'A', 'imei': '777/01'}, {'serial': 'B', 'imei': '777'}), 'MOB-GDG')
        self.assertEqual(len(again['new']), 2)
        self.assertIn('apart from its suffix', again['new'][1]['warnings'][0])

    def test_overlong_imei_is_an_error(self):
        plan = ai.plan_import(self.rows({'serial': 'A', 'imei': '1' * 41}), 'MOB-GDG')
        self.assertEqual((plan['new'], len(plan['errors'])), ([], 1))
        self.assertIn('IMEI', plan['errors'][0])

    def test_apply_saves_the_three_columns(self):
        a = ai.apply_import(ai.plan_import(self.rows({'serial': 'A', 'imei': '1/01', 'imei2': '2', 'imei3': '3'}), 'MOB-GDG'))[0]
        self.assertEqual((a.imei, a.imei2, a.imei3, a.imei_list), ('1/01', '2', '3', ['1/01', '2', '3']))


@FAST
class PlanTests(TestCase):
    def setUp(self):
        Asset.objects.create(asset_tag='MOB-PC-003', name='Existing PC', serial_number='SN8900')
        Asset.objects.create(asset_tag='MOB-GDG-005', name='Earlier gadget', serial_number='G5')

    def rows(self, *specs):
        base = dict(tag='', serial='', name='n', type='LAPTOP', status='ACTIVE', imei='', imei2='', imei3='', manufacturer='', model='', department='',
                    section='', location='', owner='', date='', cost='', warranty='', notes='', warnings=[])
        return [{**base, 'line': i + 2, **s} for i, s in enumerate(specs)]

    def test_existing_serials_are_skipped_case_insensitively_and_say_which_asset(self):
        plan = ai.plan_import(self.rows({'serial': 'sn8900'}, {'serial': 'NEW1'}), 'MOB-GDG')
        self.assertEqual([r['serial'] for r in plan['new']], ['NEW1'])
        self.assertIn('MOB-PC-003 (Existing PC)', plan['duplicates'][0]['reason'])

    def test_repeats_inside_the_file_are_skipped(self):
        plan = ai.plan_import(self.rows({'serial': 'A1'}, {'serial': 'a1'}, {'serial': 'A2'}), 'MOB-GDG')
        self.assertEqual([r['serial'] for r in plan['new']], ['A1', 'A2'])
        self.assertIn('earlier in this file', plan['duplicates'][0]['reason'])

    def test_placeholder_serials_may_repeat(self):
        plan = ai.plan_import(self.rows({'serial': 'N/A'}, {'serial': 'n/a'}, {'serial': ''}, {'serial': 'none'}), 'MOB-GDG')
        self.assertEqual((len(plan['new']), plan['duplicates']), (4, []))

    def test_tags_continue_after_the_highest_in_use(self):
        plan = ai.plan_import(self.rows({'serial': 'A'}, {'serial': 'B'}), 'MOB-GDG')
        self.assertEqual([r['tag'] for r in plan['new']], ['MOB-GDG-006', 'MOB-GDG-007'])
        self.assertEqual([r['tag'] for r in ai.plan_import(self.rows({'serial': 'A'}), 'LAB')['new']], ['LAB-001'])

    def test_tags_given_in_the_file_are_kept_unless_taken(self):
        plan = ai.plan_import(self.rows({'serial': 'A', 'tag': 'CUSTOM-1'}, {'serial': 'B', 'tag': 'custom-1'},
                                        {'serial': 'C', 'tag': 'mob-pc-003'}), 'MOB-GDG')
        self.assertEqual([r['tag'] for r in plan['new']], ['CUSTOM-1'])
        self.assertEqual(len(plan['duplicates']), 2)
        self.assertIn('already in use', plan['duplicates'][0]['reason'])

    def test_generated_tags_never_collide_with_a_tag_given_later_in_the_file(self):
        plan = ai.plan_import(self.rows({'serial': 'A'}, {'serial': 'B', 'tag': 'MOB-GDG-007'}, {'serial': 'C'}), 'MOB-GDG')
        tags = [r['tag'] for r in plan['new']]
        self.assertEqual(len(tags), len(set(tags)))

    def test_too_long_values_are_errors_not_crashes(self):
        plan = ai.plan_import(self.rows({'serial': 'A', 'name': 'x' * 151}, {'serial': 'B' * 101}, {'serial': 'OK'}), 'MOB-GDG')
        self.assertEqual([r['serial'] for r in plan['new']], ['OK'])
        self.assertEqual(len(plan['errors']), 2)

    def test_bad_prefix_is_refused(self):
        for bad in ('', 'has space', '../x', 'x' * 21, '-lead'):
            with self.assertRaises(ImportProblem):
                ai.plan_import(self.rows({'serial': 'A'}), bad)

    def test_planning_changes_nothing(self):
        before = Asset.objects.count()
        ai.plan_import(self.rows({'serial': 'A'}), 'MOB-GDG')
        self.assertEqual(Asset.objects.count(), before)

    def test_apply_creates_everything_and_is_all_or_nothing(self):
        user = User.objects.create_user('agent', password='x', is_staff=True)
        plan = ai.plan_import(self.rows({'serial': 'A', 'cost': '850.00', 'date': '2024-03-15', 'warranty': '2027-03-15',
                                         'owner': 'T. Moyo', 'location': 'Town Hall'}), 'MOB-GDG')
        a = ai.apply_import(plan, user)[0]
        self.assertEqual((a.asset_tag, a.purchase_cost, a.purchase_date, a.warranty_expiry, a.assigned_to_name, a.created_by),
                         ('MOB-GDG-006', Decimal('850.00'), date(2024, 3, 15), date(2027, 3, 15), 'T. Moyo', user))
        plan = ai.plan_import(self.rows({'serial': 'X1'}, {'serial': 'X2'}), 'MOB-GDG')
        real = Asset.objects.create
        calls = {'n': 0}
        def flaky(*a, **k):
            calls['n'] += 1
            if calls['n'] == 2:
                raise RuntimeError('boom')
            return real(*a, **k)
        before = Asset.objects.count()
        with mock.patch.object(Asset.objects, 'create', side_effect=flaky):
            with self.assertRaises(RuntimeError):
                ai.apply_import(plan)
        self.assertEqual(Asset.objects.count(), before)                      # the first one was rolled back too


@FAST
class ImportViewTests(TestCase):
    CSV = ('Serial number,Device Name,Type,Date,Owner\nA1,Dell Latitude,Laptop,15/03/2024,T. Moyo\n'
           'A2,Galaxy A23,Mobile,,R. Dube\nSN8900,Duplicate of existing,Laptop,,\n')

    def setUp(self):
        self.staff = User.objects.create_user('agent', password='pw', is_staff=True)
        self.req = User.objects.create_user('req', password='pw')
        Asset.objects.create(asset_tag='MOB-PC-003', name='Existing PC', serial_number='SN8900')
        self.client.force_login(self.staff)
        self.url = reverse('asset_import')

    def upload(self, text=None, prefix='MOB-GDG', name='inv.csv'):
        return self.client.post(self.url, {'file': SimpleUploadedFile(name, csv_file(text or self.CSV)), 'prefix': prefix})

    def test_only_it_staff(self):
        self.client.force_login(self.req)
        for name in ('asset_import', 'asset_import_template'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)
        self.assertEqual(self.client.post(self.url, {'file': SimpleUploadedFile('a.csv', b'x')}).status_code, 302)
        self.assertEqual(Asset.objects.count(), 1)

    def test_template_download_is_importable_as_is(self):
        r = self.client.get(reverse('asset_import_template'))
        body = r.content
        self.assertEqual(r['Content-Disposition'], 'attachment; filename="asset_import_template.csv"')
        rows, cols, _ = ai.parse_inventory('t.csv', body)
        self.assertEqual((len(rows), 'serial' in cols and 'cost' in cols), (2, True))
        self.assertEqual((rows[0]['cost'], rows[0]['date'], rows[1]['date'], rows[1]['type']), ('850.00', '2024-03-15', '2024-03-15', 'PHONE'))

    def test_preview_changes_nothing_and_explains_everything(self):
        r = self.upload()
        self.assertEqual(r.status_code, 200)
        plan = r.context['plan']
        self.assertEqual(([x['tag'] for x in plan['new']], len(plan['duplicates'])), (['MOB-GDG-001', 'MOB-GDG-002'], 1))
        self.assertContains(r, 'Add 2 assets to the registry')
        self.assertContains(r, 'already registered to MOB-PC-003')
        self.assertContains(r, 'tags MOB-GDG-001 … MOB-GDG-002')
        self.assertEqual(Asset.objects.count(), 1)

    def test_confirm_creates_audits_and_returns_to_the_registry(self):
        payload = self.upload().context['payload']
        r = self.client.post(self.url, {'payload': payload}, follow=True)
        self.assertContains(r, 'Imported 2 asset(s); 1 row(s) skipped')
        a = Asset.objects.get(serial_number='A1')
        self.assertEqual((a.asset_tag, a.asset_type, a.manufacturer, a.purchase_date, a.created_by), ('MOB-GDG-001', 'LAPTOP', 'Dell', date(2024, 3, 15), self.staff))
        self.assertEqual(Asset.objects.get(serial_number='A2').asset_type, 'PHONE')
        self.assertTrue(AuditLog.objects.filter(model_name='Asset', notes__contains='2 asset(s) imported from inv.csv').exists())

    def test_duplicates_created_between_preview_and_confirm_are_still_skipped(self):
        payload = self.upload().context['payload']
        Asset.objects.create(asset_tag='X-1', name='Sneaked in', serial_number='a1')
        r = self.client.post(self.url, {'payload': payload}, follow=True)
        self.assertContains(r, 'Imported 1 asset(s)')
        self.assertEqual(Asset.objects.filter(serial_number__iexact='A1').count(), 1)

    def test_tampered_or_expired_previews_are_refused(self):
        payload = self.upload().context['payload']
        self.assertContains(self.client.post(self.url, {'payload': payload[:-4] + 'zzzz'}, follow=True), 'expired')
        forged = signing.dumps({'rows': [], 'prefix': 'X'}, salt='other')
        self.assertContains(self.client.post(self.url, {'payload': forged}, follow=True), 'expired')
        with mock.patch('django.core.signing.time.time', return_value=__import__('time').time() + 3600):
            self.assertContains(self.client.post(self.url, {'payload': payload}, follow=True), 'expired')
        self.assertEqual(Asset.objects.count(), 1)

    def test_bad_input_keeps_the_page_usable(self):
        self.assertEqual(self.client.post(self.url, {}).context['error'], 'Choose a CSV or Excel file first.')
        self.assertIn('Serial number', self.upload('colour,size\nred,1\n').context['error'])
        r = self.upload(prefix='bad prefix!')
        self.assertIn('prefix', r.context['error'])
        self.assertEqual(r.context['prefix'], 'bad prefix!')                  # what was typed is kept

    def test_nothing_new_message(self):
        r = self.upload('Serial number,Device name\nSN8900,Existing PC\n')
        self.assertContains(r, 'Nothing new to add')

    def test_large_file_previews_100_rows_but_adds_all(self):
        text = 'Serial number,Device name,Type\n' + ''.join(f'B{i},Laptop {i},Laptop\n' for i in range(130))
        r = self.upload(text)
        self.assertEqual((len(r.context['shown']), r.context['more']), (100, 30))
        self.client.post(self.url, {'payload': r.context['payload']})
        self.assertEqual(Asset.objects.filter(serial_number__startswith='B').count(), 130)

    def test_registry_links_to_the_import(self):
        self.assertContains(self.client.get(reverse('asset_list')), reverse('asset_import'))


@FAST
class CommandTests(TestCase):
    def setUp(self):
        import os, tempfile
        fd, self.path = tempfile.mkstemp(suffix='.csv'); os.close(fd)
        with open(self.path, 'wb') as fh:
            fh.write(csv_file('Serial number,Device Name,Type\nA1,Dell Latitude,Laptop\nA2,Galaxy A23,Mobile\n'))
        self.addCleanup(os.remove, self.path)

    def run_cmd(self, *args):
        out = io.StringIO(); call_command('import_assets', self.path, *args, stdout=out); return out.getvalue()

    def test_dry_run_then_apply_then_idempotent(self):
        text = self.run_cmd('--prefix', 'MOB-GDG')
        self.assertIn('Would create 2 asset(s) (1 LAPTOP, 1 PHONE)', text)
        self.assertIn('Dry run only', text)
        self.assertEqual(Asset.objects.count(), 0)
        self.assertIn('Created 2 asset(s)', self.run_cmd('--prefix', 'MOB-GDG', '--apply'))
        self.assertEqual(sorted(Asset.objects.values_list('asset_tag', flat=True)), ['MOB-GDG-001', 'MOB-GDG-002'])
        again = self.run_cmd('--prefix', 'MOB-GDG', '--apply')
        self.assertIn('Created 0 asset(s)', again)
        self.assertIn('2 skipped as already registered', again)

    def test_errors_are_clean(self):
        with self.assertRaises(CommandError):
            call_command('import_assets', '/no/such/file.xlsx')
        with self.assertRaises(CommandError):
            call_command('import_assets', self.path, '--prefix', 'bad prefix')
