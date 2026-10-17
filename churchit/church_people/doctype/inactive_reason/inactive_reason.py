# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe import _
from frappe.model.document import Document

# Set by a Death life event and by a confirmed Member Transfer out.
DECEASED = "Deceased"
TRANSFERRED = "Transferred to another church"
SET_AUTOMATICALLY = (DECEASED, TRANSFERRED)


class InactiveReason(Document):
	"""Why a person is no longer active. The reasons the app sets cannot be deleted or renamed."""

	def on_trash(self):
		if self.name in SET_AUTOMATICALLY:
			frappe.throw(
				_("{0} is set automatically, so it cannot be deleted.").format(frappe.bold(self.name))
			)

	def before_rename(self, old, new, merge=False):
		if old in SET_AUTOMATICALLY:
			frappe.throw(_("{0} is set automatically, so it cannot be renamed.").format(frappe.bold(old)))


def create_inactive_reasons():
	"""Make the reasons the app sets, and one for each reason already typed on a Person, so every stored value links."""
	# church-scope: every church's stored reasons become the one site-wide list of reasons
	stored = frappe.get_all(
		"Person",
		filters={"inactive_reason": ("is", "set")},
		pluck="inactive_reason",
		distinct=True,
		order_by="inactive_reason asc",
	)
	for reason in (*SET_AUTOMATICALLY, *stored):
		if not frappe.db.exists("Inactive Reason", reason):
			frappe.get_doc({"doctype": "Inactive Reason", "reason": reason}).insert(ignore_permissions=True)
