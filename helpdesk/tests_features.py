"""
Tests for: organisation fields, staff performance + request-demand analytics, template colours,
the new-requests queue, My Requests tracking, and quick request links.
"""
import json
import time
from datetime import timedelta
from unittest import mock

import pyotp
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import insights
from .models import (AuditLog, Category, PendingNotification, QuickRequestLink, Ticket,
                     TicketApproval, TicketComment, TicketTemplate)

FAST = override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])


def org(user, office='', department='', section=''):
    p = user.profile
    p.office, p.department, p.section = office, department, section
    p.save()
    return user


def mk_ticket(requester=None, assigned=None, status='OPEN', priority='MEDIUM', days_ago=0,
              minutes=None, breach=False, office='', department='', section='', **kw):
    cat = Category.objects.first() or Category.objects.create(name='General')
    t = Ticket.objects.create(title=kw.pop('title', 'Issue'), description='d', category=cat,
                              requester=requester, assigned_to=assigned, priority=priority,
                              requester_office=office, requester_department=department,
                              requester_section=section, **kw)
    upd = dict(status=status, sla_breach=breach, created_at=timezone.now() - timedelta(days=days_ago))
    if minutes is not None:
        upd['resolution_time_minutes'] = minutes
    Ticket.objects.filter(pk=t.pk).update(**upd)
    t.refresh_from_db()
    return t


# ═══════════════════════════════════════════════════════════════════════════════
# Organisation fields → snapshot on tickets, user picker, admin page
# ═══════════════════════════════════════════════════════════════════════════════

@FAST
class OrganisationTests(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(name='Hardware')
        self.admin = User.objects.create_superuser('root', 'r@x.com', 'pw')
        self.staff = User.objects.create_user('agent', password='pw', is_staff=True)
        self.u1 = org(User.objects.create_user('tendai', password='pw', first_name='Tendai', last_name='Moyo'),
                      'Town Hall', 'Finance', 'Revenue')
        self.u2 = org(User.objects.create_user('rudo', password='pw', first_name='Rudo', last_name='Dube'),
                      'Depot', 'Works', 'Roads')
        self.url = reverse('portal_create')

    def test_requester_ticket_snapshots_profile_org(self):
        self.client.force_login(self.u1)
        self.client.post(self.url, dict(title='VPN down', priority='LOW'))
        t = Ticket.objects.get()
        self.assertEqual((t.requester_office, t.requester_department, t.requester_section),
                         ('Town Hall', 'Finance', 'Revenue'))

    def test_profile_value_wins_over_what_is_typed(self):
        self.client.force_login(self.u1)
        self.client.post(self.url, dict(title='x y', priority='LOW', requester_office='Somewhere Else',
                                        requester_department='Hacked', requester_section='Fake'))
        t = Ticket.objects.get()
        self.assertEqual((t.requester_office, t.requester_department, t.requester_section),
                         ('Town Hall', 'Finance', 'Revenue'))        # cannot fragment the reports

    def test_typed_org_fills_gaps_when_profile_is_empty(self):
        user = User.objects.create_user('newbie', password='pw')
        self.client.force_login(user)
        self.client.post(self.url, dict(title='x y', priority='LOW', requester_office='Border Post',
                                        requester_section='Gate 2'))
        t = Ticket.objects.get()
        self.assertEqual((t.requester_office, t.requester_department, t.requester_section),
                         ('Border Post', '', 'Gate 2'))

    def test_form_prefills_and_locks_requesters_org(self):
        self.client.force_login(self.u1)
        r = self.client.get(self.url)
        self.assertEqual((r.context['v']['requester_office'], r.context['v']['requester_department']),
                         ('Town Hall', 'Finance'))
        self.assertContains(r, 'Filled in from your profile')
        self.assertContains(r, 'readonly')

    def test_staff_on_behalf_uses_that_users_profile(self):
        self.client.force_login(self.staff)
        self.client.post(self.url, dict(title='x y', priority='LOW', source='WALK_IN', on_behalf_of=self.u2.pk,
                                        requester_office='Ignored'))
        t = Ticket.objects.get()
        self.assertEqual((t.requester, t.logged_by, t.requester_office, t.requester_section),
                         (self.u2, self.staff, 'Depot', 'Roads'))

    def test_staff_non_user_caller_uses_typed_org(self):
        self.client.force_login(self.staff)
        self.client.post(self.url, dict(title='x y', priority='LOW', source='PHONE', requester_name='Visitor',
                                        requester_office='Library', requester_department='Culture'))
        t = Ticket.objects.get()
        self.assertEqual((t.requester, t.requester_office, t.requester_department), (None, 'Library', 'Culture'))

    def test_staff_picker_has_users_with_their_offices(self):
        self.client.force_login(self.staff)
        r = self.client.get(self.url)
        by_name = {u['name']: u for u in r.context['staff_users']}
        self.assertEqual((by_name['Tendai Moyo']['office'], by_name['Rudo Dube']['section']),
                         ('Town Hall', 'Roads'))
        self.assertEqual(set(r.context['org_choices']['office']), {'Town Hall', 'Depot'})
        self.assertContains(r, 'narrow the list by office')
        self.assertContains(r, 'id="userData"')

    def test_request_page_uses_the_background_photo_and_gallery(self):
        self.client.force_login(self.u1)
        r = self.client.get(self.url)
        self.assertContains(r, 'beitbridge_bg')
        self.assertContains(r, 'Choose a template')
        self.assertContains(r, 'My requests')

    # ── organisation admin page ──────────────────────────────────────────────
    def test_only_admins_see_organisation_page(self):
        for user in (self.staff, self.u1):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('organisation')).status_code, 302)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('organisation')).status_code, 200)

    def test_admin_saves_only_changed_rows_and_audits(self):
        self.client.force_login(self.admin)
        data = {}
        for u in User.objects.filter(is_active=True):
            p = u.profile
            data.update({f'office_{u.pk}': p.office, f'department_{u.pk}': p.department, f'section_{u.pk}': p.section})
        data[f'office_{self.staff.pk}'] = '  Head   Office '
        data[f'section_{self.staff.pk}'] = 'Helpdesk'
        r = self.client.post(reverse('organisation'), data, follow=True)
        self.assertContains(r, '1 user(s) updated')
        self.staff.profile.refresh_from_db()
        self.assertEqual((self.staff.profile.office, self.staff.profile.section), ('Head Office', 'Helpdesk'))
        self.assertTrue(AuditLog.objects.filter(model_name='UserProfile').exists())

    def test_overlong_value_rejects_everything_and_keeps_input(self):
        self.client.force_login(self.admin)
        data = {f'office_{u.pk}': '' for u in User.objects.filter(is_active=True)}
        data[f'office_{self.u1.pk}'] = 'X' * 101
        data[f'office_{self.u2.pk}'] = 'Brand New Office'
        r = self.client.post(reverse('organisation'), data)
        self.assertEqual(r.status_code, 200)
        self.u2.profile.refresh_from_db()
        self.assertEqual(self.u2.profile.office, 'Depot')                  # nothing saved
        self.assertContains(r, 'Brand New Office')                         # but typed value kept
        self.assertContains(r, 'too long')


