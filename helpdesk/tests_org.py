"""Organisation import / export / backfill, and the organisation fields on the user forms."""
import importlib.util
import io
import sys
from datetime import timedelta
from unittest import mock, skipUnless

from django.contrib.auth.models import User
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import orgdata
from .models import AuditLog, Category, Ticket

FAST = override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
HAS_OPENPYXL = importlib.util.find_spec('openpyxl') is not None


def org(user, office='', department='', section=''):
    p = user.profile
    p.office, p.department, p.section = office, department, section
    p.save()
    return user


def ticket(requester, office='', department='', section='', **kw):
    cat = Category.objects.first() or Category.objects.create(name='General')
    return Ticket.objects.create(title=kw.pop('title', 'Issue'), description='d', category=cat, requester=requester,
                                 requester_office=office, requester_department=department,
                                 requester_section=section, **kw)


def csv_bytes(text, enc='utf-8'):
    return text.encode(enc)


# ═══════════════════════════════════════════════════════════════════════════════
# Reading files
# ═══════════════════════════════════════════════════════════════════════════════

class ParseUploadTests(TestCase):
    def parse(self, text, name='f.csv', enc='utf-8'):
        return orgdata.parse_upload(name, csv_bytes(text, enc))

    def test_plain_csv(self):
        rows, cols, warn = self.parse('username,office,department,section\ntendai,Town Hall,Finance,Revenue\nrudo,Depot,Works,Roads\n')
        self.assertEqual(cols, ['office', 'department', 'section'])
        self.assertEqual(rows[0], {'line': 2, 'username': 'tendai', 'office': 'Town Hall', 'department': 'Finance', 'section': 'Revenue'})
        self.assertEqual((len(rows), warn), (2, []))

    def test_semicolon_and_tab_delimiters(self):
        for sep in (';', '\t'):
            rows, cols, _ = self.parse(sep.join(['username', 'office']) + '\n' + sep.join(['tendai', 'Town Hall']) + '\n')
            self.assertEqual((rows[0]['username'], rows[0]['office']), ('tendai', 'Town Hall'), repr(sep))

    def test_excel_style_utf8_bom_and_windows_1252(self):
        rows, _, _ = self.parse('﻿username,office\nrudo,Café Depot\n')
        self.assertEqual(rows[0]['office'], 'Café Depot')
        rows, _, _ = self.parse('username,office\nrudo,Café Depot\n', enc='cp1252')
        self.assertEqual(rows[0]['office'], 'Café Depot')

    def test_header_aliases_any_order_case_and_extra_columns(self):
        rows, cols, _ = self.parse('Full Name,SECTION,Login,Dept\nT M,Revenue,tendai,Finance\n')
        self.assertEqual(cols, ['department', 'section'])
        self.assertEqual((rows[0]['username'], rows[0]['department'], rows[0]['section']), ('tendai', 'Finance', 'Revenue'))

    def test_only_the_columns_present_are_reported(self):
        _, cols, _ = self.parse('username,office\na,b\n')
        self.assertEqual(cols, ['office'])

    def test_whitespace_is_normalised(self):
        rows, _, _ = self.parse('username,office\n  tendai ,  Town    Hall  \n')
        self.assertEqual((rows[0]['username'], rows[0]['office']), ('tendai', 'Town Hall'))

    def test_blank_lines_skipped_and_missing_usernames_warned(self):
        rows, _, warn = self.parse('username,office\n\ntendai,A\n,B\n  ,\nrudo,C\n')
        self.assertEqual([r['username'] for r in rows], ['tendai', 'rudo'])
        self.assertEqual(len(warn), 1)
        self.assertIn('Row 4', warn[0])

    def test_short_rows_do_not_crash(self):
        rows, _, _ = self.parse('username,office,department\ntendai,Town Hall\n')
        self.assertEqual((rows[0]['office'], rows[0]['department']), ('Town Hall', ''))

    def test_problems_are_explained(self):
        cases = [(b'', 'empty'), (b'\n\n', 'no data'), (csv_bytes('office,section\nA,B\n'), 'username'),
                 (csv_bytes('username,notes\na,b\n'), 'office'),
                 (b'PK\x03\x04not really a zip', 'could not be read')]
        for raw, word in cases:
            with self.assertRaises(orgdata.ImportProblem) as cm:
                orgdata.parse_upload('f.csv', raw)
            self.assertIn(word, str(cm.exception).lower(), raw[:20])

    def test_size_and_row_limits(self):
        with self.assertRaises(orgdata.ImportProblem):
            orgdata.parse_upload('f.csv', b'a' * (orgdata.MAX_BYTES + 1))
        big = 'username,office\n' + ''.join(f'u{i},x\n' for i in range(orgdata.MAX_ROWS + 1))
        with self.assertRaises(orgdata.ImportProblem) as cm:
            self.parse(big)
        self.assertIn('Too many rows', str(cm.exception))

    @skipUnless(HAS_OPENPYXL, 'openpyxl not installed')
    def test_excel_file(self):
        import openpyxl
        wb = openpyxl.Workbook(); ws = wb.active
        ws.append(['Username', 'Office', 'Department', 'Section'])
        ws.append(['tendai', 'Town Hall', 'Finance', 'Revenue'])
        ws.append(['rudo', None, 'Works', None])
        buf = io.BytesIO(); wb.save(buf)
        rows, cols, _ = orgdata.parse_upload('org.xlsx', buf.getvalue())
        self.assertEqual(cols, ['office', 'department', 'section'])
        self.assertEqual((rows[0]['office'], rows[1]['office'], rows[1]['department']), ('Town Hall', '', 'Works'))

    def test_excel_without_openpyxl_gives_a_helpful_message(self):
        with mock.patch.dict(sys.modules, {'openpyxl': None}):
            with self.assertRaises(orgdata.ImportProblem) as cm:
                orgdata.parse_upload('org.xlsx', b'PK\x03\x04....')
        self.assertIn('Save As', str(cm.exception))


