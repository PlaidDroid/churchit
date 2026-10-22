# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe import _
from frappe.model.document import Document

from churchit.church_scope import CHURCH_FIELD, has_church_access


class PersonNote(Document):
	"""A note about a person. Its Note Category decides who reads and adds it (church_people/notes.py)."""

	def validate(self):
		self.refuse_changes_to_a_note_the_user_cannot_change()
		self.refuse_another_churches_person()

	def refuse_changes_to_a_note_the_user_cannot_change(self):
		"""A change needs the write right on the note as stored.

		Frappe checks the write right on the note as sent, and a REST update returns the saved note.
		Re-filing a note into a category the user may write would otherwise pass that check and hand
		them the text. Frappe itself refuses a new author, since `owner` is set only once.
		"""
		before = self.get_doc_before_save()
		if before and not self.flags.ignore_permissions and not before.has_permission("write"):
			frappe.throw(_("You cannot change this note."), frappe.PermissionError)

	def refuse_another_churches_person(self):
		"""Only the person's own church writes notes about them.

		Frappe checks the create right before `church` is fetched from the person,
		so it never sees whose person this is. Writes with ignore_permissions are
		the app's own and are not refused.
		"""
		if self.flags.ignore_permissions or not self.has_value_changed("person"):
			return
		church = frappe.db.get_value("Person", self.person, CHURCH_FIELD)
		if not has_church_access(frappe.session.user, church):
			frappe.throw(
				_("{0} belongs to another church, so only that church can add notes about them.").format(
					self.person_name or self.person
				),
				frappe.PermissionError,
			)
