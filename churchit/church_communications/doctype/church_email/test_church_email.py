# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import json
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from churchit.church_communications.doctype.church_email.church_email import deliver, send_scheduled
from churchit.church_communications.doctype.communication_consent.communication_consent import (
	EMAIL,
	GIVEN,
	SMS,
	WITHDRAWN,
	record,
)
from churchit.church_communications.doctype.communication_purpose.communication_purpose import (
	CHURCH_NEWS,
	PASTORAL_CARE,
	create_communication_purposes,
)
from churchit.church_communications.newsletter import UNSUBSCRIBE_METHOD, set_subscribed
from churchit.church_communications.sending import Delivery, Recipient
from churchit.church_communications.test_sending import log_sms
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
SENDING = "churchit.church_communications.sending"


def make_reader(first_name, **values):
	email = f"_test_church_email_{first_name.lower()}@example.com"
	return make_person(first_name, "_Test Church Email", emails=[{"email_address": email}], **values)


def email_of(person):
	return person.emails[0].email_address


def addresses(email):
	return [recipient.address for recipient in email.get_recipients()]


def deliver_now(name):
	"""Run the delivery job without committing the test's transaction."""
	with mock.patch.object(frappe.db, "commit"):
		deliver(name)


def make_people_list(name, **filters):
	"""A smart list of the people whose fields match *filters*."""
	return frappe.get_doc(
		{
			"doctype": "Report",
			"report_name": name,
			"ref_doctype": "Person",
			"report_type": "Report Builder",
			"is_standard": "No",
			"json": json.dumps({"filters": [["Person", key, "=", value] for key, value in filters.items()]}),
		}
	).insert(ignore_permissions=True)


def copies_of(email):
	"""The Communications a Church Email made, as (person, medium, subject, content)."""
	names = frappe.get_all(
		"Communication Link",
		filters={"parenttype": "Communication", "link_doctype": "Church Email", "link_name": email},
		pluck="parent",
	)
	return frappe.get_all(
		"Communication",
		filters={"name": ("in", names), "reference_doctype": "Person"},
		fields=["reference_name", "communication_medium", "subject", "content", "phone_no", "recipients"],
		order_by="reference_name asc",
	)


class ChurchEmailTestCase(RollbackEachTest):
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