# ═══════════════════════════════════════════════════════════════════════════════
# Planning and applying
# ═══════════════════════════════════════════════════════════════════════════════

@FAST
class PlanImportTests(TestCase):
    def setUp(self):
        self.t = org(User.objects.create_user('tendai', password='x', first_name='Tendai'), 'Town Hall', 'Finance', 'Revenue')
        self.r = User.objects.create_user('rudo', password='x', first_name='Rudo')
        self.gone = User.objects.create_user('gone', password='x', is_active=False)

    def rows(self, *specs):
        return [{'line': i + 2, 'username': u, **vals} for i, (u, vals) in enumerate(specs)]

    def test_classifies_everything(self):
        plan = orgdata.plan_import(self.rows(
            ('TENDAI', {'office': 'Town Hall'}),           # same value, different case of username
            ('rudo', {'office': 'Depot'}),                 # change
            ('nobody', {'office': 'X'}),                   # unknown
            ('gone', {'office': 'X'})), ['office'])        # inactive
        self.assertEqual((len(plan['updates']), plan['unchanged'], plan['unknown'], plan['inactive']),
                         (1, 1, ['nobody'], ['gone']))
        self.assertEqual(plan['updates'][0]['changes'], {'office': ('', 'Depot')})

    def test_blank_cells_leave_values_alone_unless_asked_to_clear(self):
        rows = self.rows(('tendai', {'office': '', 'department': 'Works'}))
        keep = orgdata.plan_import(rows, ['office', 'department'])
        self.assertEqual(keep['updates'][0]['changes'], {'department': ('Finance', 'Works')})
        clear = orgdata.plan_import(rows, ['office', 'department'], clear_blanks=True)
        self.assertEqual(clear['updates'][0]['changes'], {'office': ('Town Hall', ''), 'department': ('Finance', 'Works')})

    def test_duplicate_usernames_last_row_wins(self):
        plan = orgdata.plan_import(self.rows(('rudo', {'office': 'A'}), ('Rudo', {'office': 'B'})), ['office'])
        self.assertEqual((plan['duplicates'], plan['updates'][0]['changes']['office']), (1, ('', 'B')))

    def test_too_long_values_are_reported_not_applied(self):
        plan = orgdata.plan_import(self.rows(('rudo', {'office': 'x' * 101})), ['office'])
        self.assertEqual((plan['updates'], len(plan['errors'])), ([], 1))
        self.assertIn('Row 2', plan['errors'][0])

    def test_apply_writes_only_planned_fields(self):
        plan = orgdata.plan_import(self.rows(('tendai', {'office': 'Annex'}), ('rudo', {'section': 'Roads'})), ['office', 'section'])
        self.assertEqual(orgdata.apply_import(plan), 2)
        self.t.profile.refresh_from_db(); self.r.profile.refresh_from_db()
        self.assertEqual((self.t.profile.office, self.t.profile.department, self.t.profile.section), ('Annex', 'Finance', 'Revenue'))
        self.assertEqual((self.r.profile.office, self.r.profile.section), ('', 'Roads'))

    def test_apply_creates_a_missing_profile(self):
        self.r.profile.delete()
        plan = orgdata.plan_import(self.rows(('rudo', {'office': 'Depot'})), ['office'])
        orgdata.apply_import(plan)
        self.assertEqual(User.objects.get(pk=self.r.pk).profile.office, 'Depot')

    def test_export_round_trips_through_import(self):
        text = '\n'.join(','.join(f'"{c}"' for c in row) for row in orgdata.export_rows())
        rows, cols, _ = orgdata.parse_upload('x.csv', text.encode())
        plan = orgdata.plan_import(rows, cols)
        self.assertEqual((plan['updates'], plan['unknown']), ([], []))     # nothing to change = faithful export


