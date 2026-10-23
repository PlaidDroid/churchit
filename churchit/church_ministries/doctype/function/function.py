# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

from datetime import datetime, time, timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, date_diff, format_datetime, get_datetime, getdate

from churchit.church_ministries.attendance import get_attendance_total
from churchit.utils import link_title

TIME_FORMAT = "h:mm a"


class Function(Document):
	def validate(self):
		# Only the recurrence job links an occurrence, so a user cannot write into another template.
		if (
			self.source_template
			and self.has_value_changed("source_template")
			and not self.flags.ignore_permissions
		):
			frappe.throw(_("Only the recurring functions job sets Source Template."), frappe.PermissionError)

	def before_save(self):
		name = self.function_name or ""
		if self.start_date:
			self.title = f"{name}: {self.start_date}"
		else:
			self.title = name

		self.attendance_total = get_attendance_total(self.attendance or [], self.start_date, self.headcount)

	def on_trash(self):
		"""Keep a deleted occurrence deleted: its template skips the date from now on."""
		if not self.source_template:
			return
		# Its copied bookings go with it, or they would block the delete.
		# church-scope: only the bookings of this occurrence
		for booking in frappe.get_all("Room Booking", filters={"function": self.name}, pluck="name"):
			frappe.delete_doc("Room Booking", booking, ignore_permissions=True)
		if not frappe.db.exists("Function", self.source_template):
			return
		# Locked, so a second delete waits for the first one's skip date instead of a timestamp clash.
		template = frappe.get_doc("Function", self.source_template, for_update=True)
		if template.church != self.church:
			return
		if getdate(self.start_date) in {getdate(row.date) for row in template.skip_dates}:
			return
		template.append("skip_dates", {"date": self.start_date, "reason": _("Occurrence deleted")})
		template.save(ignore_permissions=True)


@frappe.whitelist()
def apply_template(source_name: str, start_date: str | None = None):
	"""The shape of a template function, to start a new one from on `start_date`.

	Templates are shared configuration: a branch may apply the main church's, so
	the source is not measured against the reader's church. It does have to be a
	template, though, or naming any function would hand over its plan and the
	people in it.
	"""
	frappe.has_permission("Function", "read", throw=True)
	refuse_a_source_that_is_not_a_template(source_name)
	template_dict = frappe.get_doc("Function", source_name).as_dict()

	copied_doc = {}

	# Specify which fields to include (whitelist approach)
	include_fields = [
		"address",
		"all_day",
		"description",
		"associated_ministry",
		"min_age",
		"max_age",
		"min_grade",
		"max_grade",
	]

	# Copy normal fields
	for field in include_fields:
		if field in template_dict:
			copied_doc[field] = template_dict[field]

	# Copy child tables
	include_child_tables = ["associations", "attendance", "schedule", "team_needs"]
	for child_table in include_child_tables:
		if template_dict.get(child_table):
			copied_doc[child_table] = [dict(child_row) for child_row in template_dict[child_table]]
	copied_doc["attendance"] = readable_attendance(copied_doc.get("attendance"))

	if start_date:
		move_schedule(copied_doc.get("schedule", []), date_diff(start_date, template_dict.start_date))
	return copied_doc


def move_schedule(rows: list[dict], days: int) -> None:
	"""Move each schedule row's start and end by `days`, so the schedule keeps to its function's date."""
	for row in rows:
		for field in ("start", "end"):
			if row.get(field):
				row[field] = add_days(get_datetime(row[field]), days)


def get_time_window(function) -> tuple[datetime, datetime]:
	"""When *function* starts and ends. An all-day function, or a missing time, takes the whole day."""
	start_date = getdate(function.start_date)
	end_date = getdate(function.end_date or function.start_date)
	if function.all_day or not function.start_time:
		return datetime.combine(start_date, time.min), datetime.combine(end_date, time.max)

	end_time = _as_time(function.end_time) if function.end_time else time.max
	return datetime.combine(start_date, _as_time(function.start_time)), datetime.combine(end_date, end_time)


def get_schedule_rows(function) -> list[frappe._dict]:
	"""*function*'s schedule as (time, title, detail) rows: a linked item's title, else the description."""
	rows = []
	for item in function.schedule:
		title = link_title(item.item_type, item.item) if item.item else None
		rows.append(
			frappe._dict(
				time=format_datetime(item.start, TIME_FORMAT) if item.start else None,
				title=title or item.description,
				detail=(item.description or item.item_type) if title else None,
			)
		)
	return rows


def refuse_a_source_that_is_not_a_template(source_name):
	"""Allow a function the reader may see, or one a Function Type names as its template."""
	if frappe.has_permission("Function", doc=source_name):
		return
	if frappe.db.exists("Function Type", {"template_function": source_name}):
		return
	frappe.throw(_("{0} is not a function template.").format(source_name), frappe.PermissionError)


def readable_attendance(rows):
	"""The attendance rows whose person the reader may see.

	A template carries the regulars of the function it came from, which is the
	point of copying them onto the next one. A template shared by another church
	carries that church's people, who are none of this reader's business.
	"""
	if not rows:
		return []
	people = [row.get("person") for row in rows if row.get("person")]
	if not people:
		return rows
	readable = set(frappe.get_list("Person", filters={"name": ("in", people)}, pluck="name"))
	return [row for row in rows if row.get("person") in readable]


def _as_time(value):
	if hasattr(value, "hour"):
		return value
	# timedelta from DB
	if isinstance(value, timedelta):
		total = int(value.total_seconds())
		return (datetime.min + timedelta(seconds=total)).time()
	# string fallback
	return datetime.strptime(str(value), "%H:%M:%S").time()
