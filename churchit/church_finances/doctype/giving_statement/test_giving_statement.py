# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

from unittest.mock import patch

import frappe
from frappe.handler import execute_cmd
from frappe.tests.utils import FrappeTestCase

from churchit.church_finances.doctype.giving_statement.giving_statement import (
	generate_statements,
	get_givers,
	get_statement_recipients,
	statement_header,
)
from churchit.church_finances.doctype.payment_type.payment_type import (
	NON_CASH,
	create_non_cash_payment_type,
)
from churchit.church_foundations.church_access import set_user_church
from churchit.patches.v1_0 import mark_funds_tax_deductible
from churchit.tests.helpers import (
	RollbackEachTest,
	ensure,
	ensure_root_church,
	ensure_user,
	make_branch,
	make_person,
	make_primary_contact,
	make_two_churches,
	set_multi_church,
)

PERIOD = ("2029-01-01", "2029-12-31")


def make_collection(date, donations, submit=True, church=None, fund=None, rows=()):
	"""A Collection carrying one Donation row per (person, amount) pair, plus any whole *rows*."""
	fund_name = f"_Test Statement Fund {church}" if church else "_Test Statement Fund"
	fund = fund or ensure(
		"Fund", {"fund": fund_name}, {"fund": fund_name, "church": church} if church else None
	)
	payment_type = ensure("Payment Type", {"type": "_Test Statement Cash"}, {"type": "_Test Statement Cash"})
	collection = frappe.get_doc(
		{"doctype": "Collection", "date": date, "expected_total": sum(a for _p, a in donations)}
	)
	if church:
		collection.church = church
	for person, amount in donations:
		collection.append(
			"donations",
			{"person": person, "amount": amount, "fund": fund, "payment_type": payment_type},
		)
	for row in rows:
		collection.append("donations", {"fund": fund, **row})
	collection.insert(ignore_permissions=True)
	if submit:
		collection.submit()
	return collection


class TestGivingStatement(FrappeTestCase):
	def setUp(self):
		self.giver = make_person("_Test Statement", "Giver")

	def _statement(self, **values):
		return frappe.get_doc(
			{
				"doctype": "Giving Statement",
				"person": self.giver.name,
				"from_date": PERIOD[0],
				"to_date": PERIOD[1],
				**values,
			}
		).insert(ignore_permissions=True)

	def test_lines_and_total_come_from_submitted_donations(self):
		make_collection("2029-03-01", [(self.giver.name, 100)])
		make_collection("2029-06-01", [(self.giver.name, 250)])

		statement = self._statement()

		self.assertEqual(len(statement.lines), 2)
		self.assertEqual(statement.total_amount, 350)

	def test_draft_collections_are_excluded(self):
		make_collection("2029-03-01", [(self.giver.name, 100)], submit=False)
		self.assertEqual(self._statement().total_amount, 0)

	def test_gifts_outside_the_period_are_excluded(self):
		make_collection("2028-12-31", [(self.giver.name, 500)])
		make_collection("2029-05-05", [(self.giver.name, 75)])

		statement = self._statement()

		self.assertEqual(len(statement.lines), 1)
		self.assertEqual(statement.total_amount, 75)

	def test_a_gift_at_2359_on_the_last_day_is_included(self):
		make_collection(f"{PERIOD[1]} 23:59:00", [(self.giver.name, 40)])
		make_collection("2030-01-01 00:00:00", [(self.giver.name, 900)])

		self.assertEqual(self._statement().total_amount, 40)
		self.assertIn(self.giver.name, get_givers(PERIOD[1], PERIOD[1]))

	def test_household_statement_covers_every_family_member(self):
		family = frappe.get_doc({"doctype": "Family", "family_name": "_Test Statement Household"}).insert(
			ignore_permissions=True
		)
		self.giver.family = family.name
		self.giver.save(ignore_permissions=True)
		spouse = make_person("_Test Statement", "Spouse", family=family.name)

		make_collection("2029-04-01", [(self.giver.name, 60), (spouse.name, 40)])

		statement = self._statement(family=family.name)

		self.assertEqual(statement.total_amount, 100)
		self.assertCountEqual([line.person for line in statement.lines], [self.giver.name, spouse.name])

	def test_rebuilding_replaces_lines_rather_than_appending(self):
		make_collection("2029-03-01", [(self.giver.name, 100)])
		statement = self._statement()

		statement.save(ignore_permissions=True)

		self.assertEqual(len(statement.lines), 1)
		self.assertEqual(statement.total_amount, 100)

	def test_title_carries_the_name_and_year(self):
		statement = self._statement()
		self.assertEqual(statement.title, f"{self.giver.full_name} - 2029")

	def test_reversed_dates_are_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._statement(from_date="2029-12-31", to_date="2029-01-01")