@FAST
class ImportViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('root', 'r@x.com', 'pw')
        self.staff = User.objects.create_user('agent', password='pw', is_staff=True)
        self.t = User.objects.create_user('tendai', password='pw', first_name='Tendai', last_name='Moyo')
        self.r = org(User.objects.create_user('rudo', password='pw', first_name='Rudo'), 'Old Depot', '', '')
        self.client.force_login(self.admin)
        self.url = reverse('organisation_import')

    def upload(self, text, **extra):
        f = SimpleUploadedFile('org.csv', csv_bytes(text), content_type='text/csv')
        return self.client.post(self.url, {'file': f, **extra})

    GOOD = 'username,office,department\ntendai,Town Hall,Finance\nrudo,Depot,Works\nnobody,X,Y\n'

    def test_admin_only(self):
        for user in (self.staff, self.t):
            self.client.force_login(user)
            for name in ('organisation_import', 'organisation_export', 'organisation_backfill'):
                r = self.client.get(reverse(name)) if name != 'organisation_backfill' else self.client.post(reverse(name))
                self.assertEqual(r.status_code, 302, (user.username, name))

    def test_upload_form(self):
        r = self.client.get(self.url)
        self.assertContains(r, 'username, office, department, section')
        self.assertContains(r, 'Download list')

    def test_preview_shows_the_exact_changes_and_changes_nothing(self):
        r = self.upload(self.GOOD)
        self.assertEqual(r.status_code, 200)
        plan = r.context['plan']
        self.assertEqual((len(plan['updates']), plan['unknown']), (2, ['nobody']))
        self.assertContains(r, 'Old Depot')                                 # before
        self.assertContains(r, 'Depot')                                     # after
        self.assertContains(r, 'Apply 2 changes')
        self.assertContains(r, 'not found')
        self.t.profile.refresh_from_db(); self.assertEqual(self.t.profile.office, '')   # untouched until confirmed

    def test_confirm_applies_audits_and_reports(self):
        payload = self.upload(self.GOOD).context['payload']
        r = self.client.post(self.url, {'payload': payload}, follow=True)
        self.assertContains(r, 'Import complete — 2 user(s) updated')
        self.t.profile.refresh_from_db(); self.r.profile.refresh_from_db()
        self.assertEqual((self.t.profile.office, self.t.profile.department), ('Town Hall', 'Finance'))
        self.assertEqual((self.r.profile.office, self.r.profile.department), ('Depot', 'Works'))
        self.assertTrue(AuditLog.objects.filter(model_name='UserProfile', notes__contains='imported from file').exists())

    def test_tampered_or_expired_preview_is_refused(self):
        payload = self.upload(self.GOOD).context['payload']
        r = self.client.post(self.url, {'payload': payload[:-3] + 'abc'}, follow=True)
        self.assertContains(r, 'expired')
        forged = signing.dumps({'rows': [{'line': 2, 'username': 'root', 'office': 'Hacked'}], 'columns': ['office'], 'clear': False},
                               salt='some-other-salt')
        self.client.post(self.url, {'payload': forged}, follow=True)
        self.admin.profile.refresh_from_db(); self.assertEqual(self.admin.profile.office, '')
        with mock.patch('django.core.signing.time.time', return_value=__import__('time').time() + 3600):
            r = self.client.post(self.url, {'payload': payload}, follow=True)
        self.assertContains(r, 'expired')
        self.t.profile.refresh_from_db(); self.assertEqual(self.t.profile.office, '')

    def test_apply_rechecks_against_current_data(self):
        payload = self.upload('username,office\nrudo,Depot\n').context['payload']
        org(self.r, 'Depot')                                                # someone else made the same change meanwhile
        r = self.client.post(self.url, {'payload': payload}, follow=True)
        self.assertContains(r, '0 user(s) updated, 1 already up to date')

    def test_clear_blanks_option(self):
        text = 'username,office\nrudo,\n'
        self.assertEqual(self.upload(text).context['plan']['updates'], [])  # default: ignored
        r = self.upload(text, clear_blanks='on')
        self.assertEqual(r.context['plan']['updates'][0]['changes'], {'office': ('Old Depot', '')})
        self.assertContains(r, '(cleared)')

    def test_bad_files_show_a_message_and_keep_the_page(self):
        r = self.upload('office,section\nA,B\n')
        self.assertContains(r, 'Could not find a')
        self.assertEqual(self.client.post(self.url, {}).context['error'], 'Choose a CSV or Excel file first.')
        self.assertNotIn('preview', self.upload('nothing,here\n1,2\n').context)

    def test_preview_caps_the_table_but_applies_everything(self):
        for i in range(130): User.objects.create_user(f'bulk{i}', password='x')
        text = 'username,office\n' + ''.join(f'bulk{i},Annex\n' for i in range(130))
        r = self.upload(text)
        self.assertEqual((len(r.context['shown']), r.context['more']), (100, 30))
        self.client.post(self.url, {'payload': r.context['payload']})
        self.assertEqual(User.objects.filter(profile__office='Annex').count(), 130)

    def test_export_download_and_formula_safety(self):
        org(self.t, '=SUM(A1)', 'Finance', '')
        r = self.client.get(reverse('organisation_export'))
        body = r.content.decode('utf-8-sig')
        self.assertEqual(r['Content-Disposition'], 'attachment; filename="it_helpdesk_organisation.csv"')
        self.assertTrue(body.startswith('username,full name,role,office,department,section'))
        self.assertIn("'=SUM(A1)", body)
        self.assertIn('rudo,Rudo,User,Old Depot', body)
        self.assertIn('root,,Administrator', body)

    def test_preview_offers_the_backfill_on_a_first_time_import_without_saving_anything(self):
        t1 = ticket(self.t)                                                # profiles are still empty
        r = self.upload('username,office,section\ntendai,Town Hall,Revenue\n')
        self.assertEqual(r.context['backfill']['tickets'], 1)              # estimated from the file, not today's profiles
        self.assertContains(r, 'Also fill in older tickets')
        self.assertContains(r, 'About 1 ticket would be filled in')
        self.t.profile.refresh_from_db(); t1.refresh_from_db()
        self.assertEqual((self.t.profile.office, t1.requester_office), ('', ''))          # the simulation was rolled back

    def test_no_backfill_offer_when_it_would_do_nothing(self):
        r = self.upload('username,office\nrudo,Depot\n')                   # rudo has no tickets
        self.assertEqual(r.context['backfill']['tickets'], 0)
        self.assertNotContains(r, 'Also fill in older tickets')

    def test_import_without_the_box_ticked_leaves_tickets_alone(self):
        t1 = ticket(self.t)
        r = self.upload('username,office\ntendai,Town Hall\n')
        self.client.post(self.url, {'payload': r.context['payload']}, follow=True)
        t1.refresh_from_db(); self.assertEqual(t1.requester_office, '')

    def test_import_can_also_backfill(self):
        t1 = ticket(self.t)
        r = self.upload('username,office,section\ntendai,Town Hall,Revenue\n')
        self.client.post(self.url, {'payload': r.context['payload'], 'also_backfill': 'on'}, follow=True)
        t1.refresh_from_db()
        self.assertEqual((t1.requester_office, t1.requester_section), ('Town Hall', 'Revenue'))


