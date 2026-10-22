# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe import _
from frappe.model.document import Document

from churchit.church_people.member_access import pin_to_members_own_person, validate_members_recipient
from churchit.church_scope import CHURCH_FIELD, has_church_access
from churchit.utils import resolve_link_titles


class AlmsRequest(Document):
	def validate(self):
		# Before the pin: `church` came from the requestor as sent, and the pin does not change it.
		self.refuse_another_churches_person()
		pin_to_members_own_person(self, "requestor")
		validate_members_recipient(self)

	def refuse_another_churches_person(self):
		"""Only the requestor's own church records their alms requests.

		Frappe checks the create right before `church` is fetched from the requestor, so it never sees
		whose person this is. Writes with ignore_permissions, such as the portal's web form, are the
		app's own and are not refused.
		"""
		if self.flags.ignore_permissions or not self.has_value_changed("requestor"):
			return
		stored = self.get_doc_before_save() or frappe._dict()
		for person, church in (
			(self.requestor, frappe.db.get_value("Person", self.requestor, CHURCH_FIELD)),
			(stored.requestor, stored.church),
		):
			if not has_church_access(frappe.session.user, church):
				frappe.throw(
					_(
						"{0} belongs to another church, so only that church can record their alms requests."
					).format(frappe.db.get_value("Person", person, "full_name") or person),
					frappe.PermissionError,
				)

	def before_save(self):
		# Set the alms request title
		recipient_label = self.recipient or ""
		if self.recipient and self.recipient_type:
			meta = frappe.get_meta(self.recipient_type)
			if meta.title_field:
				recipient_label = (
					frappe.db.get_value(self.recipient_type, self.recipient, meta.title_field)
					or self.recipient
				)
		parts = [recipient_label, str(self.amount or ""), self.status or ""]
		self.title = " - ".join(p for p in parts if p)


@frappe.whitelist(methods=["POST"])
def create_expense(alms_request_name: str):
	"""Create a Expense from the given Alms Request."""
	alms = frappe.get_doc("Alms Request", alms_request_name)
	alms.check_permission("read")
	# Make sure an expense type and amount are provided
	if not alms.amount:
		frappe.throw(_("⚠️ An amount is required for an expense to be created."))
	if not alms.expense_type:
		frappe.throw(_("⚠️ An expense type is required for an expense to be created."))
	expense = frappe.new_doc("Expense")
	expense.title = f"Alms: {alms.title}"
	expense.amount = alms.amount
	expense.type = alms.expense_type
	expense.date = frappe.utils.now()
	expense.insert()
	expense.submit()
	frappe.db.set_value("Alms Request", alms_request_name, "associated_expense", expense.name)


def get_list_context(context):
	context.filters = {"owner": frappe.session.user}
	context.order_by = "modified desc"

	def get_list(doctype, txt, filters, limit_start, limit_page_length=20, **kwargs):
		from frappe.www.list import get_list as default_get_list

		rows = default_get_list(doctype, txt, filters, limit_start, limit_page_length, **kwargs)
		resolve_link_titles(rows, doctype)
		return rows

	context.get_list = get_list
	return context
