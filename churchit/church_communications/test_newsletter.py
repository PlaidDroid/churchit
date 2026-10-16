# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

from unittest import mock

import frappe
from frappe.exceptions import ValidationError

from churchit.church_communications.newsletter import (
	get_subscription_status,
	set_subscription,
	unsubscribe,
	unsubscribed_emails,
)
from churchit.tests.helpers import (
	RollbackEachTest,
	ensure_root_church,
	ensure_user,
	force_single_church,
	make_person,
)


class TestChurchEmailSubscription(RollbackEachTest):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		force_single_church()
		cls.church = ensure_root_church()
		cls.user = ensure_user("_test_subscriber@example.com", "_Test Subscriber")
		cls.person = make_person("_Test Newsletter", "Member", user=cls.user)
		cls.person.append("emails", {"email_address": "_test_work@example.com"})
		cls.person.append("emails", {"email_address": "_test_home@example.com", "is_primary": 1})
		cls.person.save(ignore_permissions=True)

	def test_a_member_receives_church_emails_until_they_unsubscribe(self):
		frappe.set_user(self.user)
		status = get_subscription_status()
		self.assertEqual((status["email"], status["subscribed"]), ("_test_home@example.com", True))

		self.assertEqual(set_subscription("0"), {"subscribed": False})
		self.assertFalse(get_subscription_status()["subscribed"])
		self.assertIn("_test_home@example.com", unsubscribed_emails(self.church))

		self.assertEqual(set_subscription(1), {"subscribed": True})
		self.assertNotIn("_test_home@example.com", unsubscribed_emails(self.church))

	def test_unsubscribing_twice_keeps_one_record(self):
		frappe.set_user(self.user)
		set_subscription(0)
		set_subscription(0)
		self.assertEqual(
			frappe.db.count(
				"Email Unsubscribe", {"email": "_test_home@example.com", "reference_name": self.church}
			),
			1,
		)

	def test_the_link_in_an_email_stops_that_churchs_emails(self):
		frappe.set_user("Guest")
		with mock.patch.object(frappe.db, "commit"):
			unsubscribe(
				email="_test_linked@example.com", church=self.church, doctype="Church Email", name="x"
			)
		self.assertIn("_test_linked@example.com", unsubscribed_emails(self.church))

	def test_status_falls_back_to_the_user_email(self):
		user = ensure_user("_test_personless@example.com", "_Test Personless")
		frappe.set_user(user)
		self.assertEqual(get_subscription_status()["email"], "_test_personless@example.com")

	def test_guests_have_no_status(self):
		frappe.set_user("Guest")
		self.assertIsNone(get_subscription_status()["email"])

	def test_set_subscription_needs_an_email_on_file(self):
		frappe.set_user("Guest")
		with self.assertRaises(ValidationError):
			set_subscription(1)