class TestStatementGeneration(FrappeTestCase):
	def setUp(self):
		frappe.db.set_single_value("Giving Settings", "statement_grouping", "Household")

	def test_household_grouping_issues_one_statement_per_family(self):
		family = frappe.get_doc({"doctype": "Family", "family_name": "_Test Gen Household"}).insert(
			ignore_permissions=True
		)
		head = make_primary_contact(make_person("_Test Gen", "Head", family=family.name))
		spouse = make_person("_Test Gen", "Spouse", family=family.name)
		make_collection("2029-02-02", [(head.name, 10), (spouse.name, 20)])

		recipients = get_statement_recipients(*PERIOD)
		ours = [r for r in recipients if r["family"] == family.name]

		self.assertEqual(len(ours), 1)
		self.assertEqual(ours[0]["person"], head.name, "addressed to the primary contact")

	def test_person_grouping_issues_one_statement_each(self):
		frappe.db.set_single_value("Giving Settings", "statement_grouping", "Person")
		family = frappe.get_doc({"doctype": "Family", "family_name": "_Test Gen Split"}).insert(
			ignore_permissions=True
		)
		one = make_person("_Test Split", "One", family=family.name)
		two = make_person("_Test Split", "Two", family=family.name)
		make_collection("2029-02-02", [(one.name, 10), (two.name, 20)])

		people = [r["person"] for r in get_statement_recipients(*PERIOD)]

		self.assertIn(one.name, people)
		self.assertIn(two.name, people)

	def test_generation_is_idempotent(self):
		giver = make_person("_Test Idempotent", "Giver")
		make_collection("2029-07-07", [(giver.name, 45)])

		first = generate_statements(*PERIOD)
		second = generate_statements(*PERIOD)

		self.assertGreaterEqual(first["created"], 1)
		self.assertEqual(second["created"], 0, "a second run rebuilds rather than duplicates")
		self.assertEqual(
			frappe.db.count("Giving Statement", {"person": giver.name, "from_date": PERIOD[0]}), 1
		)

	def test_people_without_gifts_get_no_statement(self):
		quiet = make_person("_Test Quiet", "Person")
		generate_statements(*PERIOD)
		self.assertFalse(frappe.db.exists("Giving Statement", {"person": quiet.name}))

	def test_generating_statements_takes_a_post(self):
		"""Frappe checks the CSRF token only on a POST, and a GET from another site carries the session."""
		giver = make_person("_Test Get", "Giver")
		make_collection("2029-08-08", [(giver.name, 5)])

		with (
			patch.object(frappe.local, "request", frappe._dict(method="GET"), create=True),
			patch.object(frappe.local, "form_dict", frappe._dict(from_date=PERIOD[0], to_date=PERIOD[1])),
			self.assertRaises(frappe.PermissionError),
		):
			execute_cmd(
				"churchit.church_finances.doctype.giving_statement.giving_statement.generate_statements"
			)

		self.assertFalse(frappe.db.exists("Giving Statement", {"person": giver.name}))


