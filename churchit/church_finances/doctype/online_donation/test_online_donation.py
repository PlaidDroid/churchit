# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe.tests.utils import FrappeTestCase

from churchit.church_finances.doctype.online_donation.online_donation import alert_unrecorded_gifts
from churchit.tests.helpers import RollbackEachTest, ensure_user, make_person, make_two_churches


def ensure_online_payment_type():
	# "Online" Payment Type is required for the recorded Collection's Donation row.
	if not frappe.db.exists("Payment Type", "Online"):
		frappe.get_doc({"doctype": "Payment Type", "type": "Online"}).insert(ignore_permissions=True)


def make_gift(fund, amount, **values):
	return frappe.get_doc(
		{"doctype": "Online Donation", "amount": amount, "fund": fund, "currency": "USD", **values}
	).insert(ignore_permissions=True)


def log_charge(gift, amount=None, status="Completed", currency="USD"):
	"""The Integration Request a gateway logs for *gift*, with what it charged."""
	return frappe.get_doc(
		{
			"doctype": "Integration Request",
			"integration_request_service": "Stripe",
			"reference_doctype": "Online Donation",
			"reference_docname": gift.name,
			"status": status,
			"data": frappe.as_json(
				{"amount": gift.amount if amount is None else amount, "currency": currency}
			),
		}
	).insert(ignore_permissions=True)


class TestOnlineDonation(FrappeTestCase):
	def setUp(self):
		ensure_online_payment_type()

	def _make_fund(self):
		return frappe.get_doc({"doctype": "Fund", "fund": "Test Giving Fund"}).insert(ignore_permissions=True)

	def _make_gift(self, fund, amount, **values):
		gift = make_gift(fund.name, amount, **values)
		log_charge(gift)
		return gift

	def test_payment_authorized_records_submitted_collection(self):
		fund = self._make_fund()
		gift = self._make_gift(fund, 50, donor_name="Test Giver", email="giver@example.com")

		redirect = gift.on_payment_authorized("Completed")
		gift.reload()

		# The gift is marked paid and points at its Collection
		self.assertEqual(gift.status, "Paid")
		self.assertTrue(gift.collection)
		self.assertIn(f"success={gift.name}", redirect)

		# A submitted, single-row Collection was created with the right donation
		collection = frappe.get_doc("Collection", gift.collection)
		self.assertEqual(collection.docstatus, 1)
		self.assertEqual(len(collection.donations), 1)
		row = collection.donations[0]
		self.assertEqual(row.payment_type, "Online")
		self.assertEqual(row.fund, fund.name)
		self.assertEqual(row.amount, 50)

		# The fund balance increased by the gift amount
		self.assertEqual(frappe.db.get_value("Fund", fund.name, "balance"), 50)

	def test_anonymous_gift_records_without_person(self):
		fund = self._make_fund()
		gift = self._make_gift(fund, 25, donor_name="Anon", email="anon@example.com")

		gift.on_payment_authorized("Completed")
		gift.reload()

		self.assertEqual(gift.status, "Paid")
		collection = frappe.get_doc("Collection", gift.collection)
		self.assertFalse(collection.donations[0].person)

	def test_failed_payment_marks_failed(self):
		fund = self._make_fund()
		gift = self._make_gift(fund, 10, donor_name="Nope", email="n@example.com")

		gift.on_payment_authorized("Declined")
		gift.reload()

		self.assertEqual(gift.status, "Failed")
		self.assertFalse(gift.collection)

	def test_an_authorized_payment_is_not_a_paid_one(self):
		"""Authorized means the card was held, not charged, so no gift is recorded."""
		fund = self._make_fund()
		gift = self._make_gift(fund, 10, donor_name="Held", email="h@example.com")

		gift.on_payment_authorized("Authorized")
		gift.reload()

		self.assertNotEqual(gift.status, "Paid")
		self.assertFalse(gift.collection)

	def test_authorized_is_idempotent(self):
		fund = self._make_fund()
		gift = self._make_gift(fund, 30, donor_name="Once", email="o@example.com")

		gift.on_payment_authorized("Completed")
		gift.reload()
		first_collection = gift.collection

		# Re-firing the callback must not record a second Collection
		gift.on_payment_authorized("Completed")
		gift.reload()
		self.assertEqual(gift.collection, first_collection)
		self.assertEqual(frappe.db.get_value("Fund", fund.name, "balance"), 30)


