# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""A group's meetings: Functions with the group in their Group field."""

import frappe
from frappe import _
from frappe.utils import cint, format_date, format_time, getdate

from churchit.church_ministries.doctype.function.function import TIME_FORMAT

REPEAT_UNITS = {"Daily": "days", "Weekly": "weeks", "Monthly": "months", "Yearly": "years"}


def get_meets(group: str | None) -> str:
	"""When and where *group* meets, from its recurring Function, for the Group's Meets field."""
	if not group:
		return ""
	# church-scope: the schedule of one group, whose Functions take the group's church
	schedules = frappe.get_all(
		"Function",
		filters={"group": group, "auto_repeat": 1},
		fields=[
			"start_date",
			"start_time",
			"all_day",
			"address",
			"repeat_until",
			"repeat_frequency",
			"repeat_interval",
			"repeat_day_of_week",
			"repeat_monthly_by",
			"repeat_week_of_month",
			"repeat_month_day",
		],
		order_by="creation asc",
	)
	today = getdate()
	return "; ".join(
		describe_schedule(schedule)
		for schedule in schedules
		if not schedule.repeat_until or getdate(schedule.repeat_until) >= today
	)


def describe_schedule(schedule) -> str:
	"""One line such as "Weekly on Tuesday at 7:00 PM, Main Hall"."""
	interval = cint(schedule.repeat_interval) or 1
	text = _(schedule.repeat_frequency)
	if interval > 1:
		text = _("Every {0} {1}").format(interval, _(REPEAT_UNITS[schedule.repeat_frequency]))
	day = get_repeat_day(schedule)
	if day:
		text = _("{0} on {1}").format(text, day)
	if schedule.start_time and not schedule.all_day:
		text = _("{0} at {1}").format(text, format_time(schedule.start_time, TIME_FORMAT))
	place = schedule.address and frappe.db.get_value("Address", schedule.address, "address_title")
	return f"{text}, {place}" if place else text


def get_repeat_day(schedule) -> str | None:
	"""The day of the week, month or year a schedule repeats on."""
	start = getdate(schedule.start_date)
	if schedule.repeat_frequency == "Weekly":
		return _(schedule.repeat_day_of_week or start.strftime("%A"))
	if schedule.repeat_frequency == "Monthly" and schedule.repeat_monthly_by == "Weekday":
		return _("the {0} {1}").format(_(schedule.repeat_week_of_month), _(schedule.repeat_day_of_week))
	if schedule.repeat_frequency == "Monthly":
		return _("day {0}").format(cint(schedule.repeat_month_day) or start.day)
	if schedule.repeat_frequency == "Yearly":
		return format_date(start, "MMMM d")
	return None
