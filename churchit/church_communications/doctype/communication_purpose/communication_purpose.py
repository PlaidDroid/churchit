# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe import _
from frappe.model.document import Document

# What a Church Email asks consent for, and what its unsubscribe link withdraws.
CHURCH_NEWS = "Church News"
PASTORAL_CARE = "Pastoral Care"
USED_BY_THE_APP = (CHURCH_NEWS,)

STARTER_PURPOSES = {
	CHURCH_NEWS: "Church Emails: news, events and announcements sent to the congregation.",
	PASTORAL_CARE: "Messages about a person's care, such as visits, prayer and support.",
}


class CommunicationPurpose(Document):
	"""A reason the church contacts people. The purposes the app uses cannot be deleted."""

	def on_trash(self):
		if self.name in USED_BY_THE_APP:
			frappe.throw(
				_("{0} is used by Church Emails, so it cannot be deleted.").format(frappe.bold(self.name))
			)


def create_communication_purposes():
	"""Make the starter purposes that are missing. Safe to run again."""
	for purpose, description in STARTER_PURPOSES.items():
		if not frappe.db.exists("Communication Purpose", purpose):
			frappe.get_doc(
				{"doctype": "Communication Purpose", "purpose": purpose, "description": description}
			).insert(ignore_permissions=True)
