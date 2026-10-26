"""Ticket-list filters (office / department / section / requester / time window) and the report drill-downs."""
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import insights
from .models import Category, Ticket

FAST = override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])


def mk(requester=None, days_ago=0, office='', department='', section='', status='OPEN', title='Issue', **kw):
    cat = Category.objects.first() or Category.objects.create(name='General')
    t = Ticket.objects.create(title=title, description='d', category=cat, requester=requester,
                              requester_office=office, requester_department=department,
                              requester_section=section, **kw)
    Ticket.objects.filter(pk=t.pk).update(status=status, created_at=timezone.now() - timedelta(days=days_ago, minutes=1))
    return t


@FAST
class TicketListFilterTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('agent', password='pw', is_staff=True)
        self.amy = User.objects.create_user('amy', password='pw', first_name='Amy', last_name='Moyo')
        self.bob = User.objects.create_user('bob', password='pw')
        self.a1 = mk(self.amy, office='Town Hall', department='Finance', section='Revenue', title='a1')
        self.a2 = mk(self.amy, office='town hall ', department='FINANCE', section='Billing', title='a2')   # variants of the same office
        self.b1 = mk(self.bob, office='Depot', department='Works', section='Roads', title='b1', status='RESOLVED')
        self.n1 = mk(None, office='', title='n1', requester_name='Visitor')
        self.old = mk(self.amy, days_ago=60, office='Town Hall', department='Finance', title='old')
        self.client.force_login(self.staff)

    def titles(self, **params):
        r = self.client.get(reverse('ticket_list'), params)
        self.assertEqual(r.status_code, 200)
        return sorted(t.title for t in r.context['tickets']), r

    def test_office_matches_the_way_the_report_groups_it(self):
        self.assertEqual(self.titles(office='Town Hall')[0], ['a1', 'a2', 'old'])        # case / spacing variants included
        self.assertEqual(self.titles(office='TOWN  HALL')[0], ['a1', 'a2', 'old'])
        self.assertEqual(self.titles(office='Depot')[0], ['b1'])
        self.assertEqual(self.titles(office='Nowhere')[0], [])

    def test_department_and_section(self):
        self.assertEqual(self.titles(department='finance')[0], ['a1', 'a2', 'old'])
        self.assertEqual(self.titles(section='Billing')[0], ['a2'])
        self.assertEqual(self.titles(office='Town Hall', section='Revenue')[0], ['a1'])

    def test_not_set_token(self):
        self.assertEqual(self.titles(office='__none__')[0], ['n1'])
        self.assertEqual(self.titles(section='__none__')[0], ['n1', 'old'])

    def test_requester_filter(self):
        self.assertEqual(self.titles(requester=self.amy.pk)[0], ['a1', 'a2', 'old'])
        self.assertEqual(self.titles(requester='abc')[0], ['a1', 'a2', 'b1', 'n1', 'old'])      # junk ignored, not a crash

    def test_exact_time_window(self):
        since = (timezone.now() - timedelta(days=30)).isoformat()
        self.assertEqual(self.titles(since=since)[0], ['a1', 'a2', 'b1', 'n1'])
        until = (timezone.now() - timedelta(days=30)).isoformat()
        self.assertEqual(self.titles(until=until)[0], ['old'])
        self.assertEqual(self.titles(since='not a date')[0], ['a1', 'a2', 'b1', 'n1', 'old'])
        self.assertEqual(self.titles(since=(timezone.now() - timedelta(days=30)).date().isoformat())[0], ['a1', 'a2', 'b1', 'n1'])

    def test_combines_with_the_existing_filters(self):
        self.assertEqual(self.titles(office='Depot', status='RESOLVED')[0], ['b1'])
        self.assertEqual(self.titles(office='Depot', status='OPEN')[0], [])

    def test_chips_show_each_applied_filter_with_a_link_that_removes_only_it(self):
        _, r = self.titles(office='Town Hall', requester=self.amy.pk, status='OPEN')
        labels = [c['label'] for c in r.context['chips']]
        self.assertEqual(labels, ['Office: Town Hall', 'Requester: Amy Moyo'])
        self.assertContains(r, 'Filtered by:')
        remove_office = parse_qs(urlparse(r.context['chips'][0]['remove']).query)
        self.assertEqual(sorted(remove_office), ['requester', 'status'])               # everything else is kept
        _, r2 = self.titles(office='__none__', since=(timezone.now() - timedelta(days=3)).isoformat())
        self.assertEqual(r2.context['chips'][0]['label'], 'Office: (not set)')
        old_end = self.client.get(reverse('ticket_list'), {'until': (timezone.now() - timedelta(days=5)).isoformat()})
        self.assertTrue(old_end.context['chips'][0]['label'].startswith('Created until '))     # a real past end date IS shown
        self.assertTrue(r2.context['chips'][1]['label'].startswith('Created since '))

    def test_filter_bar_lists_known_values_for_staff_and_keeps_report_filters_on_submit(self):
        _, r = self.titles(requester=self.amy.pk, since='2026-01-01T00:00:00+00:00')
        self.assertEqual(r.context['org_lists']['office'], ['Depot', 'Town Hall'])      # variants merged, blanks omitted
        self.assertContains(r, '<option value="Depot"')
        self.assertContains(r, 'name="requester" value="%s"' % self.amy.pk)
        self.assertContains(r, 'name="since" value="2026-01-01T00:00:00+00:00"')

    def test_the_most_common_spelling_is_what_people_see(self):
        mk(self.amy, office='TOWN HALL')                                       # 1 vs 2 for "Town Hall"
        _, r = self.titles()
        self.assertEqual(r.context['org_lists']['office'], ['Depot', 'Town Hall'])
        rows = {x['label'] for x in insights.demand('office', insights.parse_period({'period': 'all'}))['rows']}
        self.assertIn('Town Hall', rows)
        self.assertNotIn('TOWN HALL', rows)

    def test_selected_option_is_marked(self):
        _, r = self.titles(office='town hall')
        self.assertRegex(r.content.decode(), r'<option value="Town Hall" selected>')

    def test_date_presets_no_longer_drop_the_other_filters(self):
        _, r = self.titles(office='Depot', priority='MEDIUM', status='RESOLVED')
        for key in ('today', 'week', 'month'):
            q = parse_qs(urlparse(r.context['preset_urls'][key]).query)
            self.assertEqual((q['office'], q['priority'], q['status'], q['date_preset']), (['Depot'], ['MEDIUM'], ['RESOLVED'], [key]))
        self.assertNotIn('date_from', r.context['preset_urls']['week'])

    def test_requesters_see_only_their_own_and_no_org_controls(self):
        self.client.force_login(self.amy)
        r = self.client.get(reverse('ticket_list'), {'office': 'Depot'})
        self.assertEqual(list(r.context['tickets']), [])                               # Depot belongs to bob
        r = self.client.get(reverse('ticket_list'))
        self.assertEqual(sorted(t.title for t in r.context['tickets']), ['a1', 'a2', 'old'])
        self.assertEqual(r.context['org_lists'], {})
        self.assertNotContains(r, "Requester's:")