class TestChurchEmail(ChurchEmailTestCase):
	def test_active_people_leave_out_the_inactive_the_departed_and_the_unsubscribed(self):
		unsubscribed = make_reader("Unsubscribed", membership="Member")
		set_subscribed(email_of(unsubscribed), self.church, False)

		recipients = addresses(self.make_email())

		for person in (self.member, self.visitor):
			self.assertIn(email_of(person), recipients)
		for person in (self.inactive, self.departed, unsubscribed):
			self.assertNotIn(email_of(person), recipients)

	def test_only_people_who_allow_the_purpose_get_it(self):
		record([self.member.name], EMAIL, WITHDRAWN, CHURCH_NEWS)
		record([self.visitor.name], EMAIL, WITHDRAWN, PASTORAL_CARE)

		news = addresses(self.make_email())
		care = addresses(self.make_email(purpose=PASTORAL_CARE))

		self.assertNotIn(email_of(self.member), news)
		self.assertIn(email_of(self.visitor), news)
		self.assertIn(email_of(self.member), care)
		self.assertNotIn(email_of(self.visitor), care)

	def test_a_church_that_asks_first_emails_only_people_who_agreed(self):
		frappe.db.set_value("Church", self.church, "contact_only_with_consent", 1)
		record([self.visitor.name], EMAIL, GIVEN, CHURCH_NEWS)
		self.assertEqual(addresses(self.make_email()), [email_of(self.visitor)])

	def test_an_address_whose_mail_came_back_is_left_out(self):
		frappe.db.set_value("Email Address", self.visitor.emails[0].name, "is_bounced", 1)
		self.assertNotIn(email_of(self.visitor), addresses(self.make_email()))

	def test_a_household_sharing_an_address_gets_one_copy(self):
		sharer = make_person(
			"Sharer",
			"_Test Church Email",
			membership="Member",
			emails=[
				{
					"email_address": email_of(self.member),
					"shared_from": self.member.name,
					"shared_from_type": "Person",
				}
			],
		)
		recipients = self.make_email().get_recipients()

		self.assertEqual([r.address for r in recipients].count(email_of(self.member)), 1)
		self.assertNotIn(sharer.name, [r.person for r in recipients])

	def test_active_members_leave_out_visitors(self):
		recipients = addresses(self.make_email(send_to="Active Members"))
		self.assertIn(email_of(self.member), recipients)
		self.assertNotIn(email_of(self.visitor), recipients)

	def test_a_group_email_goes_to_the_groups_living_members(self):
		email = self.make_email(send_to="Group", group=self.group.name)
		self.assertEqual(sorted(addresses(email)), sorted([email_of(self.member), email_of(self.inactive)]))

	def test_a_smart_list_email_goes_to_the_living_people_on_the_list(self):
		smart_list = make_people_list(
			"_Test Church Email Members", last_name="_Test Church Email", membership="Member"
		)
		email = self.make_email(send_to="Smart List", smart_list=smart_list.name)
		self.assertEqual(sorted(addresses(email)), sorted([email_of(self.member), email_of(self.inactive)]))

	def test_a_smart_list_must_be_a_saved_list_of_people(self):
		families = frappe.get_doc(
			{
				"doctype": "Report",
				"report_name": "_Test Church Email Families",
				"ref_doctype": "Family",
				"report_type": "Report Builder",
				"is_standard": "No",
			}
		).insert(ignore_permissions=True)
		with self.assertRaisesRegex(frappe.ValidationError, "smart list"):
			self.make_email(send_to="Smart List", smart_list=families.name)

	def test_chosen_people_get_it_and_nobody_else(self):
		email = self.make_email(
			send_to="Chosen People", people=[{"person": self.visitor.name}, {"person": self.departed.name}]
		)
		self.assertEqual(addresses(email), [email_of(self.visitor)])

	def test_an_audience_that_send_to_does_not_use_is_cleared(self):
		email = self.make_email(group=self.group.name, people=[{"person": self.visitor.name}])
		self.assertEqual((email.group, email.people), (None, []))

	def test_each_person_gets_a_copy_with_their_own_name_kept_on_their_timeline(self):
		email = self.make_email(
			send_to="Group",
			group=self.group.name,
			subject="Hello {{ first_name }}",
			message="<p>Dear {{ doc.display_name }}</p>",
		)
		with mock.patch("frappe.enqueue") as enqueue:
			self.assertEqual(email.send(), "Sending")
		self.assertEqual(enqueue.call_args.kwargs["name"], email.name)

		deliver_now(email.name)
		deliver_now(email.name)

		self.assertEqual(self.sendmail.call_count, 2)
		sent = {call.kwargs["recipients"][0]: call.kwargs for call in self.sendmail.call_args_list}
		member = sent[email_of(self.member)]
		self.assertEqual(member["subject"], "Hello Member")
		self.assertIn("Dear Member _Test Church Email", member["message"])
		self.assertEqual(member["unsubscribe_method"], UNSUBSCRIBE_METHOD)
		self.assertEqual(member["unsubscribe_params"], {"church": self.church, "purpose": CHURCH_NEWS})
		self.assertEqual(member["reference_name"], email.name)

		copies = copies_of(email.name)
		self.assertEqual(
			[copy.reference_name for copy in copies], sorted([self.member.name, self.inactive.name])
		)
		copy = next(copy for copy in copies if copy.reference_name == self.member.name)
		self.assertEqual((copy.communication_medium, copy.subject), ("Email", "Hello Member"))
		self.assertIn("Dear Member _Test Church Email", copy.content)
		self.assertEqual(
			member["communication"], frappe.db.get_value("Communication", {"subject": "Hello Member"})
		)
		email.reload()
		self.assertEqual((email.status, email.recipients), ("Sent", 2))

	def test_a_delivery_that_stopped_part_way_sends_only_to_the_people_still_waiting(self):
		email = self.make_email(send_to="Group", group=self.group.name)
		Delivery(email).record(
			Recipient(self.member.name, email_of(self.member)), frappe._dict(subject="x", message="y")
		)
		frappe.db.set_value("Church Email", email.name, "status", "Sending")

		deliver_now(email.name)

		self.sendmail.assert_called_once()
		self.assertEqual(self.sendmail.call_args.kwargs["recipients"], [email_of(self.inactive)])
		self.assertEqual(frappe.db.get_value("Church Email", email.name, "recipients"), 2)

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
			deliver_now(email.name)

		queues = frappe.get_all(
			"Email Queue",
			filters={"reference_name": email.name},
			fields=["name", "message", "unsubscribe_params", "communication"],
		)
		self.assertEqual(len(queues), 2)
		for queue in queues:
			self.assertIn("<!--unsubscribe_url-->", queue.message)
			self.assertEqual(
				frappe.parse_json(queue.unsubscribe_params), {"church": self.church, "purpose": CHURCH_NEWS}
			)
			self.assertEqual(
				frappe.db.get_value("Communication", queue.communication, "reference_doctype"), "Person"
			)

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

		self.sendmail.assert_called()
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
		first = self.make_email(send_to="Chosen People", people=[{"person": self.member.name}])
		second = self.make_email(send_to="Chosen People", people=[{"person": self.visitor.name}])
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

	def test_a_test_email_goes_to_the_reader_alone_at_once_with_their_name(self):
		self.make_email(subject="For {{ first_name }}").send_test()
		sent = self.sendmail.call_args.kwargs
		self.assertEqual(sent["recipients"], [frappe.db.get_value("User", "Administrator", "email")])
		self.assertEqual(sent["subject"], "For Administrator")
		self.assertTrue(sent["now"])
		self.assertIsNone(sent["communication"])


