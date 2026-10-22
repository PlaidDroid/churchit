# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

from unittest.mock import MagicMock, patch

import frappe
from frappe.exceptions import ValidationError
from frappe.handler import execute_cmd
from frappe.tests.utils import FrappeTestCase

from churchit.tests.helpers import RollbackEachTest, ensure, ensure_user, make_person, make_two_churches
from churchit.www.give import get_context, start_donation


class TestGivePage(FrappeTestCase):
	def setUp(self):
		self.gateway = ensure("Payment Gateway", {"gateway": "_Test Gateway"})
		self.other_gateway = ensure("Payment Gateway", {"gateway": "_Test Other Gateway"})
		self.fund = frappe.get_doc(
			{"doctype": "Fund", "fund": "_Test Giving Fund", "allow_giving": 1}
		).insert(ignore_permissions=True)
		self.closed_fund = frappe.get_doc({"doctype": "Fund", "fund": "_Test Closed Fund"}).insert(
			ignore_permissions=True
		)
		self._configure(enabled=1, allow_anonymous=1, gateways=[self.gateway, self.other_gateway])
		frappe.local.form_dict = frappe._dict()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _configure(self, gateways=(), **values):
		settings = frappe.get_doc("Giving Settings")
		settings.update(values)
		settings.gateways = []
		for gateway in gateways:
			settings.append("gateways", {"payment_gateway": gateway})
		settings.save(ignore_permissions=True)

	def _context(self):
		context = frappe._dict()
		get_context(context)
		return context

	def test_context_offers_giving_funds_and_gateways(self):
		context = self._context()
		self.assertTrue(context.enabled)
		self.assertIn(self.fund.name, [f.name for f in context.funds])
		self.assertNotIn(self.closed_fund.name, [f.name for f in context.funds])
		self.assertEqual([g["name"] for g in context.gateways], [self.gateway, self.other_gateway])
		self.assertEqual(context.default_gateway, self.gateway)

	def test_context_prefills_donor_details_from_the_linked_person(self):
		user = ensure_user("_test_giver@example.com", "_Test Giver")
		person = make_person("_Test Generous", "Giver", user=user)
		person.append("emails", {"email_address": "_test_giving@example.com"})
		person.save(ignore_permissions=True)

		frappe.set_user(user)
		context = self._context()
		self.assertEqual(context.donor_name, "_Test Generous Giver")
		self.assertEqual(context.donor_email, "_test_giving@example.com")

	def test_guests_are_redirected_to_login_when_anonymous_giving_is_off(self):
		self._configure(allow_anonymous=0, gateways=[self.gateway])
		frappe.set_user("Guest")
		with self.assertRaises(frappe.Redirect):
			self._context()
		self.assertEqual(frappe.local.flags.redirect_location, "/login?redirect-to=/give")

	def test_start_donation_validates_amount_fund_and_gateway(self):
		with self.assertRaises(ValidationError):
			start_donation(0, self.fund.name, donor_name="A", email="a@example.com")
		with self.assertRaises(ValidationError):
			start_donation(10, self.closed_fund.name, donor_name="A", email="a@example.com")
		with self.assertRaises(ValidationError):
			start_donation(
				10, self.fund.name, payment_gateway="Not Offered", donor_name="A", email="a@example.com"
			)

	def test_start_donation_requires_a_configured_gateway(self):
		self._configure(gateways=[])
		with self.assertRaises(ValidationError):
			start_donation(10, self.fund.name, donor_name="A", email="a@example.com")

	def test_start_donation_is_blocked_when_giving_is_disabled(self):
		self._configure(enabled=0, gateways=[self.gateway])
		with self.assertRaises(ValidationError):
			start_donation(10, self.fund.name, donor_name="A", email="a@example.com")

	def test_anonymous_gifts_need_a_name_and_email(self):
		frappe.set_user("Guest")
		with self.assertRaises(ValidationError):
			start_donation(10, self.fund.name)

	def test_anonymous_gifts_are_refused_when_not_allowed(self):
		self._configure(allow_anonymous=0, gateways=[self.gateway])
		frappe.set_user("Guest")
		with self.assertRaises(ValidationError):
			start_donation(10, self.fund.name, donor_name="A", email="a@example.com")

	def test_start_donation_records_a_pending_gift_and_hands_off_to_the_gateway(self):
		controller = MagicMock()
		controller.get_payment_url.return_value = "https://pay.example.com/checkout"
		with patch("payments.utils.get_payment_gateway_controller", return_value=controller) as resolve:
			url = start_donation(
				"25.50",
				self.fund.name,
				payment_gateway=self.other_gateway,
				donor_name="Anon",
				email="anon@example.com",
			)

		self.assertEqual(url, "https://pay.example.com/checkout")
		resolve.assert_called_once_with(self.other_gateway)
		controller.validate_transaction_currency.assert_called_once_with("USD")

		kwargs = controller.get_payment_url.call_args.kwargs
		gift = frappe.get_doc("Online Donation", kwargs["reference_docname"])
		self.assertEqual(gift.status, "Pending")
		self.assertEqual(gift.amount, 25.5)
		self.assertEqual(gift.payment_gateway, self.other_gateway)
		self.assertEqual(gift.donor_name, "Anon")
		self.assertIsNone(gift.person)
		self.assertEqual(kwargs["amount"], 25.5)
		self.assertEqual(kwargs["payer_email"], "anon@example.com")
		self.assertIn(f"success={gift.name}", kwargs["redirect_to"])

	def test_start_donation_refuses_a_get(self):
		"""A GET carries no CSRF token, so another site could open pending gifts from a visitor's browser."""
		frappe.set_user("Guest")
		frappe.local.request = frappe._dict(method="GET")
		self.addCleanup(delattr, frappe.local, "request")
		frappe.local.form_dict = frappe._dict(
			amount=10, fund=self.fund.name, donor_name="Anon", email="anon@example.com"
		)
		gifts = frappe.db.count("Online Donation")

		controller = MagicMock(spec=["get_payment_url"])
		with patch("payments.utils.get_payment_gateway_controller", return_value=controller):
			with self.assertRaises(frappe.PermissionError):
				execute_cmd("churchit.www.give.start_donation")

		self.assertEqual(frappe.db.count("Online Donation"), gifts)

	def test_logged_in_gifts_link_the_session_person(self):
		user = ensure_user("_test_linked_giver@example.com", "_Test Linked Giver")
		person = make_person("_Test Linked", "Giver", user=user).name
		frappe.set_user(user)

		controller = MagicMock(spec=["get_payment_url"])
		with patch("payments.utils.get_payment_gateway_controller", return_value=controller):
			start_donation(10, self.fund.name)

		gift = frappe.get_doc(
			"Online Donation", controller.get_payment_url.call_args.kwargs["reference_docname"]
		)
		self.assertEqual(gift.person, person)
		self.assertEqual(gift.payment_gateway, self.gateway)


