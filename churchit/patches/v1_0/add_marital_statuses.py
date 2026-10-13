# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Give existing sites the Marital Statuses, and make everyone ticked Is Married Married.

People left unticked keep no status, because the box cannot tell a single adult from a child.
"""

import frappe
from frappe.query_builder.functions import Coalesce

from churchit.church_people.doctype.marital_status.marital_status import DEFAULT_STATUSES, MARRIED


def execute():
	for status, description in DEFAULT_STATUSES.items():
		if not frappe.db.exists("Marital Status", status):
			frappe.get_doc(
				{"doctype": "Marital Status", "status": status, "description": description}
			).insert(ignore_permissions=True)

	if not frappe.db.has_column("Person", "is_married"):
		return
	Person = frappe.qb.DocType("Person")
	frappe.qb.update(Person).set(Person.marital_status, MARRIED).where(
		(Person.is_married == 1) & (Coalesce(Person.marital_status, "") == "")
	).run()