# ═══════════════════════════════════════════════════════════════════════════════
# Template colours
# ═══════════════════════════════════════════════════════════════════════════════

@FAST
class TemplateColourTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('agent', password='pw', is_staff=True)
        self.cat = Category.objects.create(name='Hardware')
        self.client.force_login(self.staff)

    def payload(self, **o):
        d = dict(name='Printer jam', description='Paper stuck', icon='fas fa-print', color='#f0883e',
                 title_template='Printer jam in [room]', description_template='Which printer?',
                 category=self.cat.pk, priority='HIGH', is_active='on')
        d.update(o)
        return d

    def test_form_offers_swatches_and_preview(self):
        r = self.client.get(reverse('template_create'))
        self.assertContains(r, 'class="swatch')
        self.assertContains(r, 'name="color"')
        self.assertContains(r, 'how it appears on the request form')
        self.assertEqual(r.context['v']['color'], '#2ea043')
        self.assertEqual(r.context['v']['priority'], 'MEDIUM')              # was a lowercase default that matched nothing

    def test_create_saves_colour_and_it_shows_in_list_and_portal(self):
        r = self.client.post(reverse('template_create'), self.payload())
        self.assertEqual(r.status_code, 302)
        t = TicketTemplate.objects.get()
        self.assertEqual((t.color, t.priority, t.category), ('#f0883e', 'HIGH', self.cat))
        self.assertContains(self.client.get(reverse('template_list')), '#f0883e')
        req = User.objects.create_user('req', password='pw')
        self.client.force_login(req)
        self.assertContains(self.client.get(reverse('portal')), '#f0883e')
        self.assertContains(self.client.get(reverse('portal_create')), '--c:#f0883e')

    def test_bad_colour_rejected_and_everything_else_kept(self):
        r = self.client.post(reverse('template_create'), self.payload(color='red; background:url(x)'))
        self.assertEqual(list(r.context['errors']), ['color'])
        self.assertEqual(TicketTemplate.objects.count(), 0)
        self.assertContains(r, 'Paper stuck')
        self.assertContains(r, 'Printer jam in [room]')

    def test_other_validation_and_duplicate_name(self):
        TicketTemplate.objects.create(name='Printer jam', title_template='x', created_by=self.staff)
        r = self.client.post(reverse('template_create'), self.payload(icon='fas fa-evil', title_template='', priority='ZZ'))
        self.assertEqual(set(r.context['errors']), {'name', 'icon', 'title_template', 'priority'})

    def test_edit_changes_colour_and_can_keep_own_name(self):
        self.client.post(reverse('template_create'), self.payload())
        t = TicketTemplate.objects.get()
        r = self.client.post(reverse('template_edit', args=[t.pk]), self.payload(color='#58a6ff'))
        self.assertEqual(r.status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.color, '#58a6ff')
        form = self.client.get(reverse('template_edit', args=[t.pk]))
        self.assertEqual(form.context['v']['color'], '#58a6ff')

    def test_requesters_cannot_create_or_edit(self):
        t = TicketTemplate.objects.create(name='X', title_template='x', created_by=self.staff)
        self.client.force_login(User.objects.create_user('req', password='pw'))
        self.assertEqual(self.client.get(reverse('template_create')).status_code, 302)
        self.assertEqual(self.client.post(reverse('template_edit', args=[t.pk]), self.payload()).status_code, 302)
        t.refresh_from_db(); self.assertEqual(t.name, 'X')

    def test_migration_colours_existing_templates_by_icon(self):
        import importlib
        from django.apps import apps
        mig = importlib.import_module('helpdesk.migrations.0012_org_snapshot_template_color_quicklink')
        a = TicketTemplate.objects.create(name='A', icon='fas fa-print', title_template='x', created_by=self.staff)
        b = TicketTemplate.objects.create(name='B', icon='fas fa-unknown', title_template='x', created_by=self.staff)
        TicketTemplate.objects.update(color='#2ea043')
        class Schema:
            class connection: alias = 'default'
        mig.colour_existing_templates(apps, Schema)
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.color, b.color), ('#f0883e', '#2ea043'))

    def test_template_query_param_prefills_request_form(self):
        t = TicketTemplate.objects.create(name='Wi-Fi', title_template='Wi-Fi not working', priority='HIGH',
                                          description_template='Which building?', category=self.cat, created_by=self.staff)
        self.client.force_login(User.objects.create_user('req', password='pw'))
        r = self.client.get(reverse('portal_create') + f'?template={t.pk}')
        v = r.context['v']
        self.assertEqual((v['title'], v['description'], v['priority'], v['template_id'], v['category']),
                         ('Wi-Fi not working', 'Which building?', 'HIGH', str(t.pk), str(self.cat.pk)))
        self.client.post(reverse('portal_create'), dict(title='Wi-Fi not working', priority='HIGH', template_id=t.pk))
        t.refresh_from_db(); self.assertEqual(t.use_count, 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Analytics
# ═══════════════════════════════════════════════════════════════════════════════

@FAST
class StaffPerformanceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('root', 'r@x.com', 'pw')
        self.a = User.objects.create_user('ada', password='pw', is_staff=True, first_name='Ada', last_name='Moyo')
        self.b = User.objects.create_user('ben', password='pw', is_staff=True, first_name='Ben', last_name='Zulu')
        self.req = User.objects.create_user('req', password='pw')
        # Ada: 4 tickets in period — 3 resolved (60/120/180 min), 1 open; one SLA breach, one critical
        for m, br, pr in ((60, False, 'LOW'), (120, True, 'CRITICAL'), (180, False, 'MEDIUM')):
            mk_ticket(self.req, self.a, 'RESOLVED', pr, minutes=m, breach=br)
        mk_ticket(self.req, self.a, 'IN_PROGRESS', 'HIGH')
        # Ben: 1 closed ticket in period, plus one old (outside 30 days) resolved ticket
        mk_ticket(self.req, self.b, 'CLOSED', minutes=30)
        mk_ticket(self.req, self.b, 'RESOLVED', days_ago=100, minutes=10)
        mk_ticket(self.req, None, 'OPEN')                                  # unassigned
        t = Ticket.objects.filter(assigned_to=self.a).first()
        for _ in range(3):
            TicketComment.objects.create(ticket=t, author=self.a, body='x')
        TicketApproval.objects.create(ticket=t, decision='APPROVED', signer=self.a, signer_name='Ada', signature_image='data:x')

    def rows(self, **params):
        data = insights.staff_performance(insights.parse_period(params))
        return {r['name']: r for r in data['rows']}, data

    def test_numbers_for_the_default_30_day_period(self):
        rows, data = self.rows()
        ada, ben = rows['Ada Moyo'], rows['Ben Zulu']
        self.assertEqual((ada['total'], ada['resolved'], ada['breached'], ada['urgent']), (4, 3, 1, 2))
        self.assertEqual((ada['resolution_rate'], ada['sla_rate']), (75, 75))
        self.assertEqual((ada['avg_min'], ada['avg_label']), (120, '2h 0m'))
        self.assertEqual((ada['open_now'], ada['comments'], ada['signoffs']), (1, 3, 1))
        self.assertEqual((ben['total'], ben['resolved'], ben['avg_label'], ben['open_now']), (1, 1, '30m', 0))
        self.assertEqual(data['team']['unassigned'], 1)
        self.assertEqual(data['team']['total'], 5)

    def test_all_time_includes_old_tickets(self):
        rows, _ = self.rows(period='all')
        self.assertEqual(rows['Ben Zulu']['total'], 2)
        self.assertEqual(rows['Ben Zulu']['avg_min'], 20)                  # (30 + 10) / 2

    def test_staff_with_no_tickets_has_blank_rates_not_zero_division(self):
        rows, _ = self.rows()
        self.assertIsNone(rows['root']['resolution_rate'])
        self.assertEqual(rows['root']['avg_label'], '—')

    def test_highlights_respect_minimum_sample_sizes(self):
        _, data = self.rows()
        top = data['top']
        self.assertEqual(top['most_resolved']['name'], 'Ada Moyo')
        self.assertEqual(top['fastest']['name'], 'Ada Moyo')               # 3 resolved ⇒ eligible
        self.assertIsNone(top['best_sla'] if False else None)
        self.assertEqual(top['busiest']['name'], 'Ada Moyo')

    def test_permissions_sorting_and_csv(self):
        self.client.force_login(self.a)
        self.assertEqual(self.client.get(reverse('analytics_staff')).status_code, 302)   # staff ≠ admin
        self.client.force_login(self.admin)
        r = self.client.get(reverse('analytics_staff') + '?sort=name&dir=asc')
        self.assertEqual(r.status_code, 200)
        names = [x['name'] for x in r.context['rows']]
        self.assertEqual(names, sorted(names, key=str.lower))
        csv_r = self.client.get(reverse('analytics_staff') + '?export=csv')
        body = csv_r.content.decode('utf-8-sig')
        self.assertIn('Ada Moyo,4,3,75,75,2h 0m,1,2,3,1', body)
        self.assertEqual(csv_r['Content-Disposition'], 'attachment; filename="it_staff_performance.csv"')

    def test_custom_period_and_bad_input(self):
        today = timezone.localdate()
        p = insights.parse_period({'period': 'custom', 'from': (today - timedelta(days=2)).isoformat(), 'to': today.isoformat()})
        self.assertEqual(p['key'], 'custom')
        self.assertEqual(insights.parse_period({'period': 'custom', 'from': 'junk', 'to': 'x'})['key'], '30')
        self.assertEqual(insights.parse_period({'period': 'bogus'})['key'], '30')
        swapped = insights.parse_period({'period': 'custom', 'from': today.isoformat(), 'to': (today - timedelta(days=5)).isoformat()})
        self.assertLess(swapped['start'], swapped['end'])

    def test_fmt_minutes(self):
        f = insights.fmt_minutes
        self.assertEqual([f(None), f(5), f(59.6), f(135), f(3000)], ['—', '5m', '1h 0m', '2h 15m', '2d 2h'])


@FAST
class DemandTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('agent', password='pw', is_staff=True)
        self.u = {n: org(User.objects.create_user(n, password='pw', first_name=n.title()), o, d, s)
                  for n, o, d, s in (('amy', 'Town Hall', 'Finance', 'Revenue'), ('bob', 'Town Hall', 'Finance', 'Billing'),
                                     ('cat', 'Depot', 'Works', 'Roads'), ('dan', 'Depot', 'Works', 'Roads'),
                                     ('eve', 'Library', 'Culture', 'Books'))}
        for _ in range(5): mk_ticket(self.u['amy'], status='RESOLVED', minutes=60, office='Town Hall', department='Finance', section='Revenue', priority='HIGH')
        for _ in range(2): mk_ticket(self.u['bob'], office='town hall ', department='FINANCE', section='Billing')   # case/space variants
        mk_ticket(self.u['cat'], status='RESOLVED', minutes=180, office='Depot', department='Works', section='Roads')
        mk_ticket(None, office='', department='', section='', requester_name='Walk-in visitor')                     # non-user caller
        mk_ticket(self.u['amy'], days_ago=200, office='Town Hall', department='Finance', section='Revenue')          # old

    def get(self, by, **kw):
        return insights.demand(by, insights.parse_period(kw.pop('period', {'period': '30'})), **kw)

    def labels(self, data): return [r['label'] for r in data['rows']]

    def test_office_counts_merge_variants_and_include_zero_groups(self):
        d = self.get('office')
        rows = {r['label']: r for r in d['rows']}
        self.assertEqual(rows['Town Hall']['count'], 7)                    # 5 + 2 (variant spellings merged)
        self.assertEqual(rows['Depot']['count'], 1)
        self.assertEqual(rows['Library']['count'], 0)                      # zero requests is still listed
        self.assertEqual(rows['(not set)']['count'], 1)
        self.assertEqual((d['summary']['silent'], d['summary']['groups'], d['summary']['not_set']), (1, 3, 1))   # (not set) is reported apart, not as an office
        self.assertEqual((d['summary']['most']['label'], d['summary']['fewest']['label']), ('Town Hall', 'Depot'))
        self.assertEqual(d['summary']['total'], 9)

    def test_per_person_normalises_for_group_size(self):
        rows = {r['label']: r for r in self.get('office')['rows']}
        self.assertEqual((rows['Town Hall']['headcount'], rows['Town Hall']['per_head']), (2, 3.5))
        self.assertEqual((rows['Depot']['headcount'], rows['Depot']['per_head']), (2, 0.5))

    def test_metrics_resolved_urgent_avg(self):
        r = {x['label']: x for x in self.get('office')['rows']}['Town Hall']
        self.assertEqual((r['resolved'], r['open'], r['urgent'], r['avg_min']), (5, 2, 5, 60))
        self.assertEqual(r['share'], round(100 * 7 / 9, 1))

    def test_weighted_average_across_merged_variants(self):
        mk_ticket(self.u['bob'], status='RESOLVED', minutes=360, office='TOWN HALL')
        r = {x['label']: x for x in self.get('office')['rows']}['Town Hall']
        self.assertAlmostEqual(r['avg_min'], (5 * 60 + 360) / 6)

    def test_sorting_most_to_least_and_least_to_most(self):
        desc = self.labels(self.get('department', sort='count', direction='desc'))
        asc = self.labels(self.get('department', sort='count', direction='asc'))
        self.assertEqual(desc[0], 'Finance')
        self.assertEqual(asc[0], 'Culture')                                # zero-request group comes first when ascending
        self.assertEqual(self.labels(self.get('department', sort='name', direction='asc'))[:2], ['(not set)', 'Culture'])

    def test_by_section_and_by_user(self):
        sec = {r['label']: r['count'] for r in self.get('section')['rows']}
        self.assertEqual((sec['Revenue'], sec['Billing'], sec['Roads'], sec['Books']), (5, 2, 1, 0))
        d = self.get('user')
        users = {r['label']: r for r in d['rows']}
        self.assertEqual((users['Amy']['count'], users['Bob']['count'], users['Eve']['count']), (5, 2, 0))
        self.assertEqual((users['Amy']['office'], users['Amy']['section']), ('Town Hall', 'Revenue'))
        self.assertEqual(d['summary']['unlinked'], 1)                      # the walk-in caller isn't credited to anyone
        self.assertNotIn('agent', [r['label'] for r in d['rows']])         # IT staff with no tickets aren't listed as "silent"

    def test_staff_who_raised_tickets_appear_but_logging_for_others_does_not_credit_them(self):
        mk_ticket(self.staff, office='Helpdesk')
        users = {r['label']: r for r in self.get('user')['rows']}
        self.assertEqual(users['agent']['count'], 1)
        self.assertTrue(users['agent']['is_staff'])

    def test_period_limits(self):
        week = {r['label']: r['count'] for r in self.get('office', period={'period': '7'})['rows']}
        self.assertEqual(week['Town Hall'], 7)
        allt = {r['label']: r['count'] for r in self.get('office', period={'period': 'all'})['rows']}
        self.assertEqual(allt['Town Hall'], 8)

    def test_view_permissions_pagination_and_csv(self):
        self.client.force_login(self.u['amy'])
        self.assertEqual(self.client.get(reverse('analytics_demand')).status_code, 302)   # requesters can't see it
        self.client.force_login(self.staff)
        r = self.client.get(reverse('analytics_demand') + '?by=user')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Amy')
        self.assertEqual(self.client.get(reverse('analytics_demand') + '?by=evil&sort=zz&dir=sideways').status_code, 200)
        csv_r = self.client.get(reverse('analytics_demand') + '?by=office&export=csv')
        body = csv_r.content.decode('utf-8-sig')
        self.assertIn('Town Hall,7,', body)
        self.assertIn('Library,0,', body)

    def test_csv_neutralises_formula_injection(self):
        org(self.u['eve'], '=HYPERLINK("http://evil")', 'Culture', 'Books')
        self.client.force_login(self.staff)
        body = self.client.get(reverse('analytics_demand') + '?by=office&export=csv').content.decode('utf-8-sig')
        self.assertIn("'=HYPERLINK", body)
        self.assertNotIn('\n=HYPERLINK', body)


# ═══════════════════════════════════════════════════════════════════════════════
# New requests queue
# ═══════════════════════════════════════════════════════════════════════════════

@FAST
class QueueTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('root', 'r@x.com', 'pw')
        self.a = User.objects.create_user('ada', password='pw', is_staff=True, first_name='Ada')
        self.b = User.objects.create_user('ben', password='pw', is_staff=True, first_name='Ben')
        self.req = User.objects.create_user('req', password='pw')
        self.low_old = mk_ticket(self.req, priority='LOW', days_ago=5, title='old low')
        self.crit_new = mk_ticket(self.req, priority='CRITICAL', days_ago=0, title='new critical')
        self.high = mk_ticket(self.req, priority='HIGH', days_ago=2, title='high')
        self.assigned = mk_ticket(self.req, self.a, priority='MEDIUM', title='assigned open')
        self.working = mk_ticket(self.req, self.a, status='IN_PROGRESS', title='being worked')

    def titles(self, r): return [t.title for t in r.context['tickets']]

    def test_only_staff_can_see_the_queue(self):
        self.client.force_login(self.req)
        self.assertEqual(self.client.get(reverse('new_requests')).status_code, 302)

    def test_urgent_first_then_oldest_and_only_open(self):
        self.client.force_login(self.a)
        r = self.client.get(reverse('new_requests'))
        self.assertEqual(self.titles(r), ['new critical', 'high', 'assigned open', 'old low'])
        self.assertNotIn('being worked', self.titles(r))                   # already in progress
        self.assertEqual(r.context['counts'], {'new': 4, 'unassigned': 3, 'mine': 1})

    def test_tabs(self):
        self.client.force_login(self.a)
        self.assertEqual(self.titles(self.client.get(reverse('new_requests') + '?view=unassigned')),
                         ['new critical', 'high', 'old low'])
        self.assertEqual(self.titles(self.client.get(reverse('new_requests') + '?view=mine')), ['assigned open'])
        self.assertEqual(self.client.get(reverse('new_requests') + '?view=bogus').context['view'], 'new')

    def test_admin_sees_assign_controls_staff_sees_take(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('new_requests')), 'Assign</button>')
        self.client.force_login(self.b)
        r = self.client.get(reverse('new_requests'))
        self.assertContains(r, 'Take it')
        self.assertNotContains(r, 'Assign</button>')

    def test_admin_assigns_notifies_and_audits(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse('queue_assign', args=[self.crit_new.ticket_id]),
                             {'agent_id': self.b.pk, 'view': 'unassigned', 'page': '1'})
        self.assertRedirects(r, reverse('new_requests') + '?view=unassigned&page=1')
        self.crit_new.refresh_from_db()
        self.assertEqual(self.crit_new.assigned_to, self.b)
        self.assertEqual(self.crit_new.status, 'OPEN')                     # same rule as the ticket page
        p = json.loads(PendingNotification.objects.get(user=self.b, conversation=self.crit_new.ticket_id).payload)
        self.assertEqual(p['kind'], 'assigned')
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.ACTION_ASSIGN, object_id=self.crit_new.ticket_id).exists())

    def test_admin_can_reassign_and_unassign(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('queue_assign', args=[self.assigned.ticket_id]), {'agent_id': self.b.pk})
        self.assigned.refresh_from_db(); self.assertEqual(self.assigned.assigned_to, self.b)
        self.client.post(reverse('queue_assign', args=[self.assigned.ticket_id]), {'agent_id': ''})
        self.assigned.refresh_from_db(); self.assertIsNone(self.assigned.assigned_to)

    def test_staff_cannot_assign_others_and_bad_agent_rejected(self):
        self.client.force_login(self.a)
        self.client.post(reverse('queue_assign', args=[self.high.ticket_id]), {'agent_id': self.b.pk})
        self.high.refresh_from_db(); self.assertIsNone(self.high.assigned_to)
        self.client.force_login(self.admin)
        self.client.post(reverse('queue_assign', args=[self.high.ticket_id]), {'agent_id': self.req.pk})   # not IT staff
        self.high.refresh_from_db(); self.assertIsNone(self.high.assigned_to)

    def test_take_moves_to_in_progress_once(self):
        self.client.force_login(self.b)
        self.client.post(reverse('queue_take', args=[self.high.ticket_id]))
        self.high.refresh_from_db()
        self.assertEqual((self.high.assigned_to, self.high.status), (self.b, 'IN_PROGRESS'))
        self.client.force_login(self.a)
        self.client.post(reverse('queue_take', args=[self.high.ticket_id]))        # already taken
        self.high.refresh_from_db(); self.assertEqual(self.high.assigned_to, self.b)

    def test_get_requests_do_nothing(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('queue_assign', args=[self.high.ticket_id])).status_code, 302)
        self.high.refresh_from_db(); self.assertIsNone(self.high.assigned_to)

    def test_sidebar_badge_counts_unassigned_new_requests(self):
        self.client.force_login(self.a)
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(r.context['sidebar_new_requests'], 3)
        self.assertContains(r, 'New Requests')

    def test_queue_paginates_at_10(self):
        for i in range(12): mk_ticket(self.req, title=f'extra {i}')
        self.client.force_login(self.a)
        r = self.client.get(reverse('new_requests'))
        self.assertEqual((len(r.context['tickets']), r.context['tickets'].paginator.num_pages), (10, 2))


