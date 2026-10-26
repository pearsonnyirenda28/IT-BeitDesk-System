"""IMEI columns and the Tablet type across the registry, plus the two asset migrations (0013, 0014)."""
import csv
import importlib
import io
from unittest import mock

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Asset, AuditLog

FAST = override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
M13 = importlib.import_module('helpdesk.migrations.0013_asset_imei_tablet')
M14 = importlib.import_module('helpdesk.migrations.0014_load_gadget_inventory')


class _Schema:                       # stand-in for the schema editor the migrations receive
    class connection:
        alias = 'default'


@FAST
class AssetFormImeiTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('agent', password='pw', is_staff=True)
        self.client.force_login(self.staff)
        Asset.objects.create(asset_tag='MOB-GDG-001', name='Galaxy Z fold 3', serial_number='SER1',
                             imei='351014293379976', imei2='352191243379972/01')

    def payload(self, **o):
        d = dict(asset_tag='MOB-GDG-002', name='Galaxy A23', asset_type='PHONE', status='ACTIVE', serial_number='SER2',
                 imei='359142451784208', imei2='', imei3='', manufacturer='Samsung', model='', department='', section='',
                 location='', assigned_to_name='R. Dube', purchase_date='', purchase_cost='', warranty_expiry='', notes='')
        d.update(o)
        return d

    def test_form_has_three_imei_inputs_and_the_tablet_type(self):
        r = self.client.get(reverse('asset_create'))
        for n in ('imei', 'imei2', 'imei3'):
            self.assertContains(r, f'name="{n}"')
        self.assertContains(r, 'value="TABLET"')
        self.assertContains(r, 'Tablet')

    def test_saves_up_to_three_numbers_including_suffixes(self):
        self.client.post(reverse('asset_create'), self.payload(imei='359142451784208/01', imei2='1234-5678', imei3='ABC123'))
        a = Asset.objects.get(asset_tag='MOB-GDG-002')
        self.assertEqual((a.imei, a.imei2, a.imei3, a.imei_list), ('359142451784208/01', '1234-5678', 'ABC123',
                                                                     ['359142451784208/01', '1234-5678', 'ABC123']))

    def test_a_duplicate_imei_is_rejected_naming_the_asset_and_everything_else_is_kept(self):
        r = self.client.post(reverse('asset_create'), self.payload(imei2='351014293379976', notes='Keep me'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(list(r.context['errors']), ['imei2'])
        self.assertIn('MOB-GDG-001 (Galaxy Z fold 3)', r.context['errors']['imei2'])
        self.assertContains(r, 'Keep me')
        self.assertContains(r, 'value="359142451784208"')
        self.assertEqual(Asset.objects.count(), 1)

    def test_the_clash_is_found_in_any_of_the_three_columns_case_insensitively(self):
        Asset.objects.create(asset_tag='X', name='x', imei3='abc999')
        for field in ('imei', 'imei2', 'imei3'):
            r = self.client.post(reverse('asset_create'), self.payload(**{field: 'ABC999'}))
            self.assertEqual(list(r.context['errors']), [field], field)
        r = self.client.post(reverse('asset_create'), self.payload(imei='352191243379972/01'))
        self.assertIn('imei', r.context['errors'])

    def test_the_same_number_twice_on_one_asset(self):
        r = self.client.post(reverse('asset_create'), self.payload(imei='555', imei2='555'))
        self.assertEqual(r.context['errors']['imei2'], 'This is the same number as IMEI 1.')

    def test_characters_and_length(self):
        r = self.client.post(reverse('asset_create'), self.payload(imei='35 10 14', imei2='<script>', imei3='1' * 41))
        self.assertEqual(set(r.context['errors']), {'imei', 'imei2', 'imei3'})
        self.assertIn('Too long', r.context['errors']['imei3'])

    def test_editing_can_keep_its_own_numbers_but_not_take_anothers(self):
        a = Asset.objects.get(asset_tag='MOB-GDG-001')
        b = Asset.objects.create(asset_tag='MOB-GDG-009', name='Other', serial_number='SER9', imei='888')
        url = reverse('asset_edit', args=[a.pk])
        ok = self.client.post(url, self.payload(serial_number='SER1', imei='351014293379976', imei2='352191243379972/01', name='Renamed'))
        self.assertEqual(ok.status_code, 302)
        bad = self.client.post(url, self.payload(serial_number='SER1', imei='888'))
        self.assertEqual(list(bad.context['errors']), ['imei'])
        self.assertContains(bad, 'value="Galaxy A23"')                              # the edits are kept
        b.refresh_from_db(); self.assertEqual(b.imei, '888')

    def test_edit_form_shows_existing_numbers(self):
        a = Asset.objects.get(asset_tag='MOB-GDG-001')
        r = self.client.get(reverse('asset_edit', args=[a.pk]))
        self.assertContains(r, 'value="351014293379976"')
        self.assertContains(r, 'value="352191243379972/01"')

    def test_detail_page_shows_the_numbers_and_tablet_icon(self):
        a = Asset.objects.get(asset_tag='MOB-GDG-001')
        r = self.client.get(reverse('asset_detail', args=[a.pk]))
        self.assertContains(r, '351014293379976')
        self.assertContains(r, '352191243379972/01')
        self.assertContains(r, 'IMEI')
        t = Asset.objects.create(asset_tag='TAB-1', name='Ipad', asset_type='TABLET')
        self.assertContains(self.client.get(reverse('asset_detail', args=[t.pk])), 'fa-tablet-screen-button')
        self.assertContains(self.client.get(reverse('asset_list')), 'fa-tablet-screen-button')
        self.assertNotContains(self.client.get(reverse('asset_detail', args=[t.pk])), 'fa-sim-card')   # nothing to show

    def test_the_registry_search_finds_an_asset_by_any_imei(self):
        for needle in ('351014293379976', '352191243379972', '0142933'):
            r = self.client.get(reverse('asset_list'), {'search': needle})
            self.assertEqual([x.asset_tag for x in r.context['assets']], ['MOB-GDG-001'], needle)
        self.assertEqual(list(self.client.get(reverse('asset_list'), {'search': '000000'}).context['assets']), [])

    def test_the_tablet_type_can_be_filtered(self):
        Asset.objects.create(asset_tag='TAB-1', name='Ipad', asset_type='TABLET')
        r = self.client.get(reverse('asset_list'), {'type': 'TABLET'})
        self.assertEqual([x.asset_tag for x in r.context['assets']], ['TAB-1'])

    def test_csv_export_has_the_imei_columns(self):
        body = b''.join(self.client.get(reverse('export_assets_csv')).streaming_content).decode('utf-8-sig')
        rows = list(csv.reader(io.StringIO(body)))
        head = rows[0]
        self.assertEqual(head[head.index('Serial Number'):head.index('Serial Number') + 4], ['Serial Number', 'IMEI 1', 'IMEI 2', 'IMEI 3'])
        self.assertEqual(rows[1][head.index('IMEI 1')], '351014293379976')
        self.assertEqual(rows[1][head.index('IMEI 2')], '352191243379972/01')


class MoveImeiOutOfNotesTests(TestCase):
    """Migration 0013: IMEIs an earlier version stored in the notes go into the new columns."""

    def run13(self):
        M13.move_imei_out_of_notes(apps, _Schema)

    def test_moves_one_two_and_three_numbers_and_keeps_other_notes(self):
        a = Asset.objects.create(asset_tag='A', name='a', notes='IMEI: 111 / 222')
        b = Asset.objects.create(asset_tag='B', name='b', notes='IMEI: 1 / 2 / 3\nmy own note')
        c = Asset.objects.create(asset_tag='C', name='c', notes='IMEI: 111')
        self.run13()
        a.refresh_from_db(); b.refresh_from_db(); c.refresh_from_db()
        self.assertEqual((a.imei, a.imei2, a.imei3, a.notes), ('111', '222', '', ''))
        self.assertEqual((b.imei, b.imei2, b.imei3, b.notes), ('1', '2', '3', 'my own note'))
        self.assertEqual((c.imei, c.notes), ('111', ''))

    def test_extra_numbers_beyond_three_stay_in_the_notes(self):
        a = Asset.objects.create(asset_tag='A', name='a', notes='IMEI: 1 / 2 / 3 / 4 / 5')
        self.run13(); a.refresh_from_db()
        self.assertEqual((a.imei3, a.notes), ('3', 'More IMEIs: 4 / 5'))

    def test_never_overwrites_numbers_already_entered_or_touches_other_notes(self):
        keep = Asset.objects.create(asset_tag='K', name='k', imei='MINE', notes='IMEI: 999')
        plain = Asset.objects.create(asset_tag='P', name='p', notes='Bought with IMEI card reader')
        long_ = Asset.objects.create(asset_tag='L', name='l', notes='IMEI: ' + '9' * 41)
        self.run13()
        for x in (keep, plain, long_): x.refresh_from_db()
        self.assertEqual((keep.imei, keep.notes), ('MINE', 'IMEI: 999'))
        self.assertEqual((plain.imei, plain.notes), ('', 'Bought with IMEI card reader'))
        self.assertEqual((long_.imei, long_.notes[:6]), ('', 'IMEI: '))

    def test_running_twice_changes_nothing_more(self):
        a = Asset.objects.create(asset_tag='A', name='a', notes='IMEI: 111 / 222')
        self.run13(); self.run13(); a.refresh_from_db()
        self.assertEqual((a.imei, a.imei2), ('111', '222'))


class LoadGadgetInventoryTests(TestCase):
    """Migration 0014: put the spreadsheet's gadgets into the registry."""

    def load(self):
        with mock.patch.object(M14, '_running_tests', return_value=False):
            M14.load_gadgets(apps, _Schema)

    def test_it_does_nothing_while_the_test_suite_runs(self):
        M14.load_gadgets(apps, _Schema)                                   # real guard
        self.assertEqual(Asset.objects.count(), 0)                        # so test databases stay empty

    def test_other_databases_are_left_alone(self):
        class Other(_Schema):
            class connection:
                alias = 'year_2025'
        with mock.patch.object(M14, '_running_tests', return_value=False):
            M14.load_gadgets(apps, Other)
        self.assertEqual(Asset.objects.count(), 0)

    def test_loads_everything_with_the_new_columns(self):
        self.load()
        self.assertEqual(Asset.objects.count(), 59)
        a = Asset.objects.get(serial_number='RFCRA1LHSRY')
        self.assertEqual((a.asset_tag, a.name, a.asset_type, a.manufacturer, a.assigned_to_name, a.imei, a.imei2, a.notes),
                         ('MOB-GDG-001', 'Galaxy Z fold 3', 'PHONE', 'Samsung', 'G. Nyoni', '351014293379976', '352191243379972', ''))
        self.assertEqual(Asset.objects.filter(asset_type='TABLET').count(), 9)
        self.assertEqual((Asset.objects.exclude(imei='').count(), Asset.objects.exclude(imei2='').count(), Asset.objects.exclude(imei3='').count()),
                         (37, 22, 2))
        self.assertTrue(AuditLog.objects.filter(object_repr='Gadget inventory', notes__contains='59 asset(s) added').exists())

    def test_the_data_matches_the_spreadsheet_it_came_from(self):
        """Pinned to the original file: counts per type, owners, and the awkward rows."""
        self.load()
        types = {t: Asset.objects.filter(asset_type=t).count() for t, _ in Asset.TYPE_CHOICES}
        self.assertEqual({k: v for k, v in types.items() if v}, {'DESKTOP': 6, 'LAPTOP': 21, 'PRINTER': 1, 'PHONE': 22, 'TABLET': 9})
        self.assertEqual(Asset.objects.get(serial_number='6W7BML3').name, 'Laptop (name not recorded)')
        self.assertEqual(Asset.objects.filter(assigned_to_name='').count(), 1)
        self.assertEqual(Asset.objects.filter(purchase_date__isnull=True).count(), 7)
        self.assertEqual(len({a.serial_number.lower() for a in Asset.objects.all()}), 59)

    def test_running_it_again_adds_nothing(self):
        self.load(); self.load()
        self.assertEqual(Asset.objects.count(), 59)

    def test_existing_registry_entries_are_never_touched(self):
        mine = Asset.objects.create(asset_tag='MOB-PC-003', name='Existing PC', serial_number='SN8900', notes='keep')
        clash = Asset.objects.create(asset_tag='MY-OWN', name='Same serial, own tag', serial_number='rfcra1lhsry', asset_type='OTHER')
        self.load()
        mine.refresh_from_db(); clash.refresh_from_db()
        self.assertEqual((mine.notes, clash.asset_tag, clash.asset_type, clash.imei), ('keep', 'MY-OWN', 'OTHER', ''))   # not a MOB-GDG asset: left exactly as it was
        self.assertEqual(Asset.objects.count(), 1 + 1 + 58)

    def test_gadgets_seeded_by_the_earlier_version_are_brought_up_to_date(self):
        ipad = Asset.objects.get_or_create(asset_tag='MOB-GDG-052', name='Ipad pro 11-inch', asset_type='OTHER',
                                           serial_number='PLACEHOLDER')[0]
        from .management.commands.seed_data import ASSET_SEED
        tablet = next(r for r in ASSET_SEED if r[3] == 'TABLET')
        Asset.objects.all().delete()
        seeded = Asset.objects.create(asset_tag=tablet[0], name=tablet[2], asset_type='OTHER', serial_number=tablet[1])
        phone = next(r for r in ASSET_SEED if r[7] and r[3] == 'PHONE')
        edited = Asset.objects.create(asset_tag=phone[0], name=phone[2], asset_type='PHONE', serial_number=phone[1], imei='MY-OWN-IMEI')
        self.load()
        seeded.refresh_from_db(); edited.refresh_from_db()
        self.assertEqual(seeded.asset_type, 'TABLET')
        self.assertEqual(edited.imei, 'MY-OWN-IMEI')                              # something a person entered is never replaced
        self.assertEqual(Asset.objects.count(), 59)
        self.assertTrue(AuditLog.objects.filter(notes__contains='brought up to date').exists())

    def test_a_tag_already_taken_by_something_else_moves_the_gadget_to_the_next_number(self):
        Asset.objects.create(asset_tag='MOB-GDG-001', name='Not a gadget', serial_number='UNRELATED')
        self.load()
        self.assertEqual(Asset.objects.get(serial_number='UNRELATED').asset_tag, 'MOB-GDG-001')
        first = Asset.objects.get(serial_number='RFCRA1LHSRY')
        self.assertNotEqual(first.asset_tag, 'MOB-GDG-001')
        tags = list(Asset.objects.values_list('asset_tag', flat=True))
        self.assertEqual(len(tags), len(set(tags)))

    def test_the_frozen_table_is_self_consistent(self):
        rows = M14.GADGETS
        self.assertEqual(len(rows), 59)
        self.assertEqual(len({r[0] for r in rows}), 59)
        self.assertEqual(len({r[1].lower() for r in rows}), 59)
        self.assertTrue(all(r[3] in dict(Asset.TYPE_CHOICES) for r in rows))
