# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

from unittest.mock import patch

import frappe
from frappe.exceptions import ValidationError
from frappe.handler import execute_cmd
from frappe.tests.utils import FrappeTestCase

from churchit.church_finances.doctype.alms_request.alms_request import create_expense
from churchit.church_foundations.church_access import set_user_church
from churchit.tests.helpers import RollbackEachTest, ensure_user, make_person, make_two_churches


def _ensure(doctype, filters, values):
	name = frappe.db.exists(doctype, filters)
	if name:
		return name
	return frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True).name


class TestAlmsRequest(FrappeTestCase):
	def setUp(self):
		# Expense Type is a tree doctype whose inserts commit, so keep it and its
		# Fund as persistent fixtures rather than per-test records.
		self.fund = _ensure("Fund", {"fund": "_Test Alms Fund"}, {"fund": "_Test Alms Fund"})
		self.expense_type = _ensure(
			"Expense Type",
			{"type": "_Test Alms Expense Type"},
			{"type": "_Test Alms Expense Type", "fund": self.fund},
		)
		self.person = _ensure(
			"Person", {"first_name": "_Test Alms Recipient"}, {"first_name": "_Test Alms Recipient"}
		)

	def _make_request(self, **values):
		return frappe.get_doc(
			{
				"doctype": "Alms Request",
				"recipient_type": "Person",
				"recipient": self.person,
				"requestor": self.person,
				"description": "Needs assistance",
				"status": "Approved",
				**values,
			}
		).insert(ignore_permissions=True)

	def test_title_combines_recipient_amount_and_status(self):
		alms = self._make_request(amount=250)
		self.assertIn("250", alms.title)
		self.assertIn("Approved", alms.title)

	def test_create_expense_requires_an_amount(self):
		alms = self._make_request(expense_type=self.expense_type)
		with self.assertRaises(ValidationError):
			create_expense(alms.name)

	def test_create_expense_requires_an_expense_type(self):
		alms = self._make_request(amount=100)
		with self.assertRaises(ValidationError):
			create_expense(alms.name)

	def test_create_expense_submits_expense_and_links_it_back(self):
		alms = self._make_request(amount=100, expense_type=self.expense_type)
		create_expense(alms.name)

		linked = frappe.db.get_value("Alms Request", alms.name, "associated_expense")
		self.assertTrue(linked)
		expense = frappe.get_doc("Expense", linked)
		self.assertEqual(expense.amount, 100)
		self.assertEqual(expense.type, self.expense_type)
		self.assertEqual(expense.docstatus, 1)

	def test_failed_create_expense_leaves_no_link(self):
		alms = self._make_request(amount=100)
		with self.assertRaises(ValidationError):
			create_expense(alms.name)
		self.assertFalse(frappe.db.get_value("Alms Request", alms.name, "associated_expense"))

	def test_creating_an_expense_takes_a_post(self):
		"""Frappe checks the CSRF token only on a POST, and a GET from another site carries the session."""
		alms = self._make_request(amount=100, expense_type=self.expense_type)

		with (
			patch.object(frappe.local, "request", frappe._dict(method="GET"), create=True),
			patch.object(frappe.local, "form_dict", frappe._dict(alms_request_name=alms.name)),
			self.assertRaises(frappe.PermissionError),
		):
			execute_cmd("churchit.church_finances.doctype.alms_request.alms_request.create_expense")

		self.assertFalse(frappe.db.get_value("Alms Request", alms.name, "associated_expense"))


class TestAlmsRequestAcrossChurches(RollbackEachTest):
	"""Only the requestor's own church records their alms requests."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_root, cls.branch_a, cls.branch_b = make_two_churches()
		cls.addClassCleanup(frappe.clear_cache)
		cls.care_b = ensure_user("_test_alms_care_b@example.com", "Care B", roles=("Church Pastoral Care",))
		set_user_church(cls.care_b, cls.branch_b)
		cls.member_b = ensure_user("_test_alms_member_b@example.com", "Member B")
		cls.person_b = make_person("_Test Alms", "Member B", church=cls.branch_b, user=cls.member_b).name
		set_user_church(cls.member_b, cls.branch_b)
		cls.person_a = make_person("_Test Alms", "Person A", church=cls.branch_a).name

	def request(self, requestor):
		return frappe.get_doc(
			{
				"doctype": "Alms Request",
				"recipient_type": "Person",
				"recipient": self.person_b,
				"requestor": requestor,
				"description": "_Test across churches",
				"church": self.branch_b,
			}
		)

	def test_another_churchs_staff_cannot_record_a_request_for_a_person(self):
		"""Frappe checks the create right on the church the caller sends, before it fetches the person's."""
		frappe.set_user(self.care_b)
		with self.assertRaises(frappe.PermissionError):
			self.request(self.person_a).insert()

		self.assertEqual(self.request(self.person_b).insert().church, self.branch_b)

	def test_a_member_naming_another_churchs_person_is_refused(self):
		"""Not pinned to themselves while the request keeps the church fetched from the other person."""
		frappe.set_user(self.member_b)
		with self.assertRaises(frappe.PermissionError):
			self.request(self.person_a).insert()
