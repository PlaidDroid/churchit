# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from churchit.church_scope import CHURCH_FIELD, has_church_access

EMAIL = "Email"
SMS = "SMS"
PHONE = "Phone"
POST = "Post"

GIVEN = "Given"
WITHDRAWN = "Withdrawn"

DESK = "Desk"
WEB_FORM = "Web Form"
MEMBER_PORTAL = "Member Portal"
UNSUBSCRIBE_LINK = "Unsubscribe Link"


class CommunicationConsent(Document):
	"""A person's yes or no to one channel. Its fields are set once; a change of mind is a new record."""

	def validate(self):
		self.refuse_another_churches_person()

	def refuse_another_churches_person(self):
		"""Only the person's own church records their consent.

		Frappe checks the create right before `church` is fetched from the person,
		so it never sees whose person this is. Writes with ignore_permissions, such
		as an unsubscribe link, are the app's own and are not refused.
		"""
		if self.flags.ignore_permissions:
			return
		church = frappe.db.get_value("Person", self.person, CHURCH_FIELD)
		if not has_church_access(frappe.session.user, church):
			frappe.throw(
				_("{0} belongs to another church, so only that church can record their consent.").format(
					self.person_name or self.person
				),
				frappe.PermissionError,
			)


def record(
	people: list[str],
	channel: str,
	status: str,
	purpose: str | None = None,
	method: str = DESK,
	recorded_on=None,
) -> list[str]:
	"""Add a consent for each of *people* and return their names."""
	return [
		frappe.get_doc(
			{
				"doctype": "Communication Consent",
				"person": person,
				"channel": channel,
				"purpose": purpose,
				"status": status,
				"method": method,
				"recorded_on": recorded_on or now_datetime(),
			}
		)
		.insert(ignore_permissions=True)
		.name
		for person in people
	]
