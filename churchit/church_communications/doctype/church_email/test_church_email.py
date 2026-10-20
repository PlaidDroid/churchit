# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from churchit.church_communications.doctype.church_email.church_email import deliver, send_scheduled
from churchit.church_communications.doctype.communication_consent.communication_consent import (
	EMAIL,
	GIVEN,
	WITHDRAWN,
	record,
)
from churchit.church_communications.doctype.communication_purpose.communication_purpose import (
	CHURCH_NEWS,
	PASTORAL_CARE,
	create_communication_purposes,
)
from churchit.church_communications.newsletter import UNSUBSCRIBE_METHOD, set_subscribed
from churchit.tests.helpers import (
	RollbackEachTest,
	ensure,
	ensure_root_church,
	ensure_user,
	force_single_church,
	make_person,
	make_two_churches,
)

MODULE = "churchit.church_communications.doctype.church_email.church_email"


def make_reader(first_name, **values):
	email = f"_test_church_email_{first_name.lower()}@example.com"
	return make_person(first_name, "_Test Church Email", emails=[{"email_address": email}], **values)


def email_of(person):
	return person.emails[0].email_address


class TestChurchEmail(RollbackEachTest):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		force_single_church()
		cls.church = ensure_root_church()
		for status in ("Active", "Inactive"):
			ensure("Member Status", {"status": status})
		for membership in ("Member", "Visitor"):
			ensure("Membership Type", {"type": membership})
		ensure("Life Event Type", {"type": "Death"})
		create_communication_purposes()
		cls.member = make_reader("Member", membership="Member")
		cls.visitor = make_reader("Visitor", membership="Visitor")
		cls.inactive = make_reader("Inactive", membership="Member", membership_status="Inactive")
		cls.departed = make_reader(
			"Departed", membership="Member", life_events=[{"event_type": "Death", "date": "2024-01-01"}]
		)
		cls.group = frappe.get_doc(
			{
				"doctype": "Group",
				"group_name": "_Test Church Email Group",
				"members": [{"person": p.name} for p in (cls.member, cls.inactive, cls.departed)],
			}
		).insert(ignore_permissions=True)

	def setUp(self):
		super().setUp()
		self.sendmail = self.start(mock.patch("frappe.sendmail"))
		self.start(mock.patch(f"{MODULE}.can_send_email", return_value=True))

	def start(self, patcher):
		self.addCleanup(patcher.stop)
		return patcher.start()

	def make_email(self, **values):
		return frappe.get_doc(
			{"doctype": "Church Email", "subject": "_Test Church Email", "message": "<p>Hello</p>", **values}
		).insert()

	def test_active_people_leave_out_the_inactive_the_departed_and_the_unsubscribed(self):
		unsubscribed = make_reader("Unsubscribed", membership="Member")
		set_subscribed(email_of(unsubscribed), self.church, False)

		recipients = self.make_email().get_recipients()

		for person in (self.member, self.visitor):
			self.assertIn(email_of(person), recipients)
		for person in (self.inactive, self.departed, unsubscribed):
			self.assertNotIn(email_of(person), recipients)

	def test_people_who_withdrew_church_news_are_left_out(self):
		record([self.member.name], EMAIL, WITHDRAWN, CHURCH_NEWS)
		record([self.visitor.name], EMAIL, WITHDRAWN, PASTORAL_CARE)

		recipients = self.make_email().get_recipients()

		self.assertNotIn(email_of(self.member), recipients)
		self.assertIn(email_of(self.visitor), recipients)

	def test_a_church_that_asks_first_emails_only_people_who_agreed(self):
		frappe.db.set_value("Church", self.church, "contact_only_with_consent", 1)
		record([self.visitor.name], EMAIL, GIVEN, CHURCH_NEWS)
		self.assertEqual(self.make_email().get_recipients(), [email_of(self.visitor)])

	def test_active_members_leave_out_visitors(self):
		recipients = self.make_email(send_to="Active Members").get_recipients()
		self.assertIn(email_of(self.member), recipients)
		self.assertNotIn(email_of(self.visitor), recipients)

	def test_a_group_email_goes_to_the_groups_living_members(self):
		email = self.make_email(send_to="Group", group=self.group.name)
		self.assertEqual(email.get_recipients(), sorted([email_of(self.member), email_of(self.inactive)]))

	def test_sending_queues_one_copy_each_with_a_link_to_stop_the_churchs_emails(self):
		email = self.make_email(send_to="Group", group=self.group.name)
		with mock.patch("frappe.enqueue") as enqueue:
			self.assertEqual(email.send(), "Sending")
		self.assertEqual(enqueue.call_args.kwargs["name"], email.name)

		deliver(email.name)
		deliver(email.name)

		self.sendmail.assert_called_once()
		sent = self.sendmail.call_args.kwargs
		self.assertEqual(sent["recipients"], email.get_recipients())
		self.assertEqual(sent["unsubscribe_method"], UNSUBSCRIBE_METHOD)
		self.assertEqual(sent["unsubscribe_params"], {"church": self.church})
		email.reload()
		self.assertEqual((email.status, email.recipients), ("Sent", 2))

	def test_each_queued_copy_carries_a_link_to_stop_the_churchs_emails(self):
		"""Frappe only adds the footer link when it is given a message for it, so this runs the real sendmail."""
		ensure(
			"Email Account",
			{"enable_outgoing": 1, "default_outgoing": 1},
			{
				"email_account_name": "_Test Church Email Outgoing",
				"email_id": "_test_church_email_outgoing@example.com",
				"service": "GMail",
				"enable_outgoing": 1,
				"default_outgoing": 1,
				"no_smtp_authentication": 1,
			},
		)
		email = self.make_email(send_to="Group", group=self.group.name)
		frappe.db.set_value("Church Email", email.name, "status", "Sending")

		with (
			mock.patch("frappe.sendmail", wraps=frappe.email.sendmail),
			mock.patch.object(frappe.db, "rollback"),
		):
			deliver(email.name)

		queue = frappe.get_last_doc("Email Queue", filters={"reference_name": email.name})
		self.assertIn("<!--unsubscribe_url-->", queue.message)
		self.assertEqual(frappe.parse_json(queue.unsubscribe_params), {"church": self.church})
		self.assertEqual(len(queue.recipients), 2)

	def test_a_delivery_lost_with_its_worker_is_picked_up_again(self):
		email = self.make_email()
		frappe.db.set_value(
			"Church Email",
			email.name,
			{"status": "Sending", "modified": add_to_date(now_datetime(), hours=-1)},
			update_modified=False,
		)

		with mock.patch.object(frappe.db, "commit"):
			send_scheduled()

		self.assertEqual(frappe.db.get_value("Church Email", email.name, "status"), "Sent")

	def test_a_send_at_ahead_waits_for_the_scheduled_job(self):
		email = self.make_email(send_after=add_to_date(now_datetime(), hours=1))
		self.assertEqual(email.send(), "Scheduled")

		with mock.patch.object(frappe.db, "commit"):
			send_scheduled()
			self.sendmail.assert_not_called()

			frappe.db.set_value(
				"Church Email", email.name, "send_after", add_to_date(now_datetime(), hours=-1)
			)
			send_scheduled()

		self.sendmail.assert_called_once()
		self.assertEqual(frappe.db.get_value("Church Email", email.name, "status"), "Sent")

	def test_an_unscheduled_email_is_not_sent(self):
		email = self.make_email(send_after=add_to_date(now_datetime(), hours=1))
		email.send()
		email.unschedule()
		frappe.db.set_value("Church Email", email.name, "send_after", add_to_date(now_datetime(), hours=-1))

		with mock.patch.object(frappe.db, "commit"):
			send_scheduled()

		self.sendmail.assert_not_called()
		self.assertEqual(frappe.db.get_value("Church Email", email.name, "status"), "Draft")

	def test_a_failed_delivery_returns_the_email_to_draft_and_says_why(self):
		email = self.make_email()
		frappe.db.set_value("Church Email", email.name, "status", "Sending")
		self.sendmail.side_effect = frappe.OutgoingEmailError("_Test mail server down")

		with mock.patch.object(frappe.db, "commit"), mock.patch.object(frappe.db, "rollback"):
			with self.assertRaises(frappe.OutgoingEmailError):
				deliver(email.name)

		self.assertEqual(frappe.db.get_value("Church Email", email.name, "status"), "Draft")
		comments = frappe.get_all(
			"Comment", filters={"reference_name": email.name, "comment_type": "Comment"}, pluck="content"
		)
		self.assertTrue([comment for comment in comments if "_Test mail server down" in comment])

	def test_one_failed_scheduled_email_does_not_hold_back_the_others(self):
		first, second = self.make_email(), self.make_email()
		for email in (first, second):
			frappe.db.set_value(
				"Church Email",
				email.name,
				{"status": "Scheduled", "send_after": add_to_date(now_datetime(), hours=-1)},
			)
		self.sendmail.side_effect = [frappe.OutgoingEmailError("_Test refused"), mock.DEFAULT]

		with mock.patch.object(frappe.db, "commit"), mock.patch.object(frappe.db, "rollback"):
			send_scheduled()

		statuses = {frappe.db.get_value("Church Email", e.name, "status") for e in (first, second)}
		self.assertEqual(statuses, {"Draft", "Sent"})

	def test_an_email_is_sent_only_once(self):
		email = self.make_email()
		with mock.patch("frappe.enqueue"):
			email.send()
			with self.assertRaisesRegex(frappe.ValidationError, "already been sent"):
				email.send()

	def test_a_sent_email_cannot_be_changed(self):
		email = self.make_email()
		frappe.db.set_value("Church Email", email.name, "status", "Sent")
		email.reload()
		email.subject = "_Test Changed"
		with self.assertRaisesRegex(frappe.ValidationError, "can no longer be changed"):
			email.save()

	def test_staff_who_may_only_read_cannot_send(self):
		email = self.make_email()
		frappe.set_user(ensure_user("_test_church_email_staff@example.com", "Staff", roles=("Church Staff",)))
		for action in (email.send, email.send_test, email.unschedule):
			with self.subTest(action.__name__), self.assertRaises(frappe.PermissionError):
				action()

	def test_the_status_moves_only_by_sending(self):
		email = self.make_email(status="Sent")
		self.assertEqual(email.status, "Draft")

		email.status = "Sending"
		email.save()
		self.assertEqual(frappe.db.get_value("Church Email", email.name, "status"), "Draft")

	def test_a_scheduled_email_keeps_its_send_at(self):
		email = self.make_email(send_after=add_to_date(now_datetime(), hours=1))
		email.send()
		email.reload()
		email.send_after = None
		with self.assertRaisesRegex(frappe.ValidationError, "Unschedule"):
			email.save()

	def test_nothing_is_sent_without_an_outgoing_email_account(self):
		email = self.make_email()
		with mock.patch(f"{MODULE}.can_send_email", return_value=False):
			with self.assertRaisesRegex(frappe.ValidationError, "outgoing Email Account"):
				email.send()

	def test_a_test_email_goes_to_the_reader_alone_at_once(self):
		self.make_email().send_test()
		sent = self.sendmail.call_args.kwargs
		self.assertEqual(sent["recipients"], [frappe.db.get_value("User", "Administrator", "email")])
		self.assertTrue(sent["now"])


class TestChurchEmailPerChurch(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_root, cls.branch_a, cls.branch_b = make_two_churches()
		cls.addClassCleanup(frappe.clear_cache)
		ensure("Member Status", {"status": "Active"})

	def test_an_unsubscribe_from_one_church_leaves_the_others_emails_coming(self):
		reader = make_reader("Both", church=self.branch_b)
		set_subscribed(email_of(reader), self.branch_a, False)
		email = frappe.get_doc(
			{"doctype": "Church Email", "subject": "_Test Branch B", "message": "Hi", "church": self.branch_b}
		).insert()
		self.assertIn(email_of(reader), email.get_recipients())

	def test_a_church_email_goes_only_to_its_own_churchs_people(self):
		mine = make_reader("Mine", church=self.branch_a)
		theirs = make_reader("Theirs", church=self.branch_b)
		email = frappe.get_doc(
			{"doctype": "Church Email", "subject": "_Test Branch", "message": "Hi", "church": self.branch_a}
		).insert()

		recipients = email.get_recipients()

		self.assertIn(email_of(mine), recipients)
		self.assertNotIn(email_of(theirs), recipients)
		self.assertEqual(email.sending_church, self.branch_a)