@FAST
class ReportDrillDownTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('agent', password='pw', is_staff=True)
        self.amy = User.objects.create_user('amy', password='pw', first_name='Amy')
        self.bob = User.objects.create_user('bob', password='pw', first_name='Bob')
        for _ in range(3): mk(self.amy, office='Town Hall', department='Finance', section='Revenue')
        mk(self.amy, office='town hall', department='finance', section='Revenue')
        mk(self.bob, office='Depot', department='Works', section='Roads')
        mk(None, requester_name='Visitor')
        mk(self.amy, days_ago=45, office='Town Hall', department='Finance', section='Revenue')    # outside 30 days
        self.client.force_login(self.staff)

    def click(self, row, params):
        """Follow a report row's link; return the ticket list response."""
        return self.client.get(row['drill'])

    def test_every_row_with_requests_links_to_exactly_that_many_tickets(self):
        for by in ('office', 'department', 'section', 'user'):
            for period in ('30', '7', 'all'):
                r = self.client.get(reverse('analytics_demand'), {'by': by, 'period': period})
                page = r.context['rows']
                for row in page:
                    if not row['count']:
                        self.assertEqual(row['drill'], '', (by, row['label']))
                        continue
                    lst = self.client.get(row['drill'])
                    self.assertEqual(lst.context['total_count'], row['count'], (by, period, row['label']))

    def test_links_carry_the_right_filter_and_window(self):
        r = self.client.get(reverse('analytics_demand'), {'by': 'office'})
        rows = {x['label']: x for x in r.context['rows']}
        q = parse_qs(urlparse(rows['Town Hall']['drill']).query)
        self.assertEqual(q['office'], ['Town Hall'])
        self.assertIn('since', q); self.assertIn('until', q)
        self.assertEqual(parse_qs(urlparse(rows['(not set)']['drill']).query)['office'], ['__none__'])
        allq = parse_qs(urlparse({x['label']: x for x in self.client.get(reverse('analytics_demand'), {'by': 'office', 'period': 'all'})
                                  .context['rows']}['Town Hall']['drill']).query)
        self.assertNotIn('since', allq)                                              # "all time" has no bounds

    def test_user_rows_link_by_requester_id(self):
        rows = {x['label']: x for x in self.client.get(reverse('analytics_demand'), {'by': 'user'}).context['rows']}
        self.assertEqual(parse_qs(urlparse(rows['Amy']['drill']).query)['requester'], [str(self.amy.pk)])

    def test_the_report_page_renders_the_links(self):
        r = self.client.get(reverse('analytics_demand'), {'by': 'office'})
        self.assertContains(r, 'title="Show these tickets"')
        self.assertContains(r, reverse('ticket_list'))

    def test_clicking_through_shows_the_filter_chips(self):
        rows = {x['label']: x for x in self.client.get(reverse('analytics_demand'), {'by': 'department'}).context['rows']}
        lst = self.client.get(rows['Finance']['drill'])
        self.assertEqual([c['label'] for c in lst.context['chips']][0], 'Department: Finance')
        self.assertEqual(len(lst.context['chips']), 2)                               # + the created-since window ("until now" is not shown)
        self.assertEqual(lst.context['total_count'], 4)