class TestGivingAtABranch(RollbackEachTest):
	"""A gift belongs to the church whose gateway takes the money."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.root, cls.branch_a, cls.branch_b = make_two_churches()
		cls.addClassCleanup(frappe.clear_cache)
		frappe.db.set_value("Church", cls.branch_b, "publish", 1)
		cls.shared_gateway = ensure("Payment Gateway", {"gateway": "_Test Shared Gateway"})
		cls.branch_gateway = ensure("Payment Gateway", {"gateway": "_Test Branch B Gateway"})
		settings = frappe.get_doc("Giving Settings")
		settings.update({"enabled": 1, "allow_anonymous": 1, "gateways": []})
		settings.append("gateways", {"payment_gateway": cls.shared_gateway})
		settings.append("gateways", {"payment_gateway": cls.branch_gateway, "church": cls.branch_b})
		settings.save(ignore_permissions=True)
		cls.branch_fund = ensure(
			"Fund",
			{"fund": "_Test Branch B Fund"},
			{"fund": "_Test Branch B Fund", "allow_giving": 1, "church": cls.branch_b},
		)
		cls.shared_fund = ensure(
			"Fund",
			{"fund": "_Test Shared Giving Fund"},
			{"fund": "_Test Shared Giving Fund", "allow_giving": 1, "is_shared": 1},
		)

	def give(self, fund, user="Guest", **form):
		frappe.set_user(user)
		frappe.local.form_dict = frappe._dict(form)
		controller = MagicMock(spec=["get_payment_url"])
		with patch("payments.utils.get_payment_gateway_controller", return_value=controller):
			start_donation(10, fund, donor_name="Anon", email="anon@example.com")
		return frappe.get_doc(
			"Online Donation", controller.get_payment_url.call_args.kwargs["reference_docname"]
		)

	def test_a_gift_to_a_branch_fund_belongs_to_that_branch(self):
		gift = self.give(self.branch_fund)

		self.assertEqual((gift.church, gift.payment_gateway), (self.branch_b, self.branch_gateway))

	def test_a_shared_fund_gift_belongs_to_the_branch_whose_page_took_it(self):
		user = ensure_user("_test_branch_a_giver@example.com", "_Test Branch A Giver")
		make_person("_Test Branch A", "Giver", user=user, church=self.branch_a)

		gift = self.give(self.shared_fund, user=user, church=self.branch_b)

		self.assertEqual((gift.church, gift.payment_gateway), (self.branch_b, self.branch_gateway))
