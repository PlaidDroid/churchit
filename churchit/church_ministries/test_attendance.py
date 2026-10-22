# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from churchit.church_ministries.attendance import refresh_started_totals
from churchit.church_ministries.report.church_attendance import church_attendance
from churchit.church_ministries.report.function_attendance_by_person import function_attendance_by_person
from churchit.church_ministries.report.function_attendance_by_type import function_attendance_by_type
from churchit.church_people.report.inactive_members import inactive_members
from churchit.church_people.report.visitor_log import visitor_log
from churchit.dashboard import count_weekly_attendance
from churchit.patches.v1_0 import zero_future_attendance_totals
from churchit.tests.helpers import make_function, make_person


def make_attended_function(function_name, start_date, people, attendance_type):
	function = make_function(function_name, start_date=start_date)
	for person in people:
		function.append("attendance", {"person": person, "attendance_type": attendance_type})
	return function.save(ignore_permissions=True)


class TestAttendance(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.people = [
			make_person(f"_Test Roster {number}", membership="Member", membership_status="Active").name
			for number in (1, 2, 3)
		]

	def test_a_future_roster_counts_nowhere(self):
		before = count_weekly_attendance()["value"]
		function = make_attended_function("_Test Roster Ahead", add_days(today(), 1), self.people, "Assumed")

		self.assertEqual(function.attendance_total, 0)
		self.assertEqual(count_weekly_attendance()["value"], before)
		self.assertNotIn(function.name, {row.name for row in church_attendance.get_data({})})
		self.assertNotIn(function.name, {row.function for row in function_attendance_by_type.get_data({})})
		self.assertEqual(function_attendance_by_person.get_data({"person": self.people[0]}), [])
		inactive = {row.name: row for row in inactive_members.get_data({})}
		self.assertIsNone(inactive[self.people[0]].last_attended)

	def test_the_daily_job_counts_a_function_once_it_starts(self):
		function = make_attended_function(
			"_Test Roster Started", add_days(today(), 1), self.people, "Assumed"
		)
		frappe.db.set_value("Function", function.name, "start_date", add_days(today(), -1))

		refresh_started_totals()

		self.assertEqual(frappe.db.get_value("Function", function.name, "attendance_total"), 3)

	def test_signed_up_and_absent_rows_never_count(self):
		visitors = [make_person(f"_Test Visitor {number}", membership="Visitor").name for number in (1, 2)]
		yesterday = add_days(today(), -1)
		signed_up = make_attended_function("_Test Signed Up", yesterday, visitors[:1], "Signed-Up")
		absent = make_attended_function("_Test Absent", yesterday, visitors[1:], "Absent")

		self.assertEqual((signed_up.attendance_total, absent.attendance_total), (0, 0))
		rows = {row.name: row for row in visitor_log.get_data({})}
		for visitor in visitors:
			self.assertEqual(rows[visitor].visit_count, 0)
			self.assertIsNone(rows[visitor].first_visit)

	def test_the_patch_zeroes_future_totals_and_can_run_again(self):
		future = make_attended_function("_Test Roster Patched", add_days(today(), 1), self.people, "Assumed")
		past = make_attended_function("_Test Roster Kept", today(), self.people, "Assumed")
		frappe.db.set_value("Function", future.name, "attendance_total", 3)

		zero_future_attendance_totals.execute()
		zero_future_attendance_totals.execute()

		self.assertEqual(frappe.db.get_value("Function", future.name, "attendance_total"), 0)
		self.assertEqual(frappe.db.get_value("Function", past.name, "attendance_total"), 3)
