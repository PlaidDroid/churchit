# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe import _
from frappe.model.document import Document

from churchit.church_scope import CHURCH_FIELD, has_church_access


class VisitationLog(Document):
	def validate(self):
		self.refuse_another_churches_person()

	def refuse_another_churches_person(self):
		"""Only the person's own church logs visits to them.

		Frappe checks the create right before `church` is fetched from the person, and the write right on
		the visit as the caller sent it, so it sees neither whose person this is nor whose visit it was.
		Writes with ignore_permissions are the app's own and are not refused.
		"""
		if self.flags.ignore_permissions or not self.has_value_changed("person"):
			return
		before = self.get_doc_before_save()
		if before and not has_church_access(frappe.session.user, before.church):
			frappe.throw(_("This visit belongs to another church."), frappe.PermissionError)
		church = frappe.db.get_value("Person", self.person, CHURCH_FIELD)
		if not has_church_access(frappe.session.user, church):
			frappe.throw(
				_("{0} belongs to another church, so only that church can log visits to them.").format(
					frappe.db.get_value("Person", self.person, "full_name")
				),
				frappe.PermissionError,
			)