# ═══════════════════════════════════════════════════════════════════════════════
# Backfill
# ═══════════════════════════════════════════════════════════════════════════════

@FAST
class BackfillTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('root', 'r@x.com', 'pw')
        self.a = org(User.objects.create_user('amy', password='pw'), 'Town Hall', 'Finance', 'Revenue')
        self.b = org(User.objects.create_user('bob', password='pw'), 'Depot', '', 'Roads')
        self.c = User.objects.create_user('cat', password='pw')                          # nothing on her profile

    def test_fills_blanks_only_and_never_overwrites(self):
        blank = ticket(self.a)
        partial = ticket(self.a, office='Annex Building')                                   # already has an office
        done = ticket(self.a, 'Town Hall', 'Finance', 'Revenue')
        r = orgdata.backfill_ticket_org('default', apply=True)
        for t in (blank, partial, done): t.refresh_from_db()
        self.assertEqual((blank.requester_office, blank.requester_department, blank.requester_section),
                         ('Town Hall', 'Finance', 'Revenue'))
        self.assertEqual((partial.requester_office, partial.requester_department), ('Annex Building', 'Finance'))
        self.assertEqual(r['tickets'], 2)                                                   # `done` needed nothing
        self.assertEqual(r['fields'], 3 + 2)

    def test_only_fields_the_profile_has_are_filled(self):
        t = ticket(self.b)
        orgdata.backfill_ticket_org('default', apply=True)
        t.refresh_from_db()
        self.assertEqual((t.requester_office, t.requester_department, t.requester_section), ('Depot', '', 'Roads'))

    def test_untouched_cases(self):
        no_profile_data = ticket(self.c)
        non_user_caller = ticket(None, requester_name='Visitor')
        r = orgdata.backfill_ticket_org('default', apply=True)
        self.assertEqual(r, {'users': 0, 'tickets': 0, 'fields': 0})
        for t in (no_profile_data, non_user_caller):
            t.refresh_from_db(); self.assertEqual(t.requester_office, '')

    def test_dry_run_changes_nothing_and_counts_match_apply(self):
        for u in (self.a, self.a, self.b): ticket(u)
        dry = orgdata.backfill_ticket_org('default')
        self.assertEqual((dry['users'], dry['tickets'], dry['fields']), (2, 3, 0))
        self.assertFalse(Ticket.objects.exclude(requester_office='').exists())
        real = orgdata.backfill_ticket_org('default', apply=True)
        self.assertEqual((real['users'], real['tickets']), (2, 3))

    def test_second_run_finds_nothing(self):
        ticket(self.a)
        orgdata.backfill_ticket_org('default', apply=True)
        self.assertEqual(orgdata.backfill_ticket_org('default', apply=True)['tickets'], 0)

    def test_management_command_dry_run_then_apply(self):
        ticket(self.a)
        out = io.StringIO(); call_command('backfill_ticket_org', stdout=out)
        self.assertIn('Would update 1 ticket(s) for 1 requester(s)', out.getvalue())
        self.assertIn('Dry run only', out.getvalue())
        self.assertEqual(Ticket.objects.get().requester_office, '')
        out = io.StringIO(); call_command('backfill_ticket_org', '--apply', '--database', 'default', stdout=out)
        self.assertIn('Updated 1 ticket(s)', out.getvalue())
        self.assertEqual(Ticket.objects.get().requester_office, 'Town Hall')

    def test_command_survives_a_bad_database(self):
        out, err = io.StringIO(), io.StringIO()
        call_command('backfill_ticket_org', '--database', 'does_not_exist', stdout=out, stderr=err)
        self.assertIn('skipped', err.getvalue())

    def test_admin_button_and_cards(self):
        ticket(self.a); ticket(self.b)
        self.client.force_login(self.admin)
        page = self.client.get(reverse('organisation'))
        self.assertEqual(page.context['backfill']['tickets'], 2)
        self.assertContains(page, '2 older tickets')
        r = self.client.post(reverse('organisation_backfill'), follow=True)
        self.assertContains(r, 'Updated 2 older ticket(s) for 2 requester(s)')
        self.assertTrue(AuditLog.objects.filter(notes__contains='filled in on 2 older').exists())
        self.assertNotContains(self.client.get(reverse('organisation')), 'older ticket')        # card gone
        self.assertContains(self.client.post(reverse('organisation_backfill'), follow=True), 'Nothing to update')

    def test_get_does_not_backfill(self):
        ticket(self.a)
        self.client.force_login(self.admin)
        self.client.get(reverse('organisation_backfill'))
        self.assertEqual(Ticket.objects.get().requester_office, '')

    def test_demand_report_tells_admins_about_fillable_tickets(self):
        ticket(self.a)
        self.client.force_login(self.admin)
        r = self.client.get(reverse('analytics_demand') + '?by=office')
        self.assertEqual(r.context['fillable_tickets'], 1)
        self.assertContains(r, 'can be assigned to the right office')
        staff = User.objects.create_user('agent', password='pw', is_staff=True)
        self.client.force_login(staff)
        self.assertIsNone(self.client.get(reverse('analytics_demand') + '?by=office').context['fillable_tickets'])


