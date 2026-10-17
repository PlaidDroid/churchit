# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe.model.document import Document

from churchit.church_people.derived import refresh_daily


class PeopleSettings(Document):
	def on_update(self):
		if self.has_value_changed("adult_age"):
			frappe.enqueue(refresh_daily, enqueue_after_commit=True)


def store_defaults() -> None:
	"""Save People Settings once on a site that never saved it, so a reader gets its defaults rather than nothing."""
	if not frappe.db.get_singles_dict("People Settings"):
		frappe.get_single("People Settings").save(ignore_permissions=True)
