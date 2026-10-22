# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Which Function Attendance rows count as attendance.

A row counts when its type says the person was there and its Function has started. A roster made
ahead of a Function, or a Signed-Up or Absent row, counts in no total, report, card or summary. Every
reader of attendance takes `COUNTED_TYPES` from here and filters `Function.start_date` up to today.
"""

import frappe
from frappe.utils import add_days, getdate, today

# Function Attendance Type is named by its type, so these are also the record names.
COUNTED_TYPES = ("Confirmed", "Assumed", "Checked-In")
REFRESH_DAYS = 7


def get_total(rows, start_date) -> int:
	"""The head count of a Function's attendance rows: 0 until the Function starts."""
	if getdate(start_date) > getdate():
		return 0
	return sum(1 for row in rows if row.attendance_type in COUNTED_TYPES)


def refresh_started_totals():
	"""Daily job: recount the Functions of the last week, so a Function counts its roster once it starts."""
	# church-scope: a job over every church; each total counts only its own Function's rows
	functions = frappe.get_all(
		"Function",
		filters={"start_date": ("between", [add_days(today(), -REFRESH_DAYS), today()])},
		pluck="name",
	)
	for name in functions:
		function = frappe.get_doc("Function", name)
		total = get_total(function.attendance, function.start_date)
		if total != function.attendance_total:
			function.db_set("attendance_total", total, update_modified=False)