class YearAliasTests(TestCase):
    def test_registers_a_year_database_once_cloned_from_default(self):
        from django.conf import settings
        from .db_router import register_year_alias
        settings.DATABASES.pop('year_2031', None)
        try:
            self.assertEqual(register_year_alias(2031, '/tmp/y2031.sqlite3'), 'year_2031')
            cfg = settings.DATABASES['year_2031']
            self.assertEqual((cfg['NAME'], cfg['ENGINE']), ('/tmp/y2031.sqlite3', settings.DATABASES['default']['ENGINE']))
            settings.DATABASES['year_2031']['NAME'] = 'changed'
            register_year_alias(2031, '/tmp/other')                         # already registered: left alone
            self.assertEqual(settings.DATABASES['year_2031']['NAME'], 'changed')
        finally:
            settings.DATABASES.pop('year_2031', None)

    def test_middleware_still_selects_the_alias_through_the_helper(self):
        from .db_router import YearDatabaseMiddleware, get_current_db_alias
        seen = {}
        mw = YearDatabaseMiddleware(lambda req: seen.setdefault('alias', get_current_db_alias()) and mock.MagicMock())
        req = RequestFactory().get('/')
        req.session = {'active_db_year': 2030, 'active_db_name': 'x.db'}
        with mock.patch('helpdesk.db_router.register_year_alias', return_value='default') as m:
            mw(req)
        m.assert_called_once_with(2030, 'x.db')
        self.assertEqual(get_current_db_alias(), 'default')                  # cleaned up after the request