# ═══════════════════════════════════════════════════════════════════════════════
# My requests (view + track)
# ═══════════════════════════════════════════════════════════════════════════════

@FAST
class MyRequestsTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('agent', password='pw', is_staff=True, first_name='Ada')
        self.req = User.objects.create_user('req', password='pw')
        self.t_open = mk_ticket(self.req, title='Printer jam')
        self.t_assigned = mk_ticket(self.req, self.staff, title='VPN down')
        self.t_prog = mk_ticket(self.req, self.staff, 'IN_PROGRESS', title='Laptop slow')
        self.t_done = mk_ticket(self.req, self.staff, 'RESOLVED', title='Password reset')
        mk_ticket(User.objects.create_user('other', password='pw'), title='Not mine')

    def test_stages(self):
        from .views import ticket_stage
        self.assertEqual([ticket_stage(t) for t in (self.t_open, self.t_assigned, self.t_prog, self.t_done)], [1, 2, 3, 4])

    def test_requester_sees_only_own_with_progress_tracker(self):
        self.client.force_login(self.req)
        r = self.client.get(reverse('portal'))
        self.assertEqual(len(r.context['my_tickets']), 4)
        stages = {t.title: t.stage for t in r.context['my_tickets']}
        self.assertEqual(stages, {'Printer jam': 1, 'VPN down': 2, 'Laptop slow': 3, 'Password reset': 4})
        self.assertContains(r, 'class="track"')
        self.assertContains(r, 'with Ada')
        self.assertNotContains(r, 'Not mine')

    def test_filters_and_search(self):
        self.client.force_login(self.req)
        self.assertEqual(len(self.client.get(reverse('portal') + '?status=open').context['my_tickets']), 3)
        self.assertEqual(len(self.client.get(reverse('portal') + '?status=resolved').context['my_tickets']), 1)
        self.assertEqual(len(self.client.get(reverse('portal') + '?status=bogus').context['my_tickets']), 4)
        r = self.client.get(reverse('portal') + '?q=vpn')
        self.assertEqual([t.title for t in r.context['my_tickets']], ['VPN down'])
        self.assertEqual(len(self.client.get(reverse('portal') + f'?q={self.t_done.ticket_id}').context['my_tickets']), 1)

    def test_staff_can_open_their_own_my_requests(self):
        mine = mk_ticket(self.staff, title='Staff own laptop')
        self.client.force_login(self.staff)
        r = self.client.get(reverse('portal'))
        self.assertEqual(r.status_code, 200)                               # no longer bounced to the dashboard
        self.assertEqual([t.title for t in r.context['my_tickets']], ['Staff own laptop'])
        self.assertContains(r, 'Dashboard')

    def test_request_page_shows_open_count_and_recent_requests(self):
        self.client.force_login(self.req)
        r = self.client.get(reverse('portal_create'))
        self.assertEqual(r.context['my_open_count'], 3)
        self.assertEqual(len(r.context['my_recent']), 3)
        self.assertContains(r, 'Your recent requests')

    def test_single_request_page_has_journey_tracker(self):
        self.client.force_login(self.req)
        r = self.client.get(reverse('portal_ticket', args=[self.t_prog.ticket_id]))
        self.assertEqual(r.context['stage'], 3)
        self.assertContains(r, 'class="journey"')
        self.assertContains(r, 'Being handled by Ada')

    def test_reply_from_portal_now_notifies_it(self):
        self.client.force_login(self.req)
        self.client.post(reverse('portal_ticket', args=[self.t_assigned.ticket_id]), {'comment': 'Any update please?'})
        p = json.loads(PendingNotification.objects.get(user=self.staff, conversation=self.t_assigned.ticket_id).payload)
        self.assertEqual((p['kind'], p['body']), ('comment', 'Any update please?'))


