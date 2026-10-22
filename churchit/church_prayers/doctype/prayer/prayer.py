# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe import _
from frappe.model.document import Document

from churchit.church_scope import CHURCH_FIELD, has_church_access


class Prayer(Document):
	def validate(self):
		self.refuse_another_churches_person()

	def before_save(self):
		parts = [self.person or "", str(self.date or "")]
		self.title = " - ".join(p for p in parts if p)

	def refuse_another_churches_person(self):
		"""Only the person's own church records their prayers.

		Frappe checks the create right before `church` is fetched from the person, so it never sees
		whose person this is. Writes with ignore_permissions, such as `create_prayer` for the signed-in
		person, are the app's own and are not refused.
		"""
		if self.flags.ignore_permissions or not self.has_value_changed("person"):
			return
		stored = self.get_doc_before_save() or frappe._dict()
		for person, church in (
			(self.person, frappe.db.get_value("Person", self.person, CHURCH_FIELD)),
			(stored.person, stored.church),
		):
			if not has_church_access(frappe.session.user, church):
				frappe.throw(
					_("{0} belongs to another church, so only that church can record their prayers.").format(
						frappe.db.get_value("Person", person, "full_name") or person
					),
					frappe.PermissionError,
				)
