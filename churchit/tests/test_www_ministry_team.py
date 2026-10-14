# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""The /ministry-team portal page and the /ministry-team-response page a request email links to."""

import frappe
from frappe.utils import add_days, getdate

from churchit.church_ministries.member_ministry_team import (
	add_own_block,
	answer_by_key,
	answer_own_assignment,
	remove_own_block,
)
from churchit.tests.helpers import (
	RollbackEachTest,
	ensure_user,
	make_function,
	make_person,
	make_team_assignment,
	make_team_position,
)
from churchit.www import ministry_team, ministry_team_response


class TestMinistryTeamPages(RollbackEachTest):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.email = ensure_user("_test_team_member@example.com", "Team")
		cls.member = make_person("_Test Team Page", "Member", user=cls.email)
		cls.stranger = make_person("_Test Team Page", "Stranger")
		cls.position = make_team_position("_Test Page Greeter", [cls.member.name, cls.stranger.name])
		function = make_function("_Test Team Page Sunday", start_date=add_days(getdate(), 5))
		cls.mine = make_team_assignment(function.name, cls.position.name, cls.member.name)
		cls.theirs = make_team_assignment(function.name, cls.position.name, cls.stranger.name)

	def setUp(self):
		super().setUp()
		frappe.local.form_dict = frappe._dict()

	def context(self, page):
		context = frappe._dict()
		page.get_context(context)
		return context

	def status(self, assignment):
		return frappe.db.get_value("Ministry Team Assignment", assignment.name, "status")

	def test_guests_are_sent_to_login(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.Redirect):
			self.context(ministry_team)

	def test_a_member_sees_only_their_own_assignments(self):
		frappe.set_user(self.email)
		listed = [row.name for row in self.context(ministry_team).assignments]
		self.assertEqual(listed, [self.mine.name])

	def test_a_function_stays_listed_until_it_ends(self):
		today = getdate()
		retreat = make_function(
			"_Test Team Page Retreat", start_date=add_days(today, -1), end_date=add_days(today, 1)
		)
		ended = make_function(
			"_Test Team Page Past", start_date=add_days(today, -3), end_date=add_days(today, -1)
		)
		running = make_team_assignment(retreat.name, self.position.name, self.member.name)
		make_team_assignment(ended.name, self.position.name, self.member.name)

		frappe.set_user(self.email)
		listed = {row.name: row.has_started for row in self.context(ministry_team).assignments}
		self.assertEqual(listed, {running.name: True, self.mine.name: False})

	def test_a_member_answers_their_own_assignment_and_no_one_elses(self):
		frappe.set_user(self.email)
		answer_own_assignment(self.mine.name, "Declined", "Away")
		with self.assertRaises(frappe.PermissionError):
			answer_own_assignment(self.theirs.name, "Declined")

		frappe.set_user("Administrator")
		self.assertEqual(self.status(self.mine), "Declined")
		self.assertEqual(self.status(self.theirs), "Pending")

	def test_anyone_with_the_emailed_link_can_answer_it(self):
		frappe.set_user("Guest")
		frappe.local.form_dict = frappe._dict(key=self.theirs.response_key)
		self.assertEqual(self.context(ministry_team_response).request.first_name, "_Test Team Page")

		answer_by_key(self.theirs.response_key, "Accepted")

		frappe.set_user("Administrator")
		self.assertEqual(self.status(self.theirs), "Accepted")

	def test_a_wrong_key_answers_nothing(self):
		frappe.set_user("Guest")
		for key in ("", "not-a-key"):
			frappe.local.form_dict = frappe._dict(key=key)
			with self.assertRaises(frappe.PermissionError):
				self.context(ministry_team_response)
			with self.assertRaises(frappe.PermissionError):
				answer_by_key(key, "Accepted")

	def test_a_member_adds_and_removes_their_own_blocks(self):
		frappe.set_user(self.email)
		add_own_block(str(add_days(getdate(), 1)), str(add_days(getdate(), 3)), "Away", "1")
		[block] = self.context(ministry_team).blocks
		self.assertEqual((block.reason, block.whole_household), ("Away", 1))

		remove_own_block(block.name)
		self.assertEqual(self.context(ministry_team).blocks, [])

	def test_a_member_cannot_remove_someone_elses_block(self):
		theirs = frappe.get_doc(
			{
				"doctype": "Ministry Team Block",
				"person": self.stranger.name,
				"from_date": getdate(),
				"to_date": getdate(),
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.email)
		with self.assertRaises(frappe.PermissionError):
			remove_own_block(theirs.name)