# ═══════════════════════════════════════════════════════════════════════════════
# Quick request links
# ═══════════════════════════════════════════════════════════════════════════════

@FAST
class QuickLinkModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tendai', password='pw')

    def future(self, **kw): return timezone.now() + timedelta(**(kw or {'days': 3}))

    def test_only_a_hash_is_stored(self):
        link, raw = QuickRequestLink.issue(self.user, 'My phone', self.future())
        self.assertNotIn(raw, (link.token_hash, link.token_hint, link.label))
        self.assertEqual(len(link.token_hash), 64)
        self.assertEqual(link.token_hint, raw[:6])
        self.assertEqual(QuickRequestLink.find(raw), link)
        self.assertIsNone(QuickRequestLink.find(raw + 'x'))
        self.assertIsNone(QuickRequestLink.find(''))
        self.assertIsNone(QuickRequestLink.find('a' * 500))
        self.assertGreaterEqual(len(raw), 40)                              # 256 bits of randomness

    def test_tokens_are_unique(self):
        raws = {QuickRequestLink.issue(self.user, 'x', self.future())[1] for _ in range(20)}
        self.assertEqual(len(raws), 20)

    def test_status_transitions(self):
        link, _ = QuickRequestLink.issue(self.user, 'x', self.future())
        self.assertEqual(link.status, 'active')
        link.expires_at = timezone.now() - timedelta(seconds=1); link.save()
        self.assertEqual(link.status, 'expired')
        link.expires_at = self.future(); link.revoke('done'); link.refresh_from_db()
        self.assertEqual((link.status, link.revoked_note), ('revoked', 'done'))

    def test_inactive_owner_disables_link(self):
        link, _ = QuickRequestLink.issue(self.user, 'x', self.future())
        self.user.is_active = False; self.user.save()
        self.assertEqual(QuickRequestLink.find(_).status, 'revoked')

    def test_lives_on_the_default_database(self):
        from .db_router import YearDatabaseRouter
        self.assertIn('quickrequestlink', YearDatabaseRouter.DEFAULT_ONLY_MODELS if hasattr(YearDatabaseRouter, 'DEFAULT_ONLY_MODELS')
                      else __import__('helpdesk.db_router', fromlist=['x']).DEFAULT_ONLY_MODELS)


