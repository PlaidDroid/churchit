# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from jinja2.exceptions import SecurityError, UndefinedError

from churchit.church_communications.newsletter import set_subscribed
from churchit.church_communications.sending import (
	Recipient,
	can_send_texts,
	check_placeholders,
	get_email_recipients,
	get_text_recipients,
	get_unique_recipients,
	get_values,
	render,
)
from churchit.tests.helpers import RollbackEachTest, ensure_root_church, force_single_church, make_person

ANN = get_values({"first_name": "Ann", "last_name": "Lee", "display_name": "Ann Lee", "full_name": "Ann Lee"})


class TestPlaceholders(FrappeTestCase):
	def test_each_placeholder_is_filled_in_at_the_top_and_under_doc(self):
		self.assertEqual(render("{{ first_name }} {{ doc.last_name }}", ANN), "Ann Lee")
		self.assertEqual(render("{{ display_name }}", get_values({"first_name": "Ann"})), "")

	def test_names_are_escaped_only_in_an_html_message(self):
		values = get_values({"first_name": "<b>Tom</b> & Ann"})
		self.assertEqual(render("Hi {{ first_name }}", values), "Hi <b>Tom</b> & Ann")
		self.assertEqual(
			render("<p>{{ first_name }}</p>", values, html=True), "<p>&lt;b&gt;Tom&lt;/b&gt; &amp; Ann</p>"
		)

	def test_the_sandbox_cannot_reach_frappe_or_python(self):
		with self.assertRaises(UndefinedError):
			render("{{ frappe.db.sql('select 1') }}", ANN)
		with self.assertRaises(SecurityError):
			render("{{ first_name.__class__.__mro__ }}", ANN)

	def test_a_template_is_refused_unless_it_uses_only_the_persons_names(self):
		refused = (
			"{{ frappe.db.get_value('User', 'Administrator', 'api_secret') }}",
			"{{ doc.name }}",
			"{% for row in doc.items() %}{{ row }}{% endfor %}",
			"{{ first_name.__class__.__mro__ }}",
			"Dear {{ first_name",
		)
		for source in refused:
			with self.subTest(source), self.assertRaises(frappe.ValidationError):
				check_placeholders(source, "Message")

		check_placeholders(
			"{% if display_name %}{{ display_name }}{% endif %} {{ doc.first_name | e }}", "Message"
		)

	def test_the_refusal_names_the_placeholders_that_work(self):
		with self.assertRaisesRegex(frappe.ValidationError, r"\{\{ frappe \}\}.*\{\{ first_name \}\}"):
			check_placeholders("{{ frappe.session.user }}", "Subject")


class TestRecipients(RollbackEachTest):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		force_single_church()
		cls.church = ensure_root_church()

	def test_an_address_is_used_once_whoever_holds_it(self):
		addresses = {"P1": "home@example.com", "P2": "HOME@example.com", "P3": "work@example.com"}
		self.assertEqual(
			get_unique_recipients(["P1", "P2", "P3", "P4"], addresses, "Email Address"),
			[Recipient("P1", "home@example.com"), Recipient("P3", "work@example.com")],
		)

	def test_a_number_is_used_once_however_it_is_written(self):
		numbers = {"P1": "+1 (555) 010-0101", "P2": "15550100101"}
		self.assertEqual(
			get_unique_recipients(["P1", "P2"], numbers, "Phone Number"),
			[Recipient("P1", "+1 (555) 010-0101")],
		)

	def test_email_recipients_leave_out_bad_bounced_and_unsubscribed_addresses(self):
		people = {
			name: make_person(name, "_Test Sending", emails=[{"email_address": address}])
			for name, address in (
				("Good", "_test_sending_good@example.com"),
				("Bounced", "_test_sending_bounced@example.com"),
				("Stopped", "_test_sending_stopped@example.com"),
			)
		}
		frappe.db.set_value("Email Address", people["Bounced"].emails[0].name, "is_bounced", 1)
		set_subscribed("_test_sending_stopped@example.com", self.church, False)

		recipients = get_email_recipients([person.name for person in people.values()], self.church)

		self.assertEqual(recipients, [Recipient(people["Good"].name, "_test_sending_good@example.com")])

	def test_text_recipients_are_at_their_primary_phone(self):
		person = make_person(
			"Texter",
			"_Test Sending",
			phones=[{"phone_number": "555 0101"}, {"phone_number": "555 0102", "is_primary": 1}],
		)
		self.assertEqual(get_text_recipients([person.name]), [Recipient(person.name, "555 0102")])


class TestTextsSetUp(FrappeTestCase):
	def test_texts_need_a_gateway_or_an_app_that_sends_them(self):
		with (
			mock.patch("frappe.get_hooks", return_value=[]),
			mock.patch("frappe.db.get_single_value", return_value=None),
		):
			self.assertFalse(can_send_texts())
		with mock.patch("frappe.db.get_single_value", return_value="https://sms.example.com/send"):
			self.assertTrue(can_send_texts())
		with mock.patch("frappe.get_hooks", return_value=["my_app.sms.send"]):
			self.assertTrue(can_send_texts())
