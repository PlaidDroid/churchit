# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Move the schedule rows of recurring Functions onto their own dates.

Each Function made from a template used to keep the template's dates on its schedule rows. A row
that still starts when one of its template's rows starts was never edited, so it moves by the days
between the template and the copy. A row someone changed is left alone.
"""

import frappe
from frappe.utils import date_diff

from churchit.church_ministries.doctype.function.function import move_schedule


def execute():
	copies = frappe.get_all(
		"Function",
		filters={"source_template": ("is", "set")},
		fields=["name", "start_date", "source_template"],
	)
	for copy in copies:
		template_start_date = frappe.db.get_value("Function", copy.source_template, "start_date")
		if template_start_date:
			move_unedited_rows(copy, copy.source_template, date_diff(copy.start_date, template_start_date))


def move_unedited_rows(copy, template: str, days: int) -> None:
	template_starts = frappe.get_all(
		"Function Schedule", filters={"parent": template, "start": ("is", "set")}, pluck="start"
	)
	if not days or not template_starts:
		return

	rows = frappe.get_all(
		"Function Schedule",
		filters={"parent": copy.name, "start": ("in", template_starts)},
		fields=["name", "start", "end"],
	)
	move_schedule(rows, days)
	for row in rows:
		frappe.db.set_value(
			"Function Schedule", row.name, {"start": row.start, "end": row.end}, update_modified=False
		)
