# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

from unittest.mock import patch

import frappe
from frappe.exceptions import ValidationError
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_months, get_datetime, getdate, now

from churchit.church_finances.report.fund_transactions_by_date import fund_transactions_by_date
from churchit.patches.v1_0 import date_collection_ledger_rows


def _ensure(doctype, filters, values):
	name = frappe.db.exists(doctype, filters)
	if name:
		return name
	return frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True).name


class TestCollection(FrappeTestCase):
	def setUp(self):
		self.fund = (
			frappe.get_doc({"doctype": "Fund", "fund": "_Test Collection Fund"})
			.insert(ignore_permissions=True)
			.name
		)
		self.payment_type = _ensure("Payment Type", {"type": "Cash"}, {"type": "Cash"})

	def _make_collection(self, amount=200, expected_total=None, date=None):
		collection = frappe.get_doc(
			{
				"doctype": "Collection",
				"date": date or now(),
				"expected_total": amount if expected_total is None else expected_total,
			}
		)
		collection.append(
			"donations",
			{"payment_type": self.payment_type, "fund": self.fund, "amount": amount},
		)
		return collection.insert(ignore_permissions=True)

	def test_totals_and_breakdowns_computed(self):
		collection = self._make_collection(amount=200)
		self.assertEqual(collection.total_amount, 200)
		self.assertEqual(collection.imbalance, 0)
		self.assertEqual({f.fund: f.total for f in collection.fund_totals}, {self.fund: 200})
		self.assertEqual(
			{p.payment_type: p.total for p in collection.payment_type_totals},
			{self.payment_type: 200},
		)

	def test_imbalance_computed_from_expected_total(self):
		collection = self._make_collection(amount=200, expected_total=250)
		self.assertEqual(collection.imbalance, -50)

	def test_imbalance_blocks_submit(self):
		collection = self._make_collection(amount=200, expected_total=250)
		with self.assertRaises(ValidationError):
			collection.submit()

	def test_submit_increases_fund_balance(self):
		self._make_collection(amount=200).submit()
		self.assertEqual(frappe.db.get_value("Fund", self.fund, "balance"), 200)

	def test_cancel_reverses_fund_balance(self):
		collection = self._make_collection(amount=200)
		collection.submit()
		self.assertEqual(frappe.db.get_value("Fund", self.fund, "balance"), 200)
		collection.cancel()
		self.assertEqual(frappe.db.get_value("Fund", self.fund, "balance"), 0)

	def ledger(self, **filters):
		rows = frappe.get_all(
			"Financial Transaction",
			{"parenttype": "Fund", **filters},
			["parent", "amount"],
			order_by="parent",
		)
		return [(row.parent, row.amount) for row in rows]

	def test_each_fund_gets_one_ledger_row_and_no_version(self):
		second = frappe.get_doc({"doctype": "Fund", "fund": "_Test Collection Second Fund"}).insert(
			ignore_permissions=True
		)
		funds = (self.fund, second.name)
		versions = {
			fund: frappe.db.count("Version", {"ref_doctype": "Fund", "docname": fund}) for fund in funds
		}
		self._make_collection(amount=7).submit()
		collection = frappe.get_doc({"doctype": "Collection", "date": now(), "expected_total": 180})
		for fund, amount in ((self.fund, 100), (second.name, 30), (self.fund, 50)):
			collection.append(
				"donations", {"payment_type": self.payment_type, "fund": fund, "amount": amount}
			)
		collection.insert(ignore_permissions=True).submit()

		self.assertEqual(self.ledger(source=collection.name), sorted([(self.fund, 150), (second.name, 30)]))
		for fund in funds:
			self.assertEqual(
				frappe.db.count("Version", {"ref_doctype": "Fund", "docname": fund}), versions[fund]
			)
			self.assertEqual(
				frappe.db.get_value("Fund", fund, "balance"),
				sum(amount for _fund, amount in self.ledger(parent=fund)),
			)

		collection.cancel()
		self.assertEqual(self.ledger(source=collection.name), [])
		self.assertEqual(self.ledger(parent=self.fund), [(self.fund, 7)])
		self.assertEqual([frappe.db.get_value("Fund", fund, "balance") for fund in funds], [7, 0])

	def ledger_dates(self, from_date, to_date):
		filters = {"from_date": from_date, "to_date": to_date}
		return [row.date for row in fund_transactions_by_date.execute(filters)[1] if row.amount == 321]

	def test_a_collection_entered_late_shows_in_its_own_month(self):
		last_month = add_months(getdate(), -1).replace(day=15)
		self._make_collection(amount=321, date=f"{last_month} 10:00:00").submit()

		self.assertEqual(self.ledger_dates(last_month, last_month), [get_datetime(f"{last_month} 10:00:00")])
		self.assertEqual(self.ledger_dates(getdate(), getdate()), [])

	def test_the_patch_dates_old_ledger_rows_from_their_collection(self):
		collection = self._make_collection(amount=321, date="2030-04-04 09:30:00")
		collection.submit()
		row = frappe.db.get_value(
			"Financial Transaction", {"source_type": "Collection", "source": collection.name}, "name"
		)
		frappe.db.set_value("Financial Transaction", row, "date", now())

		date_collection_ledger_rows.execute()
		self.assertEqual(
			frappe.db.get_value("Financial Transaction", row, "date"), get_datetime("2030-04-04 09:30:00")
		)

		with patch.object(frappe.db, "set_value") as set_value:
			date_collection_ledger_rows.execute()
		set_value.assert_not_called()
