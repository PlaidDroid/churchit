# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

from frappe.utils import add_days, getdate

from churchit.church_ministries.report.ministry_team_schedule.ministry_team_schedule import execute
from churchit.tests.helpers import (
	RollbackEachTest,
	make_function,
	make_person,
	make_team_assignment,
	make_team_position,
)


class TestMinistryTeamSchedule(RollbackEachTest):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		people = [make_person("_Test Schedule", name).name for name in ("Ann", "Ben", "Cal")]
		cls.greeter = make_team_position("_Test Schedule Greeter", people)
		cls.usher = make_team_position("_Test Schedule Usher", people)
		cls.function = make_function(
			"_Test Schedule Sunday",
			start_date=add_days(getdate(), 3),
			team_needs=[
				{"position": cls.greeter.name, "quantity": 2},
				{"position": cls.usher.name, "quantity": 1},
			],
		)
		for person, status in zip(people, ("Accepted", "Pending", "Declined"), strict=True):
			make_team_assignment(cls.function.name, cls.greeter.name, person, status=status)

	def rows(self, **filters):
		_columns, data = execute({"function": self.function.name, **filters})
		return {row.position: row for row in data}

	def test_counts_who_is_asked_against_what_is_needed(self):
		rows = self.rows()

		greeter = rows[self.greeter.name]
		self.assertEqual((greeter.needed, greeter.accepted, greeter.pending, greeter.open), (2, 1, 1, 0))
		self.assertEqual(
			greeter.people,
			"_Test Schedule Ann (Accepted), _Test Schedule Ben (Pending), _Test Schedule Cal (Declined)",
		)
		usher = rows[self.usher.name]
		self.assertEqual((usher.needed, usher.open, usher.people), (1, 1, ""))

	def test_only_open_leaves_out_filled_positions(self):
		self.assertEqual(list(self.rows(only_open=1)), [self.usher.name])

	def test_the_date_range_applies_when_no_function_is_chosen(self):
		_columns, data = execute({"from_date": getdate(), "to_date": add_days(getdate(), 7)})
		self.assertIn(self.usher.name, [row.position for row in data])

		_columns, data = execute({"from_date": add_days(getdate(), 4), "to_date": add_days(getdate(), 7)})
		self.assertNotIn(self.usher.name, [row.position for row in data])
