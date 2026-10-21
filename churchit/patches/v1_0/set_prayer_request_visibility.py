# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Move Prayer Request's Is Private tick to Who Can See It: Private when ticked, else Whole Church.

Runs post_model_sync. Adding the visibility column gave every request its default, Whole Church, and migrate
keeps the old column after the field goes. The tick is cleared as it moves, so a second run changes nothing.
"""

import frappe

from churchit.church_prayers.prayer_access import PRAYER_REQUEST, PRIVATE


def execute():
	if not frappe.db.has_column(PRAYER_REQUEST, "is_private"):
		return
	Request = frappe.qb.DocType(PRAYER_REQUEST)
	(
		frappe.qb.update(Request)
		.set(Request.visibility, PRIVATE)
		.set(Request.is_private, 0)
		.where(Request.is_private == 1)
		.run()
	)
