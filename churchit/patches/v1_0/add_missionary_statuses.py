# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Give existing sites the Missionary Statuses, and every missionary a Status.

A missionary whose support ended before today becomes Inactive, and every other
one Active. A status the church already made under the same name is left alone.
"""

import frappe
from frappe.utils import nowdate

from churchit.church_missions.doctype.missionary_status.missionary_status import (
	ACTIVE,
	DEFAULT_STATUSES,
	INACTIVE,
)


def execute():
	for status, description in DEFAULT_STATUSES.items():
		if not frappe.db.exists("Missionary Status", status):
			frappe.get_doc(
				{"doctype": "Missionary Status", "status": status, "description": description}
			).insert(ignore_permissions=True)

	Missionary = frappe.qb.DocType("Missionary")
	has_no_status = Missionary.status.isnull() | (Missionary.status == "")
	has_ended = Missionary.support_end_date.notnull() & (Missionary.support_end_date < nowdate())
	frappe.qb.update(Missionary).set(Missionary.status, INACTIVE).where(has_no_status & has_ended).run()
	frappe.qb.update(Missionary).set(Missionary.status, ACTIVE).where(has_no_status).run()
