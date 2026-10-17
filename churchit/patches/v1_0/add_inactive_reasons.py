# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Give existing sites the Inactive Reasons: the two the app sets, and each reason already typed on a Person.

Inactive Reason on Person was free text and is now a link, so every value it holds needs a
record. Spaces typed around a value are trimmed first, so the value matches its record.
"""

import frappe
from frappe.query_builder.functions import Trim

from churchit.church_people.doctype.inactive_reason.inactive_reason import create_inactive_reasons


def execute():
	Person = frappe.qb.DocType("Person")
	frappe.qb.update(Person).set(Person.inactive_reason, Trim(Person.inactive_reason)).where(
		Person.inactive_reason != Trim(Person.inactive_reason)
	).run()
	create_inactive_reasons()
