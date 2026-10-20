# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe.model.document import Document

GENERAL = "General"
PASTORAL_CARE = "Pastoral Care"
PASTORAL_ROLES = ("Church Pastoral Care", "Church Manager")


class NoteCategory(Document):
	"""A kind of note about a person, with the roles that read and add it. A blank list leaves it to everyone."""

	def can_read(self, user: str, author: str) -> bool:
		"""Whether *user* may read a note in this category written by *author*."""
		if self.is_private:
			return user == author
		return has_listed_role(user, self.view_roles)

	def can_add(self, user: str) -> bool:
		"""Whether *user* may add a note in this category."""
		return has_listed_role(user, self.create_roles)


def has_listed_role(user: str, rows: list) -> bool:
	"""Whether *user* holds one of the roles in *rows*; an empty list admits everyone."""
	roles = {row.role for row in rows}
	return not roles or bool(roles.intersection(frappe.get_roles(user)))


def create_note_categories():
	"""Make General, which everyone reads, and Pastoral Care, which only the care team and managers read."""
	pastoral_roles = [{"role": role} for role in PASTORAL_ROLES]
	categories = (
		{"category_name": GENERAL, "description": "Notes everyone on staff can read and add."},
		{
			"category_name": PASTORAL_CARE,
			"description": "Care and counseling notes. Only the care team and Church Managers can read them.",
			"view_roles": pastoral_roles,
			"create_roles": pastoral_roles,
		},
	)
	for category in categories:
		if not frappe.db.exists("Note Category", category["category_name"]):
			frappe.get_doc({"doctype": "Note Category", **category}).insert(ignore_permissions=True)
