# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""A portal member's own ministry team assignments, blocks and calendar feed, and answers sent from a request email."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.query_builder.functions import Coalesce
from frappe.rate_limiter import rate_limit
from frappe.utils import (
	add_days,
	format_time,
	get_host_name,
	get_system_timezone,
	get_url,
	getdate,
	now_datetime,
	nowdate,
	sbool,
)

from churchit.church_ministries.doctype.function.function import TIME_FORMAT, get_time_window
from churchit.church_ministries.doctype.ministry_team_assignment.ministry_team_assignment import ACCEPTED
from churchit.church_scope import session_person

# A calendar app drops an event the feed stops listing, so the feed keeps recent ones.
CALENDAR_HISTORY_DAYS = 90


def get_own_assignments(person: str, from_date: str | None = None) -> list[frappe._dict]:
	"""*person*'s assignments at functions that end on *from_date* (today by default) or later.

	Soonest first, each with its time and has_started.
	"""
	Assignment = frappe.qb.DocType("Ministry Team Assignment")
	Function = frappe.qb.DocType("Function")
	Position = frappe.qb.DocType("Ministry Team Position")
	# church-scope: the signed-in member's own assignments, by their person
	rows = (
		frappe.qb.from_(Assignment)
		.join(Function)
		.on(Assignment.function == Function.name)
		.join(Position)
		.on(Assignment.position == Position.name)
		.select(
			Assignment.name,
			Assignment.status,
			Assignment.decline_reason,
			Assignment.function,
			Function.function_name,
			Function.start_date,
			Function.start_time,
			Function.end_date,
			Function.end_time,
			Function.all_day,
			Position.title.as_("position"),
		)
		.where(
			(Assignment.person == person)
			& (Coalesce(Function.end_date, Function.start_date) >= (from_date or nowdate()))
		)
		.orderby(Function.start_date)
		.orderby(Function.start_time)
		.run(as_dict=True)
	)
	for row in rows:
		row.time = format_time(row.start_time, TIME_FORMAT) if row.start_time and not row.all_day else ""
		row.has_started = get_time_window(row)[0] <= now_datetime()
	return rows


def get_own_blocks(person: str) -> list[frappe._dict]:
	"""*person*'s blocks that have not ended yet."""
	# church-scope: the signed-in member's own blocks, by their person
	return frappe.get_all(
		"Ministry Team Block",
		filters={"person": person, "to_date": (">=", nowdate())},
		fields=["name", "from_date", "to_date", "reason", "whole_household"],
		order_by="from_date asc",
	)


def get_calendar_links(person: str) -> frappe._dict | None:
	"""*person*'s calendar feed link, as https to copy and as webcal to open a calendar app."""
	key = frappe.db.get_value("Person", person, "team_calendar_key")
	if not key:
		return None
	url = get_url(f"/api/method/churchit.church_ministries.member_ministry_team.get_team_calendar?key={key}")
	return frappe._dict(url=url, webcal="webcal://" + url.partition("://")[2])


def get_calendar(person: str) -> str:
	"""*person*'s accepted assignments as an iCalendar feed."""
	host = get_host_name()
	stamp = as_calendar_time(now_datetime())
	lines = [
		"BEGIN:VCALENDAR",
		"VERSION:2.0",
		"PRODID:-//Churchit//Ministry Team//EN",
		"METHOD:PUBLISH",
		f"X-WR-CALNAME:{calendar_text(_('Ministry Team'))}",
	]
	for row in get_own_assignments(person, add_days(nowdate(), -CALENDAR_HISTORY_DAYS)):
		if row.status != ACCEPTED:
			continue
		summary = _("{0} at {1}").format(row.position, row.function_name)
		lines += [
			"BEGIN:VEVENT",
			f"UID:{row.name}@{host}",
			f"DTSTAMP:{stamp}",
			*calendar_times(row),
			f"SUMMARY:{calendar_text(summary)}",
			"END:VEVENT",
		]
	lines.append("END:VCALENDAR")
	return "\r\n".join(lines) + "\r\n"