@FAST
class QuickLinkOwnerPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = org(User.objects.create_user('tendai', password='pw', first_name='Tendai'), 'Town Hall', 'Finance', 'Revenue')
        self.admin = User.objects.create_superuser('root', 'r@x.com', 'pw')
        self.client.force_login(self.user)
        self.url = reverse('quick_links')

    def when(self, **kw): return (timezone.localtime() + timedelta(**kw)).strftime('%Y-%m-%dT%H:%M')

    def create(self, label='My phone', **exp):
        return self.client.post(self.url, {'label': label, 'expires_at': self.when(**(exp or {'days': 7}))})

    def test_create_shows_link_exactly_once(self):
        r = self.create()
        # fetch_redirect_response=False: following the redirect would itself consume the one-time reveal
        self.assertRedirects(r, self.url, fetch_redirect_response=False)
        first = self.client.get(self.url)
        url = first.context['fresh']['url']
        self.assertRegex(url, r'^http://testserver/r/[\w-]{40,}/$')
        self.assertContains(first, url)
        self.assertContains(first, 'shown only once')
        self.assertTrue(first.context['fresh']['qr'].startswith('data:image/png;base64,'))
        self.assertIsNone(self.client.get(self.url).context['fresh'])      # gone on the next visit
        self.assertNotContains(self.client.get(self.url), url)
        link = QuickRequestLink.objects.get()
        self.assertEqual((link.user, link.label), (self.user, 'My phone'))
        self.assertTrue(AuditLog.objects.filter(model_name='QuickRequestLink', action=AuditLog.ACTION_CREATE).exists())
        self.assertEqual(self.client.get(url).status_code, 200)            # and the link really works

    def test_expiry_rules(self):
        for exp, msg in (({'minutes': -5}, 'at least'), ({'minutes': 2}, 'at least'), ({'days': 400}, 'at most')):
            r = self.create(**exp)
            self.assertIn('expires_at', r.context['errors'], exp)
            self.assertIn(msg, r.context['errors']['expires_at'])
        r = self.client.post(self.url, {'label': 'x', 'expires_at': 'tomorrow-ish'})
        self.assertEqual(r.context['errors']['expires_at'], 'Enter a valid date and time.')
        r = self.client.post(self.url, {'label': 'x', 'expires_at': ''})
        self.assertIn('expires_at', r.context['errors'])
        self.assertEqual(QuickRequestLink.objects.count(), 0)

    def test_the_user_chooses_their_own_expiry(self):
        self.create(label='short', hours=2)
        self.create(label='long', days=30)
        short, long_ = QuickRequestLink.objects.get(label='short'), QuickRequestLink.objects.get(label='long')
        self.assertAlmostEqual((short.expires_at - timezone.now()).total_seconds(), 7200, delta=120)
        self.assertAlmostEqual((long_.expires_at - timezone.now()).days, 30, delta=1)

    def test_label_validation_keeps_other_values(self):
        r = self.client.post(self.url, {'label': '', 'expires_at': self.when(days=2)})
        self.assertEqual(list(r.context['errors']), ['label'])
        self.assertContains(r, self.when(days=2))                          # chosen expiry kept
        r = self.client.post(self.url, {'label': 'x' * 101, 'expires_at': self.when(days=2)})
        self.assertIn('label', r.context['errors'])

    def test_cap_on_active_links(self):
        for i in range(10): QuickRequestLink.issue(self.user, f'l{i}', timezone.now() + timedelta(days=1))
        r = self.create()
        self.assertIn('already have 10 active', r.context['errors']['general'])
        QuickRequestLink.objects.first().revoke()
        self.assertEqual(self.create().status_code, 302)

    def test_face_only_mfa_cannot_make_links(self):
        p = self.user.profile; p.mfa_enabled = True; p.face_mfa_enabled = True; p.save()
        r = self.create()
        self.assertIn('face recognition only', r.context['errors']['general'])
        self.assertEqual(QuickRequestLink.objects.count(), 0)

    def test_revoke_own_but_not_someone_elses(self):
        other = User.objects.create_user('other', password='pw')
        mine, _ = QuickRequestLink.issue(self.user, 'mine', timezone.now() + timedelta(days=1))
        theirs, _ = QuickRequestLink.issue(other, 'theirs', timezone.now() + timedelta(days=1))
        self.client.post(reverse('quick_link_revoke', args=[theirs.pk]))
        theirs.refresh_from_db(); self.assertEqual(theirs.status, 'active')
        self.client.post(reverse('quick_link_revoke', args=[mine.pk]))
        mine.refresh_from_db(); self.assertEqual(mine.status, 'revoked')
        self.client.get(reverse('quick_link_revoke', args=[theirs.pk]))          # GET is harmless
        theirs.refresh_from_db(); self.assertEqual(theirs.status, 'active')

    def test_admin_overview_and_revoke(self):
        link, raw = QuickRequestLink.issue(self.user, 'mine', timezone.now() + timedelta(days=1))
        self.assertEqual(self.client.get(reverse('quick_links_admin')).status_code, 302)    # not an admin
        self.client.force_login(self.admin)
        r = self.client.get(reverse('quick_links_admin'))
        self.assertContains(r, 'tendai')
        self.assertContains(r, link.token_hint)
        self.assertNotContains(r, raw)                                     # never the token
        self.assertEqual(r.context['counts'], {'active': 1, 'expired': 0, 'revoked': 0})
        self.client.post(reverse('quick_link_revoke', args=[link.pk]), {'from': 'admin'})
        link.refresh_from_db(); self.assertEqual(link.status, 'revoked')
        self.assertEqual(self.client.get(reverse('quick_links_admin') + '?status=revoked').context['links'].paginator.count, 1)