# ═══════════════════════════════════════════════════════════════════════════════
# User forms
# ═══════════════════════════════════════════════════════════════════════════════

@FAST
class UserFormTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('root', 'r@x.com', 'pw')
        self.staff = User.objects.create_user('agent', password='pw', is_staff=True)
        self.client.force_login(self.admin)

    def data(self, **o):
        d = dict(username='newuser', first_name='New', last_name='User', email='n@x.com', password='longenough1',
                 confirm_password='longenough1', office='Town Hall', department='Finance', section='Revenue')
        d.update(o)
        return d

    def test_create_with_organisation(self):
        r = self.client.post(reverse('user_create'), self.data())
        u = User.objects.get(username='newuser')
        self.assertRedirects(r, reverse('user_detail', args=[u.pk]), fetch_redirect_response=False)
        self.assertEqual((u.profile.office, u.profile.department, u.profile.section), ('Town Hall', 'Finance', 'Revenue'))

    def test_create_form_shows_the_organisation_fields_and_known_values(self):
        org(self.staff, 'Head Office')
        r = self.client.get(reverse('user_create'))
        self.assertContains(r, 'name="office"')
        self.assertContains(r, '<option value="Head Office">')

    def test_validation_failure_keeps_everything_but_the_passwords(self):
        r = self.client.post(reverse('user_create'), self.data(confirm_password='different', is_staff='on', office='Annex'))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username='newuser').exists())
        self.assertEqual(list(r.context['errors']), ['confirm_password'])
        for kept in ('value="newuser"', 'value="New"', 'value="n@x.com"', 'value="Annex"', 'value="Finance"'):
            self.assertContains(r, kept)
        self.assertRegex(r.content.decode(), r'name="is_staff"\s+checked')
        self.assertNotContains(r, 'longenough1')

    def test_each_error_is_reported_against_its_field(self):
        User.objects.create_user('Taken', password='x')
        r = self.client.post(reverse('user_create'), self.data(username='taken', office='x' * 101, password='short', confirm_password='short'))
        self.assertEqual(set(r.context['errors']), {'username', 'password', 'office'})     # case-insensitive duplicate

    def test_only_admins_can_create(self):
        self.client.force_login(self.staff)
        self.client.post(reverse('user_create'), self.data())
        self.assertFalse(User.objects.filter(username='newuser').exists())

    def test_edit_by_admin_updates_organisation(self):
        target = User.objects.create_user('tendai', password='x')
        self.assertContains(self.client.get(reverse('user_edit', args=[target.pk])), 'name="office"')
        self.client.post(reverse('user_edit', args=[target.pk]), dict(first_name='T', last_name='M', email='', office='Annex', department='IT', section=''))
        target.profile.refresh_from_db()
        self.assertEqual((target.profile.office, target.profile.department), ('Annex', 'IT'))

    def test_edit_rejects_overlong_values_and_keeps_input(self):
        target = org(User.objects.create_user('tendai', password='x'), 'Keep Me')
        r = self.client.post(reverse('user_edit', args=[target.pk]), dict(first_name='T', last_name='M', email='', office='x' * 101, department='D', section=''))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'value="D"')
        target.profile.refresh_from_db(); self.assertEqual(target.profile.office, 'Keep Me')

    def test_non_admin_staff_cannot_see_or_change_organisation(self):
        target = org(User.objects.create_user('tendai', password='x'), 'Town Hall')
        self.client.force_login(self.staff)
        self.assertNotContains(self.client.get(reverse('user_edit', args=[target.pk])), 'name="office"')
        self.client.post(reverse('user_edit', args=[target.pk]), dict(first_name='Renamed', last_name='M', email='', office='Hacked'))
        target.refresh_from_db(); target.profile.refresh_from_db()
        self.assertEqual((target.first_name, target.profile.office), ('Renamed', 'Town Hall'))
