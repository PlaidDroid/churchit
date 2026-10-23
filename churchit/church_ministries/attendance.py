# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Function Attendance rows: who writes them, and which count as attendance.

A row counts when its type says the person was there and its Function has started. A roster made
ahead of a Function, or a Signed-Up or Absent row, counts in no total, report, card or summary. Every
reader of attendance takes `COUNTED_TYPES` from here and filters `Function.start_date` up to today.

`record` and `remove` are the only writers of a person's row. Each loads the Function under its row
lock and saves it whole, so two check-ins at once lose no row, and a desk form opened before the change
fails as stale instead of dropping it. The save refreshes the person's summaries through `doc_events`.
"""

import frappe
from frappe.utils import add_days, getdate, today

# Function Attendance Type is named by its type, so these are also the record names.
COUNTED_TYPES = ("Confirmed", "Assumed", "Checked-In")
REFRESH_DAYS = 7
# A new type replaces a person's type only at the same or a higher rank.
STATUS_RANK = {"Signed-Up": 0, "Assumed": 1, "Confirmed": 2, "Absent": 2, "Checked-In": 3}
OTHER_RANK = 2


def get_total(rows, start_date) -> int:
	"""The head count of a Function's attendance rows: 0 until the Function starts."""
	if getdate(start_date) > getdate():
		return 0
	return sum(1 for row in rows if row.attendance_type in COUNTED_TYPES)


def record(person: str, function: str, status: str) -> bool:
	"""Give *person* the type *status* on *function*, unless their row says more. False when nothing changes."""
	function_doc = frappe.get_doc("Function", function, for_update=True)
	row = next((row for row in function_doc.attendance if row.person == person), None)
	rank = STATUS_RANK.get(status, OTHER_RANK)
	if not row:
		function_doc.append("attendance", {"person": person, "attendance_type": status})
	elif row.attendance_type == status or rank < STATUS_RANK.get(row.attendance_type, OTHER_RANK):
		return False
	else:
		row.attendance_type = status

	function_doc.save(ignore_permissions=True)
	return True


def remove(person: str, function: str, status: str) -> bool:
	"""Remove *person*'s row from *function* when its type is *status*. False when there is none."""
	function_doc = frappe.get_doc("Function", function, for_update=True)
	row = next(
		(row for row in function_doc.attendance if (row.person, row.attendance_type) == (person, status)),
		None,
	)
	if not row:
		return False

	function_doc.remove(row)
	function_doc.save(ignore_permissions=True)
	return True


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
