# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

from datetime import date, time, timedelta

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, get_datetime, getdate

from churchit.church_ministries.doctype.function.function import (
	_as_time,
	_compute_next_date,
	_has_ended,
	_month_day,
	apply_template,
	create_scheduled_functions,
)
from churchit.tests.helpers import ensure, make_function, make_person, make_team_position


class TestFunction(FrappeTestCase):
	def setUp(self):
		self.person = make_person("_Test Function Attendee").name

	def _occurrences(self, template):
		return frappe.get_all(
			"Function",
			filters={"source_template": template.name},
			fields=["name", "start_date", "start_time", "auto_repeat"],
			order_by="start_date asc",
		)

	def test_title_includes_start_date(self):
		function = make_function("_Test Titled", start_date="2031-05-01")
		self.assertEqual(function.title, "_Test Titled: 2031-05-01")

	def test_attendance_total_counts_only_present_types(self):
		function = make_function("_Test Counted")
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

	def test_scheduler_creates_next_weekly_occurrence(self):
		template = make_function(
			"_Test Weekly",
			start_date=add_days(getdate(), -10),
			auto_repeat=1,
			repeat_frequency="Weekly",
			repeat_day_of_week="Sunday",
			start_time="10:00:00",
			end_time="11:00:00",
			description="Sunday service",
		)
		create_scheduled_functions()

		occurrences = self._occurrences(template)
		self.assertEqual(len(occurrences), 1)
		occurrence = occurrences[0]
		self.assertGreaterEqual(getdate(occurrence.start_date), getdate())
		self.assertEqual(getdate(occurrence.start_date).weekday(), 6)
		self.assertEqual(_as_time(occurrence.start_time), time(10, 0))
		self.assertFalse(occurrence.auto_repeat)
		self.assertEqual(frappe.db.get_value("Function", occurrence.name, "description"), "Sunday service")

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

	def test_scheduler_moves_the_schedule_to_the_occurrence_date(self):
		start = add_days(getdate(), -10)
		template = make_function(
			"_Test Scheduled Plan",
			start_date=start,
			auto_repeat=1,
			repeat_frequency="Daily",
		)
		template.append("schedule", {"description": "Welcome", "start": f"{start} 10:00:00"})
		template.save(ignore_permissions=True)
		create_scheduled_functions()

		occurrence = frappe.get_doc("Function", {"source_template": template.name})
		self.assertEqual(occurrence.schedule[0].start, get_datetime(f"{occurrence.start_date} 10:00:00"))

	def test_scheduler_keeps_a_multi_day_functions_length(self):
		start = add_days(getdate(), -10)
		template = make_function(
			"_Test Retreat",
			start_date=start,
			end_date=add_days(start, 2),
			auto_repeat=1,
			repeat_frequency="Weekly",
			repeat_day_of_week="Friday",
		)
		create_scheduled_functions()

		occurrence = frappe.db.get_value(
			"Function", {"source_template": template.name}, ["start_date", "end_date"], as_dict=True
		)
		self.assertEqual(getdate(occurrence.end_date), getdate(add_days(occurrence.start_date, 2)))

	def test_scheduler_waits_while_a_future_occurrence_exists(self):
		template = make_function(
			"_Test Queued",
			start_date=add_days(getdate(), -10),
			auto_repeat=1,
			repeat_frequency="Daily",
		)
		create_scheduled_functions()
		create_scheduled_functions()
		self.assertEqual(len(self._occurrences(template)), 1)

	def test_scheduler_respects_repeat_until(self):
		template = make_function(
			"_Test Expired",
			start_date=add_days(getdate(), -10),
			auto_repeat=1,
			repeat_frequency="Daily",
			repeat_until=add_days(getdate(), -1),
		)
		create_scheduled_functions()
		self.assertEqual(self._occurrences(template), [])

	def test_scheduler_skips_templates_that_are_not_repeating(self):
		template = make_function("_Test Once", start_date=add_days(getdate(), -10), auto_repeat=0)
		create_scheduled_functions()
		self.assertEqual(self._occurrences(template), [])


class TestFunctionScheduleMath(FrappeTestCase):
	"""Pure date helpers behind the recurring-function scheduler."""

	def _template(self, **values):
		return frappe._dict({"repeat_day_of_week": None, "repeat_month_day": None, **values})

	def test_daily_advances_past_today(self):
		today = date(2030, 3, 10)
		result = _compute_next_date(self._template(repeat_frequency="Daily"), date(2030, 3, 1), today)
		self.assertEqual(result, today)

	def test_weekly_lands_on_the_requested_weekday(self):
		today = date(2030, 3, 10)  # a Sunday
		template = self._template(repeat_frequency="Weekly", repeat_day_of_week="Wednesday")
		self.assertEqual(_compute_next_date(template, date(2030, 3, 3), today), date(2030, 3, 13))

	def test_weekly_without_a_weekday_returns_none(self):
		template = self._template(repeat_frequency="Weekly")
		self.assertIsNone(_compute_next_date(template, date(2030, 3, 3), date(2030, 3, 10)))

	def test_monthly_uses_the_configured_day_and_clamps_short_months(self):
		template = self._template(repeat_frequency="Monthly", repeat_month_day=31)
		self.assertEqual(_compute_next_date(template, date(2030, 1, 31), date(2030, 2, 1)), date(2030, 2, 28))

	def test_monthly_defaults_to_the_reference_day(self):
		template = self._template(repeat_frequency="Monthly")
		self.assertEqual(
			_compute_next_date(template, date(2030, 1, 15), date(2030, 1, 20)), date(2030, 2, 15)
		)

	def test_yearly_advances_one_year(self):
		template = self._template(repeat_frequency="Yearly")
		self.assertEqual(_compute_next_date(template, date(2030, 6, 1), date(2030, 6, 2)), date(2031, 6, 1))

	def test_unknown_frequency_returns_none(self):
		self.assertIsNone(
			_compute_next_date(
				self._template(repeat_frequency="Fortnightly"), date(2030, 1, 1), date(2030, 1, 1)
			)
		)

	def test_month_day_clamps_to_month_end(self):
		self.assertEqual(_month_day(date(2030, 2, 10), 31), date(2030, 2, 28))
		self.assertEqual(_month_day(date(2030, 4, 10), 15), date(2030, 4, 15))

	def test_as_time_accepts_time_timedelta_and_string(self):
		self.assertEqual(_as_time(time(9, 30)), time(9, 30))
		self.assertEqual(_as_time(timedelta(hours=9, minutes=30)), time(9, 30))
		self.assertEqual(_as_time("09:30:00"), time(9, 30))

	def test_has_ended_uses_end_date_then_end_time(self):
		today = date(2030, 3, 10)
		self.assertTrue(
			_has_ended(frappe._dict(start_date=date(2030, 3, 9), end_date=None, end_time=None), today)
		)
		self.assertFalse(
			_has_ended(frappe._dict(start_date=date(2030, 3, 11), end_date=None, end_time=None), today)
		)
		# Same day with no end time: still running.
		self.assertFalse(_has_ended(frappe._dict(start_date=today, end_date=None, end_time=None), today))
