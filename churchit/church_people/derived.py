# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Person fields worked out from other fields, never typed in.

`sync_derived` sets them on every save. `refresh_daily` keeps the ones that change with the date current between saves.
"""

import frappe
from dateutil.relativedelta import relativedelta
from frappe.utils import getdate, nowdate


def years_since(date):
	"""Whole years from ``date`` until today; 0 when ``date`` is empty."""
	if not date:
		return 0
	return max(relativedelta(getdate(nowdate()), getdate(date)).years, 0)


def sync_derived(person):
	"""Set *person*'s age from their first dated Birth event, cleared without one, and their years married."""
	birth = next((row for row in person.life_events or [] if row.event_type == "Birth" and row.date), None)
	person.age = years_since(birth.date) if birth else None
	person.marriage_years = years_since(person.anniversary)


def refresh_daily():
	"""Daily scheduler: bring each stored age and years married up to today.

	It compares every person rather than only today's birthdays, so a day the scheduler missed is caught up.
	"""
	# church-scope: scheduled daily job: recomputes stored figures, shows nobody anything
	births = frappe.get_all(
		"Life Event",
		filters={"parenttype": "Person", "event_type": "Birth", "date": ("is", "set")},
		fields=["parent", "date"],
		order_by="idx asc",
	)
	birth_dates = {}
	for birth in births:
		birth_dates.setdefault(birth.parent, birth.date)

	for person in frappe.get_all("Person", fields=["name", "age", "anniversary", "marriage_years"]):
		current = {
			"age": years_since(birth_dates.get(person.name)),
			"marriage_years": years_since(person.anniversary),
		}
		changed = {field: value for field, value in current.items() if (person[field] or 0) != value}
		if changed:
			frappe.db.set_value("Person", person.name, changed, update_modified=False)
