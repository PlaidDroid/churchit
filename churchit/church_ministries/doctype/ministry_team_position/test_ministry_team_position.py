# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe.utils import add_days, getdate

from churchit.tests.helpers import RollbackEachTest, ensure, make_person, make_team_position


class TestMinistryTeamPosition(RollbackEachTest):
	def setUp(self):
		super().setUp()
		frappe.clear_messages()

	def warned_about(self):
		return [
			str(message) for message in frappe.message_log if "checks this position needs" in str(message)
		]

	def test_the_title_names_the_ministry(self):
		person = make_person("_Test Position", "Person").name
		position = make_team_position("_Test Sound", [person], ministry_name="_Test Worship")
		self.assertEqual(position.title, "_Test Sound (_Test Worship)")

	def test_adding_someone_without_the_checks_warns_but_is_kept(self):
		training = ensure("Background Check Type", {"type": "_Test Roster Training"})
		trained = make_person("_Test Roster", "Trained").name
		frappe.get_doc(
			{
				"doctype": "Background Check",
				"person": trained,
				"check_type": training,
				"status": "Cleared",
				"completed_on": getdate(),
				"expires_on": add_days(getdate(), 365),
			}
		).insert(ignore_permissions=True)
		untrained = make_person("_Test Roster", "Untrained").name

		position = make_team_position(
			"_Test Nursery",
			[trained, untrained],
			needs_background_check=1,
			required_checks=[{"check_type": training}],
		)

		self.assertEqual(len(position.people), 2)
		warnings = self.warned_about()
		self.assertTrue(any("_Test Roster Untrained" in warning for warning in warnings), warnings)
		self.assertFalse(any("_Test Roster Trained" in warning for warning in warnings), warnings)

	def test_only_people_newly_added_are_warned_about(self):
		first = make_person("_Test Roster", "First").name
		position = make_team_position("_Test Usher", [first], needs_background_check=1)
		frappe.clear_messages()

		position.append("people", {"person": make_person("_Test Roster", "Second").name})
		position.save(ignore_permissions=True)

		warnings = self.warned_about()
		self.assertTrue(any("_Test Roster Second" in warning for warning in warnings), warnings)
		self.assertFalse(any("_Test Roster First" in warning for warning in warnings), warnings)

	def test_a_position_without_checks_warns_about_nobody(self):
		make_team_position("_Test Greeter", [make_person("_Test Roster", "Greeter").name])
		self.assertEqual(self.warned_about(), [])
