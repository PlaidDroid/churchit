# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Smart lists: saved Report Builder reports on Person, which Frappe keeps, scopes and refreshes.

A follow-up workflow starts cards from one and a Church Email is sent to one, both through `get_people`.
"""

import json

import frappe
from frappe import _

from churchit.church_people.membership import living_people_filter
from churchit.church_scope import church_filters

SMART_LIST = ("Report Builder", "Person")


def is_smart_list(report: str | None) -> bool:
	"""True when *report* is a saved Report Builder report on Person."""
	if not report:
		return False
	return tuple(frappe.db.get_value("Report", report, ["report_type", "ref_doctype"]) or ()) == SMART_LIST


def get_people(report: str, church: str | None) -> list[str]:
	"""The living people of *church* on the smart list *report*, worked out now."""
	if not is_smart_list(report):
		frappe.throw(
			_("{0} is not a smart list, a saved report of people made with Report Builder.").format(report)
		)
	source = frappe.get_doc("Report", report)
	filters = source.get_standard_report_filters(json.loads(source.json or "{}"), church_filters(church))
	return frappe.get_all("Person", filters=[*filters, living_people_filter()], pluck="name")