class TestChargedGifts(RollbackEachTest):
	"""A gift is recorded only for what the gateway charged, and Finance hears of every other case."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_online_payment_type()
		cls.root, cls.branch_a, cls.branch_b = make_two_churches()
		cls.addClassCleanup(frappe.clear_cache)
		cls.finance_a = cls.bind_finance("_test_gift_finance_a@example.com", "GiftFinanceA", cls.branch_a)
		cls.finance_b = cls.bind_finance("_test_gift_finance_b@example.com", "GiftFinanceB", cls.branch_b)
		cls.fund = (
			frappe.get_doc({"doctype": "Fund", "fund": "_Test Charged Fund", "church": cls.branch_a})
			.insert(ignore_permissions=True)
			.name
		)

	@classmethod
	def bind_finance(cls, email, first_name, church):
		user = ensure_user(email, first_name, roles=("Church Finance",))
		make_person(first_name, "Finance", church=church, user=user)
		return user

	def alerts(self, user, gift):
		return frappe.get_all(
			"Notification Log", filters={"for_user": user, "document_name": gift.name}, pluck="subject"
		)

	def make_gift(self, amount=100):
		return make_gift(
			self.fund, amount, church=self.branch_a, donor_name="Charged", email="charged@example.com"
		)

	def test_a_charge_below_the_gift_records_nothing(self):
		gift = self.make_gift()
		request = log_charge(gift, amount=1)
		# A newer request that is not Completed does not count.
		log_charge(gift, amount=100, status="Failed")
		collections = frappe.db.count("Collection")

		gift.on_payment_authorized("Completed")
		gift.reload()

		self.assertEqual(gift.status, "Needs Review")
		self.assertFalse(gift.collection)
		self.assertEqual(frappe.db.count("Collection"), collections)
		self.assertEqual(gift.transaction_reference, request.name)
		alerts = self.alerts(self.finance_a, gift)
		self.assertEqual(len(alerts), 1)
		self.assertIn("1.00", alerts[0])
		self.assertIn("100.00", alerts[0])
		self.assertFalse(self.alerts(self.finance_b, gift))

	def test_a_charge_in_another_currency_records_nothing(self):
		gift = self.make_gift()
		log_charge(gift, currency="EUR")

		gift.on_payment_authorized("Completed")

		self.assertEqual(frappe.db.get_value("Online Donation", gift.name, "status"), "Needs Review")

	def test_a_matching_charge_records_the_gift(self):
		gift = self.make_gift()
		request = log_charge(gift, amount="100.00")

		gift.on_payment_authorized("Completed")
		gift.reload()

		self.assertEqual(gift.status, "Paid")
		self.assertEqual(gift.transaction_reference, request.name)
		self.assertTrue(gift.collection)
		self.assertFalse(self.alerts(self.finance_a, gift))

	def test_a_failed_callback_is_reported_and_can_be_recorded(self):
		gift = self.make_gift()
		log_charge(gift)

		alert_unrecorded_gifts()

		self.assertEqual(len(self.alerts(self.finance_a, gift)), 1)
		self.assertFalse(self.alerts(self.finance_b, gift))

		collections = frappe.db.count("Collection")
		frappe.set_user(self.finance_a)
		frappe.get_doc("Online Donation", gift.name).record_charged_gift()
		with self.assertRaisesRegex(frappe.ValidationError, "Only a Pending gift"):
			frappe.get_doc("Online Donation", gift.name).record_charged_gift()
		frappe.set_user("Administrator")

		self.assertEqual(frappe.db.count("Collection"), collections + 1)
		self.assertEqual(frappe.db.get_value("Online Donation", gift.name, "status"), "Paid")
		alert_unrecorded_gifts()
		self.assertEqual(len(self.alerts(self.finance_a, gift)), 1)

	def test_another_churchs_finance_cannot_record_a_gift(self):
		gift = self.make_gift()
		log_charge(gift)

		frappe.set_user(self.finance_b)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Online Donation", gift.name).record_charged_gift()
		frappe.set_user("Administrator")

		self.assertEqual(frappe.db.get_value("Online Donation", gift.name, "status"), "Pending")

	def test_only_a_charged_pending_gift_can_be_recorded(self):
		abandoned = self.make_gift()
		cancelled = self.make_gift()
		log_charge(cancelled)
		cancelled.db_set("status", "Cancelled")
		collections = frappe.db.count("Collection")

		frappe.set_user(self.finance_a)
		with self.assertRaisesRegex(frappe.ValidationError, "No completed payment"):
			frappe.get_doc("Online Donation", abandoned.name).record_charged_gift()
		with self.assertRaisesRegex(frappe.ValidationError, "Only a Pending gift"):
			frappe.get_doc("Online Donation", cancelled.name).record_charged_gift()
		frappe.set_user("Administrator")

		self.assertEqual(frappe.db.get_value("Online Donation", abandoned.name, "status"), "Pending")
		self.assertEqual(frappe.db.get_value("Online Donation", cancelled.name, "status"), "Cancelled")
		self.assertFalse(self.alerts(self.finance_a, abandoned))
		self.assertEqual(frappe.db.count("Collection"), collections)