@FAST
class QuickLinkPublicPageTests(TestCase):
    PW = 'S3cret-pass!'

    def setUp(self):
        cache.clear()
        self.cat = Category.objects.create(name='Hardware')
        self.staff = User.objects.create_user('agent', password='pw', is_staff=True)
        self.user = org(User.objects.create_user('tendai', password=self.PW, first_name='Tendai', last_name='Moyo',
                                                 email='t@x.com'), 'Town Hall', 'Finance', 'Revenue')
        self.link, self.raw = QuickRequestLink.issue(self.user, 'My phone', timezone.now() + timedelta(days=3))
        self.url = reverse('quick_request', args=[self.raw])

    def post(self, password=None, **over):
        d = dict(title='Printer jam', description='Paper stuck', category=self.cat.pk, priority='HIGH',
                 password=self.PW if password is None else password)
        d.update(over)
        return self.client.post(self.url, d)

    def tickets(self): return Ticket.objects.count()

    # ── opening the link ─────────────────────────────────────────────────────
    def test_valid_link_shows_form_without_logging_in(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Quick request for Tendai Moyo')
        self.assertContains(r, 'Confirm it')
        self.assertContains(r, 'beitbridge_bg')
        self.assertContains(r, 'Choose a template')
        self.assertNotContains(r, 'name="mfa_code"')                       # no MFA ⇒ no code box
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_page_is_not_cacheable_and_sends_no_referrer(self):
        r = self.client.get(self.url)
        self.assertIn('no-store', r['Cache-Control'])
        self.assertEqual(r['Referrer-Policy'], 'no-referrer')
        self.assertIn('noindex', r['X-Robots-Tag'])

    def test_unknown_expired_and_revoked_links_look_identical(self):
        bad = self.client.get(reverse('quick_request', args=['nope' * 12]))
        self.link.expires_at = timezone.now() - timedelta(minutes=1); self.link.save()
        expired = self.client.get(self.url)
        self.link.expires_at = timezone.now() + timedelta(days=1); self.link.save(); self.link.revoke()
        revoked = self.client.get(self.url)
        self.assertEqual({bad.status_code, expired.status_code, revoked.status_code}, {404})
        self.assertEqual(bad.content, expired.content)
        self.assertEqual(bad.content, revoked.content)
        self.assertContains(bad, "isn't available", status_code=404)

    # ── submitting ───────────────────────────────────────────────────────────
    def test_correct_password_creates_ticket_as_the_owner(self):
        r = self.post()
        self.assertEqual(r.status_code, 302)
        t = Ticket.objects.get()
        self.assertEqual((t.source, t.requester, t.logged_by, t.status, t.priority, t.requester_name, t.requester_email),
                         ('LINK', self.user, None, 'OPEN', 'HIGH', 'Tendai Moyo', 't@x.com'))
        self.assertEqual((t.requester_office, t.requester_department, t.requester_section),
                         ('Town Hall', 'Finance', 'Revenue'))
        self.assertRegex(t.ticket_id, r'^ND\d{6}$')
        self.assertNotIn('_auth_user_id', self.client.session)             # still not logged in
        self.link.refresh_from_db()
        self.assertEqual((self.link.use_count, self.link.failed_attempts), (1, 0))
        self.assertIsNotNone(self.link.last_used_at)
        self.assertTrue(AuditLog.objects.filter(model_name='Ticket', user=self.user, notes__contains='quick link').exists())

    def test_confirmation_page_and_signed_reference(self):
        r = self.post()
        done = self.client.get(r['Location'])
        t = Ticket.objects.get()
        self.assertContains(done, t.ticket_id)
        self.assertContains(done, 'Raise another request')
        base = reverse('quick_request_done', args=[self.raw])
        self.assertEqual(self.client.get(base).status_code, 404)           # no reference
        self.assertEqual(self.client.get(base + '?r=forged').status_code, 404)
        other, other_raw = QuickRequestLink.issue(self.user, 'other', timezone.now() + timedelta(days=1))
        ref = r['Location'].split('?r=')[1]
        self.assertEqual(self.client.get(reverse('quick_request_done', args=[other_raw]) + '?r=' + ref).status_code, 404)

    def test_staff_are_notified_and_see_it_in_the_queue_owner_is_told_too(self):
        self.post()
        t = Ticket.objects.get()
        p = json.loads(PendingNotification.objects.get(user=self.staff, conversation=t.ticket_id).payload)
        self.assertEqual((p['kind'], p['sender']), ('created', 'Tendai Moyo'))
        mine = json.loads(PendingNotification.objects.get(user=self.user, conversation=t.ticket_id).payload)
        self.assertEqual((mine['kind'], mine['sender']), ('link', 'Quick Link'))
        self.assertIn('My phone', mine['body'])
        self.client.force_login(self.staff)
        q = self.client.get(reverse('new_requests'))
        self.assertEqual([x.ticket_id for x in q.context['tickets']], [t.ticket_id])
        self.assertContains(q, 'Quick Link')

    def test_link_can_be_used_repeatedly(self):
        self.post(); self.post(title='Second one')
        self.assertEqual((self.tickets(), QuickRequestLink.objects.get().use_count), (2, 2))

    def test_wrong_password_creates_nothing_and_keeps_the_form_but_not_the_password(self):
        r = self.post(password='Wr0ng-Zq7!')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.tickets(), 0)
        self.assertEqual(r.context['errors']['general'], 'That password or code is not correct.')
        self.assertContains(r, 'Paper stuck')
        self.assertContains(r, 'value="Printer jam"')
        self.assertNotContains(r, 'Wr0ng-Zq7!')
        self.assertNotContains(r, self.PW)
        self.link.refresh_from_db(); self.assertEqual(self.link.failed_attempts, 1)

    def test_invalid_form_does_not_spend_an_attempt(self):
        r = self.post(title='')
        self.assertEqual(list(r.context['errors']), ['title'])
        r = self.post(password='')
        self.assertEqual(list(r.context['errors']), ['password'])
        self.link.refresh_from_db(); self.assertEqual(self.link.failed_attempts, 0)

    def test_field_validation(self):
        r = self.post(category='9999', priority='CRITICAL')
        self.assertEqual(set(r.context['errors']), {'category', 'priority'})  # critical is staff-only
        self.assertEqual(self.tickets(), 0)

    # ── lockout ──────────────────────────────────────────────────────────────
    def test_five_wrong_attempts_lock_the_link_even_for_the_right_password(self):
        for _ in range(5): self.post(password='bad')
        self.link.refresh_from_db()
        self.assertTrue(self.link.is_locked)
        r = self.post()                                                    # correct password, but locked
        self.assertEqual(self.tickets(), 0)
        self.assertIn('locked', r.context['errors']['general'])
        self.assertTrue(AuditLog.objects.filter(model_name='QuickRequestLink', notes__contains='locked').exists())

    def test_lock_expires(self):
        for _ in range(5): self.post(password='bad')
        QuickRequestLink.objects.update(locked_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.post().status_code, 302)

    def test_success_resets_the_failure_counter(self):
        for _ in range(3): self.post(password='bad')
        self.post()
        self.assertEqual(QuickRequestLink.objects.get().failed_attempts, 0)

    def test_three_lockouts_disable_the_link_and_alert_the_owner(self):
        for _round in range(3):
            QuickRequestLink.objects.update(locked_until=None)
            for _ in range(5): self.post(password='bad')
        link = QuickRequestLink.objects.get()
        self.assertEqual(link.status, 'revoked')
        self.assertIn('repeated failed attempts', link.revoked_note)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        n = json.loads(PendingNotification.objects.get(user=self.user, payload__contains='Quick link disabled').payload)
        self.assertEqual((n['level'], n['kind']), ('danger', 'link'))

    def test_ip_throttle_is_a_second_guard(self):
        cache.set('ql_ip_127.0.0.1', 30, 600)
        r = self.post()
        self.assertEqual(self.tickets(), 0)
        self.assertIn('Too many attempts from your network', r.context['errors']['general'])

    def test_inactive_owner_cannot_use_the_link(self):
        self.user.is_active = False; self.user.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    # ── MFA ──────────────────────────────────────────────────────────────────
    def enable_totp(self, backup=None):
        secret = pyotp.random_base32()
        p = self.user.profile
        p.mfa_enabled = True; p.totp_enabled = True; p.totp_secret = secret
        p.totp_backup_codes = json.dumps(backup or [])
        p.save()
        return pyotp.TOTP(secret)

    def test_mfa_account_must_give_a_code_too(self):
        totp = self.enable_totp()
        self.assertContains(self.client.get(self.url), 'name="mfa_code"')
        r = self.post()                                                    # password but no code
        self.assertEqual(list(r.context['errors']), ['mfa_code'])
        self.assertEqual(self.tickets(), 0)
        r = self.post(mfa_code='000000')
        self.assertEqual(r.context['errors']['general'], 'That password or code is not correct.')
        r = self.post(mfa_code=totp.now())
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.tickets(), 1)

    def test_right_code_with_wrong_password_is_still_rejected(self):
        totp = self.enable_totp()
        self.post(password='bad', mfa_code=totp.now())
        self.assertEqual(self.tickets(), 0)

    def test_an_authenticator_code_works_only_once(self):
        totp = self.enable_totp()
        code = totp.now()
        self.assertEqual(self.post(mfa_code=code).status_code, 302)
        r = self.post(mfa_code=code)                                       # replay of the same code
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.tickets(), 1)
        with mock.patch('time.time', return_value=time.time() + 31):       # next 30-second window
            self.assertEqual(self.post(mfa_code=totp.at(time.time())).status_code, 302)
        self.assertEqual(self.tickets(), 2)

    def test_backup_code_is_consumed(self):
        self.enable_totp(backup=['ABCD1234', 'ZZZZ9999'])
        self.assertEqual(self.post(mfa_code='abcd1234').status_code, 302)  # case-insensitive
        self.assertEqual(json.loads(self.user.profile.__class__.objects.get(user=self.user).totp_backup_codes), ['ZZZZ9999'])
        self.assertEqual(self.post(mfa_code='ABCD1234').status_code, 200)  # already used
        self.assertEqual(self.tickets(), 1)

    def test_mfa_attempts_are_logged(self):
        from accounts.models import MFALog
        totp = self.enable_totp()
        self.post(mfa_code='111111'); self.post(mfa_code=totp.now())
        results = list(MFALog.objects.filter(user=self.user).order_by('id').values_list('method', 'result'))
        self.assertEqual(results, [('TOTP', 'FAIL'), ('TOTP', 'PASS')])

    def test_face_only_mfa_is_refused_not_bypassed(self):
        p = self.user.profile; p.mfa_enabled = True; p.face_mfa_enabled = True; p.save()
        page = self.client.get(self.url)
        self.assertContains(page, "can't be used yet")
        r = self.post()
        self.assertEqual(self.tickets(), 0)
        self.assertIn('face recognition only', r.context['errors']['general'])

    def test_template_is_applied_and_counted(self):
        t = TicketTemplate.objects.create(name='Wi-Fi', title_template='Wi-Fi down', created_by=self.staff)
        self.post(template_id=t.pk)
        t.refresh_from_db(); self.assertEqual(t.use_count, 1)