class TestStatementChurch(RollbackEachTest):
	"""A statement stays in one of the issuer's churches, whoever the giver is."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_root, cls.branch_a, cls.branch_b = make_two_churches()
		cls.addClassCleanup(frappe.clear_cache)
		cls.finance_a = ensure_user(
			"_test_statement_finance_a@example.com", "Finance A", roles=("Church Finance",)
		)
		set_user_church(cls.finance_a, cls.branch_a)
		cls.giver_b = make_person("_Test Statement", "Giver B", church=cls.branch_b).name

	def statement(self, **values):
		return frappe.get_doc(
			{
				"doctype": "Giving Statement",
				"person": self.giver_b,
				"from_date": PERIOD[0],
				"to_date": PERIOD[1],
				**values,
			}
		)

	def test_a_statement_for_another_churchs_giver_is_issued_in_the_issuers_church(self):
		frappe.set_user(self.finance_a)
		self.assertEqual(self.statement().insert().church, self.branch_a)

	def test_an_empty_church_does_not_file_a_statement_in_the_givers_church(self):
		"""Frappe fills an empty church from the person after its create check has passed."""
		frappe.set_user(self.finance_a)
		with self.assertRaises(frappe.PermissionError):
			self.statement(church="").insert()

	def test_another_churchs_statement_for_the_period_does_not_stop_the_run(self):
		"""A giver who gave at both churches gets a statement from each, whoever issued first."""
		self.statement(church=self.branch_b).insert(ignore_permissions=True)
		make_collection("2029-05-05", [(self.giver_b, 10)], church=self.branch_a)
		frappe.set_user(self.finance_a)

		self.assertEqual(generate_statements(*PERIOD), {"created": 1, "updated": 0})
		self.assertEqual(
			frappe.get_all(
				"Giving Statement", filters={"person": self.giver_b}, pluck="church", order_by="church"
			),
			sorted([self.branch_a, self.branch_b]),
		)

	def test_another_church_cannot_take_a_statement(self):
		"""Frappe checks write on the church the caller sends, as a REST update does."""
		name = self.statement(church=self.branch_b).insert(ignore_permissions=True).name
		frappe.set_user(self.finance_a)
		statement = frappe.get_doc("Giving Statement", name)
		statement.church = self.branch_a

		with self.assertRaises(frappe.PermissionError):
			statement.save()


class TestStatementHeader(FrappeTestCase):
	def test_header_prefers_the_legal_name_and_carries_the_acknowledgment(self):
		frappe.db.set_single_value("Giving Settings", "statement_acknowledgment", "_Test wording.")
		church = frappe.get_all("Church", order_by="lft asc", limit=1, pluck="name")[0]
		frappe.db.set_value("Church", church, {"legal_name": "_Test Legal Name", "tax_id": "12-3456789"})

		header = statement_header()

		self.assertEqual(header["name"], "_Test Legal Name")
		self.assertEqual(header["tax_id"], "12-3456789")
		self.assertEqual(header["acknowledgment"], "_Test wording.")


class TestBranchStatementHeader(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.root = ensure_root_church()
		set_multi_church(True)
		cls.branch = make_branch("_Test Statement Branch", "TSB")
		cls.addClassCleanup(frappe.clear_cache)

	def test_a_branch_without_a_tax_id_prints_the_parents(self):
		frappe.db.set_value("Church", self.root, {"legal_name": "_Test Parent Legal", "tax_id": "98-7654321"})
		frappe.db.set_value("Church", self.branch, {"legal_name": None, "tax_id": None})

		header = statement_header(self.branch)

		self.assertEqual(header["name"], "_Test Parent Legal")
		self.assertEqual(header["tax_id"], "98-7654321")

	def test_a_branch_with_its_own_identity_prints_that(self):
		frappe.db.set_value(
			"Church", self.branch, {"legal_name": "_Test Branch Legal", "tax_id": "11-1111111"}
		)

		header = statement_header(self.branch)

		self.assertEqual(header["name"], "_Test Branch Legal")
		self.assertEqual(header["tax_id"], "11-1111111")


class TestTaxDeductibleFunds(FrappeTestCase):
	"""A statement is a tax receipt, so it counts only gifts to funds that are tax deductible."""

	def setUp(self):
		self.giver = make_person("_Test Deductible", "Giver")
		self.pass_through = ensure(
			"Fund",
			{"fund": "_Test Pass-Through Fund"},
			{"fund": "_Test Pass-Through Fund", "is_tax_deductible": 0},
		)

	def test_a_gift_to_a_fund_that_is_not_tax_deductible_is_left_off(self):
		make_collection("2029-03-01", [(self.giver.name, 100)])
		make_collection("2029-03-08", [(self.giver.name, 40)], fund=self.pass_through)

		statement = frappe.get_doc(
			{
				"doctype": "Giving Statement",
				"person": self.giver.name,
				"from_date": PERIOD[0],
				"to_date": PERIOD[1],
			}
		).insert(ignore_permissions=True)

		self.assertEqual([line.amount for line in statement.lines], [100])
		self.assertEqual(statement.total_amount, 100)

	def test_a_giver_of_only_such_gifts_gets_no_statement(self):
		make_collection("2029-03-08", [(self.giver.name, 40)], fund=self.pass_through)
		self.assertNotIn(self.giver.name, get_givers(*PERIOD))

	def test_the_patch_marks_every_fund_that_existed_tax_deductible(self):
		Fund = frappe.qb.DocType("Fund")
		frappe.qb.update(Fund).set(Fund.is_tax_deductible, 0).run()

		mark_funds_tax_deductible.execute()

		self.assertFalse(frappe.db.exists("Fund", {"is_tax_deductible": 0}))

	def test_running_the_patch_again_keeps_a_fund_the_user_unticked(self):
		ensure("Fund", {"fund": "_Test Statement Fund"})
		frappe.db.set_value("Fund", self.pass_through, "is_tax_deductible", 0)

		mark_funds_tax_deductible.execute()

		self.assertEqual(frappe.db.get_value("Fund", self.pass_through, "is_tax_deductible"), 0)


class TestNonCashOnStatements(FrappeTestCase):
	"""IRS Publication 1771: the church describes donated property and does not state its value."""

	def setUp(self):
		create_non_cash_payment_type()
		self.giver = make_person("_Test NonCash", "Giver")

	def test_the_statement_shows_the_description_with_no_amount(self):
		make_collection(
			"2029-04-01",
			[(self.giver.name, 25)],
			rows=[
				{
					"person": self.giver.name,
					"payment_type": NON_CASH,
					"amount": 0,
					"description": "_Test piano",
				}
			],
		)

		statement = frappe.get_doc(
			{
				"doctype": "Giving Statement",
				"person": self.giver.name,
				"from_date": PERIOD[0],
				"to_date": PERIOD[1],
			}
		).insert(ignore_permissions=True)

		self.assertEqual(statement.total_amount, 25)
		self.assertEqual([line.description for line in statement.lines if line.description], ["_Test piano"])
		html = frappe.get_print("Giving Statement", statement.name, "Contribution Statement")
		row = next(part for part in html.split("<tr") if "_Test piano" in part)
		self.assertNotIn("0.00", row, "a non-cash line states no value")

