import base64
import io

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse, NoReverseMatch
from PIL import Image, ImageDraw

import json

from .models import (Asset, Category, PendingNotification, Ticket, TicketApproval,
                     TicketComment)


def png_data_url(draw_stroke=True):
    img = Image.new('RGBA', (600, 160), (0, 0, 0, 0))
    if draw_stroke:
        ImageDraw.Draw(img).line([(20, 80), (200, 30), (400, 120), (560, 60)], fill=(11, 31, 58, 255), width=3)
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def make_tickets(n, requester):
    cat = Category.objects.first() or Category.objects.create(name='General')
    for i in range(n):
        Ticket.objects.create(title=f'Issue {i:02d}', description='desc', category=cat,
                              requester=requester)


class PaginationTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('staff', password='pw', is_staff=True)
        make_tickets(25, self.staff)
        self.client.force_login(self.staff)

    def test_dashboard_shows_10_per_page(self):
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(len(r.context['recent_tickets']), 10)
        self.assertEqual(r.context['recent_tickets'].paginator.num_pages, 3)
        self.assertContains(r, 'Showing 1–10 of 25 tickets')

    def test_dashboard_last_page_and_param(self):
        r = self.client.get(reverse('dashboard') + '?tpage=3')
        self.assertEqual(len(r.context['recent_tickets']), 5)

    def test_dashboard_bad_page_is_safe(self):
        self.assertEqual(self.client.get(reverse('dashboard') + '?tpage=abc').status_code, 200)
        self.assertEqual(self.client.get(reverse('dashboard') + '?tpage=999').status_code, 200)

    def test_ticket_list_10_per_page_and_keeps_filters(self):
        r = self.client.get(reverse('ticket_list') + '?status=OPEN&priority=MEDIUM&search=Issue')
        self.assertEqual(len(r.context['tickets']), 10)
        self.assertContains(r, 'page=2')
        self.assertContains(r, 'search=Issue')       # filters survive paging
        self.assertContains(r, 'priority=MEDIUM')

    def test_portal_10_per_page(self):
        user = User.objects.create_user('req', password='pw')
        make_tickets(12, user)
        self.client.force_login(user)
        r = self.client.get(reverse('portal'))
        self.assertEqual(len(r.context['my_tickets']), 10)
        r = self.client.get(reverse('portal') + '?page=2')
        self.assertEqual(len(r.context['my_tickets']), 2)


class PrintRemovedTests(TestCase):
    def test_print_routes_gone(self):
        for name in ('print_tickets', 'print_ticket_single'):
            with self.assertRaises(NoReverseMatch):
                reverse(name, args=['X']) if name.endswith('single') else reverse(name)

    def test_no_print_buttons(self):
        admin = User.objects.create_superuser('root', 'r@x.com', 'pw')
        make_tickets(1, admin)
        self.client.force_login(admin)
        t = Ticket.objects.first()
        self.assertNotContains(self.client.get(reverse('ticket_list')), 'Print Tickets')
        d = self.client.get(reverse('ticket_detail', args=[t.ticket_id]))
        self.assertNotContains(d, 'fa-print')
        self.assertContains(d, 'Approval &amp; E-Signature')


class ESignTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('agent', password='secret123', is_staff=True,
                                              first_name='Ada', last_name='Moyo')
        make_tickets(1, self.staff)
        self.ticket = Ticket.objects.first()
        self.url = reverse('ticket_detail', args=[self.ticket.ticket_id])
        self.client.force_login(self.staff)

    def post(self, **over):
        data = dict(action='esign', decision='APPROVED', reason='', signer_title='IT Manager',
                    password='secret123', signature=png_data_url())
        data.update(over)
        return self.client.post(self.url, data)

    def test_approve_creates_signed_record(self):
        r = self.post()
        self.assertRedirects(r, self.url)
        a = TicketApproval.objects.get()
        self.assertEqual((a.decision, a.signer, a.signer_name, a.signer_title),
                         ('APPROVED', self.staff, 'Ada Moyo', 'IT Manager'))
        self.assertTrue(a.is_intact)
        self.assertTrue(TicketComment.objects.filter(ticket=self.ticket, body__contains='Approved').exists())
        self.assertContains(self.client.get(self.url), 'Record verified')

    def test_reject_needs_reason_and_keeps_input(self):
        sig = png_data_url()
        r = self.post(decision='REJECTED', reason='', signature=sig)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(TicketApproval.objects.count(), 0)
        self.assertIn('reason', r.context['esign_errors'])
        self.assertEqual(list(r.context['esign_errors']), ['reason'])      # only that field flagged
        self.assertEqual(r.context['esign_values']['signature'], sig)      # signature kept
        self.assertEqual(r.context['esign_values']['signer_title'], 'IT Manager')
        self.assertContains(r, 'Nothing was lost')

    def test_reject_with_reason(self):
        self.post(decision='REJECTED', reason='Wrong asset tag on request')
        a = TicketApproval.objects.get()
        self.assertEqual((a.decision, a.reason), ('REJECTED', 'Wrong asset tag on request'))

    def test_wrong_password(self):
        r = self.post(password='Wr0ng-Pw-Zq7')
        self.assertEqual(list(r.context['esign_errors']), ['password'])
        self.assertEqual(TicketApproval.objects.count(), 0)
        self.assertNotContains(r, 'Wr0ng-Pw-Zq7')                          # password never echoed

    def test_blank_or_missing_signature(self):
        for sig in ('', png_data_url(draw_stroke=False), 'data:image/png;base64,@@@',
                    'data:text/html;base64,PHNjcmlwdD4=', 'javascript:alert(1)'):
            r = self.post(signature=sig)
            self.assertIn('signature', r.context['esign_errors'], sig[:30])
        self.assertEqual(TicketApproval.objects.count(), 0)

    def test_non_png_payload_rejected(self):
        fake = 'data:image/png;base64,' + base64.b64encode(b'<script>alert(1)</script>').decode()
        self.assertIn('signature', self.post(signature=fake).context['esign_errors'])

    def test_requester_cannot_sign(self):
        user = User.objects.create_user('plain', password='pw')
        self.ticket.requester = user; self.ticket.save()
        self.client.force_login(user)
        self.post(password='pw')
        self.assertEqual(TicketApproval.objects.count(), 0)

    def test_records_are_immutable_and_tamper_evident(self):
        self.post()
        a = TicketApproval.objects.get()
        a.reason = 'changed'
        with self.assertRaises(ValueError):
            a.save()
        TicketApproval.objects.filter(pk=a.pk).update(decision='REJECTED')   # bypass save()
        a.refresh_from_db()
        self.assertFalse(a.is_intact)
        self.assertContains(self.client.get(self.url), 'Record has been altered')

    def test_later_decision_supersedes_but_history_kept(self):
        self.post(decision='REJECTED', reason='Missing details here')
        self.post()
        self.assertEqual(TicketApproval.objects.count(), 2)
        r = self.client.get(self.url)
        self.assertEqual(r.context['latest_approval'].decision, 'APPROVED')
        self.assertContains(r, 'Sign-off history')


class AssetFormTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('agent', password='pw', is_staff=True)
        self.client.force_login(self.staff)
        Asset.objects.create(asset_tag='BM-PC-001', name='Dell OptiPlex', serial_number='SN-ABC123')

    def payload(self, **over):
        d = dict(asset_tag='BM-PC-002', name='HP ProDesk', asset_type='DESKTOP', status='ACTIVE',
                 serial_number='SN-NEW999', manufacturer='HP', model='ProDesk', department='Revenue',
                 section='Accounts', location='Room 3', assigned_to_name='T. Ncube',
                 purchase_date='2026-01-10', purchase_cost='850.50', warranty_expiry='2029-01-10',
                 notes='Handle with care')
        d.update(over)
        return d

    def test_duplicate_serial_rejected_and_everything_else_kept(self):
        r = self.client.post(reverse('asset_create'), self.payload(serial_number='SN-ABC123'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Asset.objects.count(), 1)                         # nothing saved
        self.assertEqual(list(r.context['errors']), ['serial_number'])     # ONLY serial flagged
        self.assertIn('BM-PC-001', r.context['errors']['serial_number'])   # tells you which asset
        v = r.context['v']
        self.assertEqual((v['name'], v['department'], v['location'], v['notes'], v['purchase_cost']),
                         ('HP ProDesk', 'Revenue', 'Room 3', 'Handle with care', '850.50'))
        self.assertContains(r, 'value="HP ProDesk"')                       # really rendered back
        self.assertContains(r, 'Handle with care')

    def test_duplicate_serial_is_case_and_space_insensitive(self):
        r = self.client.post(reverse('asset_create'), self.payload(serial_number='  sn-abc123 '))
        self.assertIn('serial_number', r.context['errors'])

    def test_fix_only_the_serial_then_save(self):
        self.client.post(reverse('asset_create'), self.payload(serial_number='SN-ABC123'))
        r = self.client.post(reverse('asset_create'), self.payload(serial_number='SN-FIXED1'))
        self.assertEqual(r.status_code, 302)
        a = Asset.objects.get(asset_tag='BM-PC-002')
        self.assertEqual(str(a.purchase_cost), '850.50')                   # cost now actually saved

    def test_placeholder_serials_may_repeat(self):
        for i in range(2):
            r = self.client.post(reverse('asset_create'),
                                 self.payload(asset_tag=f'BM-X-{i}', serial_number='N/A'))
            self.assertEqual(r.status_code, 302)

    def test_duplicate_tag_and_other_errors_reported_together(self):
        r = self.client.post(reverse('asset_create'),
                             self.payload(asset_tag='bm-pc-001', name='', purchase_cost='abc',
                                          warranty_expiry='2020-01-01'))
        self.assertEqual(set(r.context['errors']), {'asset_tag', 'name', 'purchase_cost', 'warranty_expiry'})

    def test_edit_can_keep_own_serial_but_not_take_anothers(self):
        other = Asset.objects.create(asset_tag='BM-PC-009', name='Other', serial_number='SN-OTHER')
        url = reverse('asset_edit', args=[other.pk])
        ok = self.client.post(url, self.payload(serial_number='SN-OTHER', name='Other renamed'))
        self.assertEqual(ok.status_code, 302)
        bad = self.client.post(url, self.payload(serial_number='SN-ABC123'))
        self.assertEqual(list(bad.context['errors']), ['serial_number'])
        self.assertEqual(bad.context['v']['name'], 'HP ProDesk')           # edits kept
        other.refresh_from_db(); self.assertEqual(other.serial_number, 'SN-OTHER')

    def test_live_serial_check_endpoint(self):
        url = reverse('api_check_serial')
        self.assertTrue(self.client.get(url, {'serial': 'sn-abc123'}).json()['duplicate'])
        self.assertFalse(self.client.get(url, {'serial': 'SN-UNUSED'}).json()['duplicate'])

    def test_blank_form_defaults(self):
        r = self.client.get(reverse('asset_create'))
        self.assertEqual((r.context['v']['asset_type'], r.context['v']['status']), ('DESKTOP', 'ACTIVE'))


class PortalFormTests(TestCase):
    def test_missing_title_keeps_other_fields(self):
        user = User.objects.create_user('req', password='pw')
        self.client.force_login(user)
        r = self.client.post(reverse('portal_create'), dict(
            title='', description='Screen flickers in Room 4', priority='HIGH',
            requester_section='Revenue Dept'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Ticket.objects.count(), 0)
        self.assertEqual(list(r.context['errors']), ['title'])
        self.assertContains(r, 'Screen flickers in Room 4')
        self.assertContains(r, 'Revenue Dept')
        self.assertContains(r, "setPri('HIGH')")


# ═══════════════════════════════════════════════════════════════════════════════
# One creation path: the portal form
# ═══════════════════════════════════════════════════════════════════════════════

class SingleCreationPathTests(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(name='Hardware')
        self.staff = User.objects.create_user('agent', password='pw', is_staff=True,
                                              first_name='Ada', last_name='Moyo')
        self.other_staff = User.objects.create_user('agent2', password='pw', is_staff=True)
        self.req = User.objects.create_user('req', password='pw', first_name='Tendai', last_name='Ncube')
        self.url = reverse('portal_create')

    def test_old_staff_create_url_redirects_and_keeps_voip_params(self):
        self.client.force_login(self.staff)
        r = self.client.get(reverse('ticket_create') + '?caller=%2B263771&source=VOIP&voip_call=5')
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r['Location'].startswith(self.url + '?'))
        self.assertIn('caller=%2B263771', r['Location'])
        self.assertIn('voip_call=5', r['Location'])

    def test_staff_ui_links_point_at_the_portal_form(self):
        self.client.force_login(self.staff)
        html = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn(f'href="{self.url}"', html)
        self.assertNotIn('/dashboard/tickets/create/', html)

    def test_requester_creates_ticket(self):
        self.client.force_login(self.req)
        r = self.client.post(self.url, dict(title='Screen flickers', description='Room 4',
                                            category=self.cat.pk, priority='HIGH'))
        self.assertRedirects(r, reverse('portal'))
        t = Ticket.objects.get()
        self.assertRegex(t.ticket_id, r'^ND\d{6}$')          # one numbering scheme now
        self.assertEqual((t.source, t.status, t.requester, t.priority),
                         ('PORTAL', 'OPEN', self.req, 'HIGH'))
        self.assertEqual(t.requester_name, 'Tendai Ncube')

    def test_requester_cannot_use_staff_only_options(self):
        self.client.force_login(self.req)
        r = self.client.post(self.url, dict(title='x y', priority='CRITICAL', source='PHONE',
                                            requester_name='Someone else', assigned_to=self.staff.pk))
        self.assertEqual(list(r.context['errors']), ['priority'])
        self.assertNotIn('staff_mode', r.context)
        self.client.post(self.url, dict(title='x y', priority='HIGH', source='PHONE',
                                        requester_name='Someone else', assigned_to=self.staff.pk))
        t = Ticket.objects.get()
        self.assertEqual((t.source, t.requester_name, t.assigned_to), ('PORTAL', 'Tendai Ncube', None))

    def test_staff_sees_extra_section_requester_does_not(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(self.url), 'Who is this for?')
        self.client.force_login(self.req)
        self.assertNotContains(self.client.get(self.url), 'Who is this for?')

    def test_staff_log_phone_call_for_non_user(self):
        self.client.force_login(self.staff)
        r = self.client.post(self.url, dict(title='Printer jam', description='Paper stuck',
                                            priority='CRITICAL', source='PHONE',
                                            requester_name='Mrs Dube', requester_phone='+263 77 123 4567'))
        t = Ticket.objects.get()
        self.assertRedirects(r, reverse('ticket_detail', args=[t.ticket_id]))
        # The caller is not a system user: no requester (so reports aren't credited to the
        # staff member typing), and the staff member is recorded as who logged it.
        self.assertEqual((t.source, t.priority, t.requester, t.logged_by, t.requester_name, t.requester_phone),
                         ('PHONE', 'CRITICAL', None, self.staff, 'Mrs Dube', '+263 77 123 4567'))

    def test_staff_must_say_who_it_is_for_and_keep_everything_else(self):
        self.client.force_login(self.staff)
        r = self.client.post(self.url, dict(title='Printer jam', description='Paper stuck',
                                            priority='HIGH', source='WALK_IN',
                                            requester_phone='0771234567', requester_email='bad'))
        self.assertEqual(set(r.context['errors']), {'requester_name', 'requester_email'})
        self.assertEqual(Ticket.objects.count(), 0)
        self.assertContains(r, 'Paper stuck')
        self.assertContains(r, '0771234567')
        self.assertContains(r, "setPri('HIGH')")

    def test_on_behalf_of_existing_user(self):
        self.client.force_login(self.staff)
        self.client.post(self.url, dict(title='VPN down', priority='MEDIUM', source='WALK_IN',
                                        on_behalf_of=self.req.pk))
        t = Ticket.objects.get()
        self.assertEqual((t.requester, t.requester_name), (self.req, 'Tendai Ncube'))

    def test_voip_link_prefills_and_links_the_call(self):
        from voip.models import VoIPCall
        call = VoIPCall.objects.create(caller_number='+263771000111', caller_name='Mr Sibanda',
                                       destination_number='100')
        self.client.force_login(self.staff)
        g = self.client.get(self.url, dict(caller='+263771000111', caller_name='Mr Sibanda',
                                           source='VOIP', voip_call=call.pk))
        v = g.context['v']
        self.assertEqual((v['requester_phone'], v['requester_name'], v['source'], v['voip_call']),
                         ('+263771000111', 'Mr Sibanda', 'VOIP', str(call.pk)))
        self.assertContains(g, 'Logging a ticket from a VoIP call')
        self.client.post(self.url, dict(title='No dial tone', priority='HIGH', source='VOIP',
                                        requester_name='Mr Sibanda', requester_phone='+263771000111',
                                        voip_call=call.pk))
        t = Ticket.objects.get()
        call.refresh_from_db()
        self.assertEqual((t.voip_call, t.source, call.ticket_created), (call, 'VOIP', True))

    def test_unknown_voip_call_rejected(self):
        self.client.force_login(self.staff)
        r = self.client.post(self.url, dict(title='x y', priority='LOW', source='VOIP',
                                            requester_name='A', voip_call='99999'))
        self.assertIn('voip_call', r.context['errors'])

    def test_only_admins_can_assign_on_create(self):
        self.client.force_login(self.staff)
        self.client.post(self.url, dict(title='x y', priority='LOW', source='PHONE',
                                        requester_name='A', assigned_to=self.other_staff.pk))
        self.assertIsNone(Ticket.objects.get().assigned_to)
        Ticket.objects.all().delete()
        admin = User.objects.create_superuser('root', 'r@x.com', 'pw')
        self.client.force_login(admin)
        self.client.post(self.url, dict(title='x y', priority='LOW', source='PHONE',
                                        requester_name='A', assigned_to=self.other_staff.pk))
        self.assertEqual(Ticket.objects.get().assigned_to, self.other_staff)

    def test_voip_log_call_redirects_to_portal_form(self):
        self.client.force_login(self.staff)
        r = self.client.post(reverse('log_call'), dict(
            caller_number='+263771999', caller_name='Mr X', destination_number='100',
            create_ticket='on'))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r['Location'].startswith(self.url), r['Location'])
        self.assertIn('voip_call=', r['Location'])
        self.assertIn('source=VOIP', r['Location'])


# ═══════════════════════════════════════════════════════════════════════════════
# WhatsApp-style notifications
# ═══════════════════════════════════════════════════════════════════════════════

def inbox(user):
    return list(PendingNotification.objects.filter(user=user).order_by('id'))


class NotificationTests(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(name='Hardware')
        self.staff1 = User.objects.create_user('agent1', password='pw', is_staff=True, first_name='Ada', last_name='Moyo')
        self.staff2 = User.objects.create_user('agent2', password='pw', is_staff=True)
        self.req = User.objects.create_user('req', password='pw', first_name='Tendai', last_name='Ncube')

    def new_ticket_via_portal(self, **over):
        self.client.force_login(self.req)
        data = dict(title='Printer jam', description='x', priority='HIGH', category=self.cat.pk)
        data.update(over)
        self.client.post(reverse('portal_create'), data)
        return Ticket.objects.latest('id')

    def test_new_ticket_notifies_all_staff_chat_style(self):
        t = self.new_ticket_via_portal()
        for staff in (self.staff1, self.staff2):
            (n,) = inbox(staff)
            p = json.loads(n.payload)
            self.assertEqual(n.conversation, t.ticket_id)
            self.assertEqual((p['sender'], p['ticket_id'], p['ticket_title'], p['kind']),
                             ('Tendai Ncube', t.ticket_id, 'Printer jam', 'created'))
            self.assertIn('Printer jam', p['body'])
            self.assertEqual(p['url'], f'/dashboard/tickets/{t.ticket_id}/')
            self.assertEqual(p['level'], 'warning')                     # HIGH
            self.assertIsNone(n.read_at)
            self.assertFalse(n.delivered)
        self.assertEqual(inbox(self.req), [])                           # creator isn't told about their own

    def test_critical_is_danger_level(self):
        self.client.force_login(self.staff1)
        self.client.post(reverse('portal_create'), dict(title='Server down', priority='CRITICAL',
                                                        source='PHONE', requester_name='Boss'))
        p = json.loads(inbox(self.staff2)[0].payload)
        self.assertEqual(p['level'], 'danger')
        self.assertEqual(inbox(self.staff1), [])                        # not the creator

    def test_logged_on_behalf_tells_that_person(self):
        self.client.force_login(self.staff1)
        self.client.post(reverse('portal_create'), dict(title='VPN down', priority='LOW',
                                                        source='WALK_IN', on_behalf_of=self.req.pk))
        (n,) = inbox(self.req)
        p = json.loads(n.payload)
        self.assertEqual((p['sender'], p['kind']), ('Ada Moyo', 'created'))
        self.assertTrue(p['url'].startswith('/dashboard/portal/ticket/'))   # requester-facing link

    def test_assignee_gets_assigned_message_not_a_duplicate(self):
        admin = User.objects.create_superuser('root', 'r@x.com', 'pw')
        self.client.force_login(admin)
        self.client.post(reverse('portal_create'), dict(title='x y', priority='LOW', source='PHONE',
                                                        requester_name='A', assigned_to=self.staff1.pk))
        kinds = [json.loads(n.payload)['kind'] for n in inbox(self.staff1)]
        self.assertEqual(kinds, ['assigned'])
        self.assertEqual([json.loads(n.payload)['kind'] for n in inbox(self.staff2)], ['created'])

    def comment(self, ticket, author, body, internal=False):
        from helpdesk.notifications import notify_new_comment
        c = TicketComment.objects.create(ticket=ticket, author=author, body=body, is_internal=internal)
        notify_new_comment(ticket, c)
        return c

    def test_requester_comment_on_unassigned_ticket_reaches_all_staff(self):
        t = self.new_ticket_via_portal()
        PendingNotification.objects.all().delete()
        self.comment(t, self.req, 'It is getting worse')
        for staff in (self.staff1, self.staff2):
            p = json.loads(inbox(staff)[0].payload)
            self.assertEqual((p['sender'], p['body'], p['kind']), ('Tendai Ncube', 'It is getting worse', 'comment'))

    def test_staff_reply_reaches_requester_but_internal_note_does_not(self):
        t = self.new_ticket_via_portal()
        t.assigned_to = self.staff2; t.save()
        PendingNotification.objects.all().delete()
        self.comment(t, self.staff1, 'Secret: replace the fuser', internal=True)
        self.assertEqual(inbox(self.req), [])                               # no leak
        self.assertEqual(len(inbox(self.staff2)), 1)                        # assignee still sees it
        self.comment(t, self.staff1, 'Technician is on the way')
        (n,) = inbox(self.req)
        p = json.loads(n.payload)
        self.assertEqual((p['kind'], p['sender'], p['body']), ('reply', 'Ada Moyo', 'Technician is on the way'))

    def test_author_is_never_notified_of_their_own_comment(self):
        t = self.new_ticket_via_portal()
        t.assigned_to = self.staff1; t.save()
        PendingNotification.objects.all().delete()
        self.comment(t, self.staff1, 'note to self')
        self.assertEqual(inbox(self.staff1), [])

    def test_status_change_respects_preference_and_names_the_agent(self):
        from helpdesk.notifications import notify_ticket_status
        t = self.new_ticket_via_portal()
        PendingNotification.objects.all().delete()
        t.status = 'RESOLVED'; t.save()
        notify_ticket_status(t, 'OPEN', self.staff1)
        p = json.loads(inbox(self.req)[0].payload)
        self.assertEqual((p['sender'], p['kind'], p['level']), ('Ada Moyo', 'status', 'success'))
        self.assertIn('Resolved', p['body'])
        PendingNotification.objects.all().delete()
        prof = self.req.profile; prof.notify_status_change = False; prof.save()
        t = Ticket.objects.get(pk=t.pk)          # fresh load, as a real request would have
        notify_ticket_status(t, 'OPEN', self.staff1)
        self.assertEqual(inbox(self.req), [])                               # the preference now works

    def test_sla_breach_accepts_the_level_check_sla_passes(self):
        from helpdesk.notifications import notify_sla_breach
        t = self.new_ticket_via_portal(); t.assigned_to = self.staff1; t.save()
        PendingNotification.objects.all().delete()
        notify_sla_breach(t, 2)                                             # used to raise TypeError
        p = json.loads(inbox(self.staff1)[0].payload)
        self.assertEqual((p['sender'], p['kind'], p['level']), ('SLA Monitor', 'sla', 'danger'))

    # ── read vs delivered, conversations ──────────────────────────────────────

    def api(self, user):
        self.client.force_login(user)
        return self.client.get(reverse('api_notifications')).json()

    def test_delivered_does_not_mean_read(self):
        t = self.new_ticket_via_portal()
        PendingNotification.objects.filter(user=self.staff1).update(delivered=True)  # browser got it
        d = self.api(self.staff1)
        self.assertEqual(d['unread'], 1)                                    # still unread until seen
        self.assertFalse(d['notifications'][0]['read'])

    def test_messages_group_into_one_conversation_per_ticket(self):
        t1 = self.new_ticket_via_portal(title='Printer jam')
        t2 = self.new_ticket_via_portal(title='No internet')
        self.comment(t1, self.req, 'second message')
        self.comment(t1, self.req, 'third message')
        d = self.api(self.staff1)
        self.assertEqual(len(d['notifications']), 4)
        c1, c2 = d['conversations']                                         # newest activity first
        self.assertEqual((c1['ticket_id'], c1['unread'], c1['count'], c1['preview']),
                         (t1.ticket_id, 3, 3, 'third message'))
        self.assertEqual((c2['ticket_id'], c2['unread']), (t2.ticket_id, 1))
        self.assertEqual(d['unread'], 4)

    def test_mark_conversation_read_only_touches_that_chat_and_that_user(self):
        t1 = self.new_ticket_via_portal(title='A one')
        t2 = self.new_ticket_via_portal(title='B two')
        self.client.force_login(self.staff1)
        r = self.client.post(reverse('api_notifications_read'),
                             json.dumps({'conversation': t1.ticket_id}), content_type='application/json')
        self.assertEqual((r.json()['updated'], r.json()['unread']), (1, 1))
        read = PendingNotification.objects.get(user=self.staff1, conversation=t1.ticket_id)
        self.assertIsNotNone(read.read_at)
        self.assertTrue(PendingNotification.objects.filter(user=self.staff2, read_at__isnull=True).count() == 2)

    def test_mark_all_read_and_bad_request(self):
        self.new_ticket_via_portal()
        self.client.force_login(self.staff1)
        self.assertEqual(self.client.post(reverse('api_notifications_read'), '{}',
                                          content_type='application/json').status_code, 400)
        r = self.client.post(reverse('api_notifications_read'), '{"all": true}',
                             content_type='application/json')
        self.assertEqual(r.json()['unread'], 0)

    def test_opening_the_ticket_clears_its_unread_count(self):
        t = self.new_ticket_via_portal()
        self.assertEqual(self.api(self.staff1)['unread'], 1)
        self.client.get(reverse('ticket_detail', args=[t.ticket_id]))
        self.assertEqual(self.api(self.staff1)['unread'], 0)
        self.assertEqual(self.api(self.staff2)['unread'], 1)                # others unaffected

    def test_requester_opening_portal_ticket_clears_reply(self):
        t = self.new_ticket_via_portal()
        self.comment(t, self.staff1, 'On it')
        self.assertEqual(self.api(self.req)['unread'], 1)
        self.client.get(reverse('portal_ticket', args=[t.ticket_id]))
        self.assertEqual(self.api(self.req)['unread'], 0)

    def test_unauthenticated_gets_empty(self):
        self.assertEqual(self.client.get(reverse('api_notifications')).json()['unread'], 0)

    def test_build_conversations_pure(self):
        from helpdesk.notifications import build_conversations
        items = [
            dict(id=3, ticket_id='B', body='b2', read=False, created_at='3'),
            dict(id=2, ticket_id='A', body='a1', read=True,  created_at='2'),
            dict(id=1, ticket_id='B', body='b1', read=False, created_at='1'),
            dict(id=0, tag='sla-X', body='legacy', read=True, created_at='0'),   # no ticket_id
        ]
        out = build_conversations(items)
        self.assertEqual([c['key'] for c in out], ['B', 'A', 'sla-X'])
        self.assertEqual((out[0]['unread'], out[0]['count'], out[0]['preview']), (2, 2, 'b2'))
        self.assertEqual(len(build_conversations(items, limit=2)), 2)

    def test_migration_backfill_treats_delivered_as_read_and_recovers_conversation(self):
        import importlib
        from django.apps import apps
        mig = importlib.import_module('helpdesk.migrations.0011_notification_read_conversation')
        old_seen = PendingNotification.objects.create(user=self.staff1, delivered=True,
                       payload=json.dumps({'tag': 'assigned-ND000042'}))
        old_new = PendingNotification.objects.create(user=self.staff1, delivered=False,
                       payload=json.dumps({'tag': 'comment-ND000043'}))
        junk = PendingNotification.objects.create(user=self.staff1, payload='not json')
        PendingNotification.objects.filter(pk__in=[old_seen.pk, old_new.pk, junk.pk]).update(conversation='')

        class Schema:                       # minimal stand-in for the schema editor
            class connection: alias = 'default'
        mig.backfill(apps, Schema)
        for n in (old_seen, old_new, junk): n.refresh_from_db()
        self.assertIsNotNone(old_seen.read_at)                              # delivered ⇒ was "read"
        self.assertIsNone(old_new.read_at)                                  # undelivered stays unread
        self.assertEqual((old_seen.conversation, old_new.conversation, junk.conversation),
                         ('ND000042', 'ND000043', ''))


# ═══════════════════════════════════════════════════════════════════════════════
# WebSocket delivery
# ═══════════════════════════════════════════════════════════════════════════════

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase


class WebSocketTests(TransactionTestCase):
    def _app(self):
        from helpdesk.consumers import NotificationConsumer
        return NotificationConsumer.as_asgi()

    async def _connect(self, user):
        comm = WebsocketCommunicator(self._app(), '/ws/notifications/')
        comm.scope['user'] = user
        ok, _ = await comm.connect()
        self.assertTrue(ok)
        return comm

    async def test_live_message_carries_chat_fields(self):
        from asgiref.sync import sync_to_async
        from helpdesk.notifications import notify
        user = await database_sync_to_async(User.objects.create_user)('u1', password='pw', is_staff=True)
        ticket = await database_sync_to_async(lambda: Ticket.objects.create(
            title='Printer jam', description='d', requester=user,
            category=Category.objects.create(name='HW')))()
        comm = await self._connect(user)
        await sync_to_async(notify)(user, 'T', 'hello there', '/dashboard/tickets/x/', 'comment-x',
                                    sender='Ada Moyo', ticket=ticket, kind='comment')
        msg = json.loads(await comm.receive_from())
        self.assertEqual((msg['type'], msg['sender'], msg['ticket_id'], msg['ticket_title'],
                          msg['kind'], msg['body']),
                         ('notification', 'Ada Moyo', ticket.ticket_id, 'Printer jam', 'comment', 'hello there'))
        self.assertTrue(msg['created_at'])
        await comm.disconnect()

    async def test_offline_backlog_is_flagged_queued_and_sent_only_once(self):
        from asgiref.sync import sync_to_async
        from helpdesk.notifications import notify
        user = await database_sync_to_async(User.objects.create_user)('u2', password='pw', is_staff=True)
        await sync_to_async(notify)(user, 'T', 'sent while offline', sender='Bob', kind='comment')

        comm = await self._connect(user)
        first = json.loads(await comm.receive_from())
        self.assertTrue(first['queued'])                       # client: no toast / sound for backlog
        self.assertEqual(first['body'], 'sent while offline')
        await comm.disconnect()

        row = await database_sync_to_async(lambda: PendingNotification.objects.get(user=user))()
        self.assertTrue(row.delivered)                         # now actually recorded (was silently failing)
        self.assertIsNone(row.read_at)                         # ...but still unread

        comm2 = await self._connect(user)                      # reconnect: must NOT be re-sent
        self.assertTrue(await comm2.receive_nothing(timeout=0.5))
        await comm2.disconnect()