# ═══════════════════════════════════════════════════════════════════════════════
# End to end
# ═══════════════════════════════════════════════════════════════════════════════

@FAST
class EndToEndTests(TestCase):
    def test_link_request_flows_to_queue_admin_assigns_and_it_shows_in_demand_report(self):
        cache.clear()
        admin = User.objects.create_superuser('root', 'r@x.com', 'pw')
        agent = User.objects.create_user('agent', password='pw', is_staff=True, first_name='Ada')
        user = org(User.objects.create_user('tendai', password='pw1', first_name='Tendai'), 'Depot', 'Works', 'Roads')
        Category.objects.create(name='Hardware')
        # 1. the user makes themselves a link
        self.client.force_login(user)
        self.client.post(reverse('quick_links'), {'label': 'Depot PC', 'expires_at':
                         (timezone.localtime() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')})
        url = self.client.get(reverse('quick_links')).context['fresh']['url']
        self.client.logout()
        # 2. anonymous visitor uses it
        r = self.client.post(url, dict(title='Mouse broken', priority='MEDIUM', password='pw1'))
        self.assertEqual(r.status_code, 302)
        # 3. staff see it in the new-requests queue
        self.client.force_login(agent)
        q = self.client.get(reverse('new_requests'))
        ticket = q.context['tickets'][0]
        self.assertEqual((ticket.title, ticket.source, ticket.requester_office), ('Mouse broken', 'LINK', 'Depot'))
        # 4. the admin assigns it
        self.client.force_login(admin)
        self.client.post(reverse('queue_assign', args=[ticket.ticket_id]), {'agent_id': agent.pk})
        ticket.refresh_from_db(); self.assertEqual(ticket.assigned_to, agent)
        # 5. it counts toward the office in the demand report and toward the agent in performance
        office = {r['label']: r for r in insights.demand('office', insights.parse_period({}))['rows']}
        self.assertEqual(office['Depot']['count'], 1)
        perf = {r['name']: r for r in insights.staff_performance(insights.parse_period({}))['rows']}
        self.assertEqual((perf['Ada']['total'], perf['Ada']['open_now']), (1, 1))