def calendar_times(function) -> list[str]:
	"""DTSTART and DTEND of *function*: whole days when it has no start time, else UTC times."""
	start, end = get_time_window(function)
	if function.all_day or not function.start_time:
		# The end of an all-day event is the day after its last day.
		return [f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end + timedelta(days=1):%Y%m%d}"]
	return [f"DTSTART:{as_calendar_time(start)}", f"DTEND:{as_calendar_time(end)}"]


def as_calendar_time(value: datetime) -> str:
	"""*value*, a time in the site's time zone, as an iCalendar UTC time."""
	local = value.replace(tzinfo=ZoneInfo(get_system_timezone()))
	return local.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def calendar_text(value: str) -> str:
	"""*value* escaped for an iCalendar text property."""
	return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def get_assignment_by_key(key: str):
	"""The assignment a request email's link answers, or a refusal for any other key."""
	name = key and frappe.db.get_value("Ministry Team Assignment", {"response_key": key})
	if not name:
		frappe.throw(
			_("This link is not valid. Ask the church office for a new one."), frappe.PermissionError
		)
	return frappe.get_doc("Ministry Team Assignment", name)


def answer(assignment, status: str, reason: str | None):
	assignment.respond(status, reason)
	# Members cannot write an assignment. Each caller has checked whose answer this is.
	assignment.save(ignore_permissions=True)


@frappe.whitelist()
def answer_own_assignment(assignment: str, status: str, reason: str | None = None):
	"""Accept or decline one of the signed-in member's own assignments."""
	doc = frappe.get_doc("Ministry Team Assignment", assignment)
	if not session_person() or doc.person != session_person():
		frappe.throw(_("This assignment is not yours to answer."), frappe.PermissionError)
	answer(doc, status, reason)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=60 * 60)
def answer_by_key(key: str, status: str, reason: str | None = None):
	"""Accept or decline the assignment a request email's link points at, signed in or not."""
	answer(get_assignment_by_key(key), status, reason)


@frappe.whitelist(allow_guest=True, methods=["GET"])
@rate_limit(limit=300, seconds=60 * 60)
def get_team_calendar(key: str):
	"""The calendar feed a member's calendar link points at, for any calendar app that holds the link."""
	person = key and frappe.db.get_value("Person", {"team_calendar_key": key})
	if not person:
		frappe.throw(
			_("This calendar link is not valid. Make a new one on the Ministry Team page."),
			frappe.PermissionError,
		)

	frappe.response.type = "download"
	frappe.response.filename = "ministry-team.ics"
	frappe.response.filecontent = get_calendar(person)
	frappe.response.content_type = "text/calendar"
	frappe.response.display_content_as = "inline"


@frappe.whitelist(methods=["POST"])
def make_own_calendar_key():
	"""Give the signed-in member a new calendar link. Their old link stops working."""
	person = session_person()
	if not person:
		frappe.throw(_("Your account is not linked to a member record."), frappe.PermissionError)
	frappe.db.set_value(
		"Person", person, "team_calendar_key", frappe.generate_hash(length=32), update_modified=False
	)


@frappe.whitelist()
def add_own_block(from_date: str, to_date: str, reason: str | None = None, whole_household: str | int = 0):
	"""Block the signed-in member from serving from *from_date* to *to_date*."""
	person = session_person()
	if not person:
		frappe.throw(_("Your account is not linked to a member record."), frappe.PermissionError)
	if getdate(to_date) < getdate():
		frappe.throw(_("Choose dates from today on."))

	frappe.get_doc(
		{
			"doctype": "Ministry Team Block",
			"person": person,
			"from_date": from_date,
			"to_date": to_date,
			"reason": reason,
			"whole_household": sbool(whole_household),
		}
	).insert(ignore_permissions=True)


@frappe.whitelist()
def remove_own_block(block: str):
	"""Remove one of the signed-in member's own blocks."""
	person = frappe.db.get_value("Ministry Team Block", block, "person")
	if not person or person != session_person():
		frappe.throw(_("This block is not yours to remove."), frappe.PermissionError)
	frappe.delete_doc("Ministry Team Block", block, ignore_permissions=True)