class TestChurchEmailPlaceholders(ChurchEmailTestCase):
	def test_a_placeholder_outside_the_persons_names_is_refused(self):
		for message in ("{{ frappe.db.sql('select 1') }}", "{{ doc.name }}", "{{ amount }}"):
			with self.subTest(message), self.assertRaisesRegex(frappe.ValidationError, "first_name"):
				self.make_email(message=message)

	def test_a_message_that_does_not_compile_is_refused(self):
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be filled in"):
			self.make_email(subject="Hello {{ first_name")

	def test_a_text_is_checked_and_named_by_its_first_words(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Text"):
			self.make_email(channel="Text Message", text_message="Hi {{ frappe }}")
		text = self.make_email(
			channel="Text Message", subject="{{ ignored", text_message="Hi {{ first_name }}"
		)
		self.assertEqual(text.subject, "Hi {{ first_name }}")

	def test_the_preview_shows_the_first_person_it_goes_to_unless_another_is_picked(self):
		email = self.make_email(
			send_to="Group",
			group=self.group.name,
			subject="Hi {{ first_name }}",
			message="<p>{{ last_name }}</p>",
		)
		first, second = sorted([self.member.name, self.inactive.name])

		preview = email.get_preview()
		self.assertEqual((preview["person"], preview["count"]), (first, 2))

		picked = email.get_preview(self.inactive.name)
		self.assertEqual(picked["to"], email_of(self.inactive))
		self.assertEqual(picked["subject"], "Hi Inactive")
		self.assertEqual(picked["message"], "<p>_Test Church Email</p>")

		with self.assertRaisesRegex(frappe.ValidationError, "does not get this message"):
			email.get_preview(self.visitor.name)

	def test_a_name_in_an_html_message_is_escaped(self):
		person = make_person(
			"<b>Bold</b>", "_Test Church Email", emails=[{"email_address": "_test_bold@example.com"}]
		)
		email = self.make_email(
			send_to="Chosen People",
			people=[{"person": person.name}],
			subject="Hi {{ first_name }}",
			message="<p>{{ first_name }}</p>",
		)
		preview = email.get_preview()
		self.assertEqual(preview["subject"], "Hi <b>Bold</b>")
		self.assertEqual(preview["message"], "<p>&lt;b&gt;Bold&lt;/b&gt;</p>")


class TestChurchTextMessage(ChurchEmailTestCase):
	def setUp(self):
		super().setUp()
		self.send_sms = self.start(mock.patch(f"{SENDING}.send_sms", side_effect=log_sms))
		self.start(mock.patch(f"{MODULE}.can_send_texts", return_value=True))
		self.texter = make_person(
			"Texter", "_Test Church Email", membership="Member", phones=[{"phone_number": "+1 555 0101"}]
		)
		self.silent = make_person(
			"Silent", "_Test Church Email", membership="Member", phones=[{"phone_number": "+1 555 0102"}]
		)
		record([self.texter.name], SMS, GIVEN, CHURCH_NEWS)

	def make_text(self, **values):
		return self.make_email(
			channel="Text Message",
			send_to="Chosen People",
			people=[{"person": self.texter.name}, {"person": self.silent.name}],
			text_message="Hi {{ first_name }}",
			**values,
		)

	def test_a_text_goes_only_to_people_who_agreed_to_texts(self):
		text = self.make_text()
		frappe.db.set_value("Church Email", text.name, "status", "Sending")

		deliver_now(text.name)

		self.send_sms.assert_called_once_with(["+1 555 0101"], "Hi Texter", success_msg=False)
		self.sendmail.assert_not_called()
		[copy] = copies_of(text.name)
		self.assertEqual(
			(copy.reference_name, copy.communication_medium, copy.phone_no),
			(self.texter.name, "SMS", "+1 555 0101"),
		)
		self.assertEqual(copy.content, "Hi Texter")
		self.assertEqual(frappe.db.get_value("Church Email", text.name, "recipients"), 1)

	def test_texts_are_refused_until_sms_is_set_up(self):
		with mock.patch(f"{MODULE}.can_send_texts", return_value=False):
			with self.assertRaisesRegex(frappe.ValidationError, "SMS Settings"):
				self.make_text().send()

	def test_a_user_who_may_not_send_texts_is_refused_on_send(self):
		text = self.make_text()
		frappe.set_user(
			ensure_user("_test_church_email_manager@example.com", "Manager", roles=("Church Manager",))
		)
		with self.assertRaisesRegex(frappe.PermissionError, "SMS"):
			text.send()

	def test_a_test_text_goes_to_the_readers_own_mobile(self):
		frappe.db.set_value("User", "Administrator", "mobile_no", "+1 555 0199")
		self.assertEqual(self.make_text().send_test(), "+1 555 0199")
		self.send_sms.assert_called_once_with(["+1 555 0199"], "Hi Administrator", success_msg=False)


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
		self.assertIn(email_of(reader), addresses(email))

	def test_a_church_email_goes_only_to_its_own_churchs_people(self):
		mine = make_reader("Mine", church=self.branch_a)
		theirs = make_reader("Theirs", church=self.branch_b)
		email = frappe.get_doc(
			{"doctype": "Church Email", "subject": "_Test Branch", "message": "Hi", "church": self.branch_a}
		).insert()

		recipients = addresses(email)

		self.assertIn(email_of(mine), recipients)
		self.assertNotIn(email_of(theirs), recipients)
		self.assertEqual(email.sending_church, self.branch_a)

	def test_a_smart_list_reaches_only_its_own_churchs_people(self):
		mine = make_reader("ListedMine", church=self.branch_a)
		theirs = make_reader("ListedTheirs", church=self.branch_b)
		smart_list = make_people_list("_Test Church Email Branch List", last_name="_Test Church Email")
		email = frappe.get_doc(
			{
				"doctype": "Church Email",
				"subject": "_Test Branch List",
				"message": "Hi",
				"church": self.branch_a,
				"send_to": "Smart List",
				"smart_list": smart_list.name,
			}
		).insert()

		recipients = addresses(email)

		self.assertIn(email_of(mine), recipients)
		self.assertNotIn(email_of(theirs), recipients)

	def test_another_churchs_person_cannot_be_chosen(self):
		theirs = make_reader("ChosenTheirs", church=self.branch_b)
		email = frappe.get_doc(
			{
				"doctype": "Church Email",
				"subject": "_Test Branch Chosen",
				"message": "Hi",
				"church": self.branch_a,
				"send_to": "Chosen People",
				"people": [{"person": theirs.name}],
			}
		)
		with self.assertRaisesRegex(frappe.ValidationError, "belong to another church"):
			email.insert()
