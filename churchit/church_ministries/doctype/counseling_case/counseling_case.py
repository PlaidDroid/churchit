# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe import _
from frappe.model.document import Document

from churchit.church_scope import CHURCH_FIELD, has_church_access


class CounselingCase(Document):
	def validate(self):
		self.refuse_another_churches_person()

	def refuse_another_churches_person(self):
		"""Only the person's own church opens a case for them, and only the case's church gives it to another.

		Frappe checks the user's rights before `church` is fetched from the person, on the church the
		caller sent, so it sees neither whose person this is nor where the case was. Writes with
		ignore_permissions are the app's own and are not refused.
		"""
		if self.flags.ignore_permissions or not self.has_value_changed("person"):
			return
		before = self.get_doc_before_save()
		churches = (frappe.db.get_value("Person", self.person, CHURCH_FIELD), before and before.church)
		if not all(has_church_access(frappe.session.user, church) for church in churches):
			frappe.throw(
				_(
					"{0} belongs to another church, so only that church can open a counseling case for them."
				).format(frappe.db.get_value("Person", self.person, "full_name") or self.person),
				frappe.PermissionError,
			)
