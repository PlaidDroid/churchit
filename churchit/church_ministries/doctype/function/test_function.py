# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

from datetime import date, time, timedelta

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, get_datetime, getdate

from churchit.church_ministries.doctype.function.function import _as_time, apply_template
from churchit.tests.helpers import ensure, make_function, make_person, make_team_position


class TestFunction(FrappeTestCase):
	def setUp(self):
		self.person = make_person("_Test Function Attendee").name

	def test_title_includes_start_date(self):
		function = make_function("_Test Titled", start_date="2031-05-01")
		self.assertEqual(function.title, "_Test Titled: 2031-05-01")

	def test_attendance_total_counts_only_present_types(self):
		function = make_function("_Test Counted", start_date=getdate())
		for attendance_type in ("Confirmed", "Assumed", "Checked-In", "Absent", "Signed-Up"):
			function.append(
				"attendance",
				{"person": make_person(f"_Test {attendance_type}").name, "attendance_type": attendance_type},
			)
		function.save(ignore_permissions=True)
		self.assertEqual(function.attendance_total, 3)

	def test_apply_template_copies_whitelisted_fields_and_tables(self):
		ministry = ensure("Ministry", {"ministry_name": "_Test Template Ministry"})
		template = make_function(
			"_Test Template",
			description="Weekly gathering",
			all_day=1,
			associated_ministry=ministry,
			publish=1,
		)
		template.append("attendance", {"person": self.person, "attendance_type": "Assumed"})
		template.save(ignore_permissions=True)

		copied = apply_template(template.name)

		self.assertEqual(copied["description"], "Weekly gathering")
		self.assertEqual(copied["all_day"], 1)
		self.assertEqual(copied["associated_ministry"], ministry)
		self.assertEqual([row["person"] for row in copied["attendance"]], [self.person])
		# Only the whitelisted fields travel: dates, publish state and name do not.
		for field in ("function_name", "start_date", "publish", "name"):
			self.assertNotIn(field, copied)

	def test_apply_template_moves_the_schedule_to_the_new_start_date(self):
		template = make_function("_Test Retreat Plan", start_date="2031-05-02", end_date="2031-05-04")
		template.append("schedule", {"description": "Opening", "start": "2031-05-02 19:00:00"})
		template.append(
			"schedule", {"description": "Hike", "start": "2031-05-03 09:00:00", "end": "2031-05-03 12:00:00"}
		)
		template.save(ignore_permissions=True)

		copied = apply_template(template.name, "2031-06-06")

		self.assertEqual(
			[(row["start"], row["end"]) for row in copied["schedule"]],
			[
				(get_datetime("2031-06-06 19:00:00"), None),
				(get_datetime("2031-06-07 09:00:00"), get_datetime("2031-06-07 12:00:00")),
			],
		)

	def test_apply_template_copies_the_team_needs(self):
		position = make_team_position("_Test Template Greeter", [self.person])
		template = make_function(
			"_Test Team Template", team_needs=[{"position": position.name, "quantity": 3}]
		)

		copied = apply_template(template.name)

		self.assertEqual(
			[(row["position"], row["quantity"]) for row in copied["team_needs"]], [(position.name, 3)]
		)

	def test_as_time_accepts_time_timedelta_and_string(self):
		self.assertEqual(_as_time(time(9, 30)), time(9, 30))
		self.assertEqual(_as_time(timedelta(hours=9, minutes=30)), time(9, 30))
		self.assertEqual(_as_time("09:30:00"), time(9, 30))
