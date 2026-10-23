# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import secrets

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from churchit.church_ministries.attendance import record, remove
from churchit.church_scope import default_church, refuse_another_churches_parent

CHECKED_IN = "Checked-In"

# Letters and digits that are hard to confuse on a printed tag.
SECURITY_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class FunctionCheckIn(Document):
	def validate(self):
		refuse_another_churches_parent(
			self,
			"function",
			_("{0} belongs to another church, so only that church can check people in to it."),
		)
		# Prevent duplicate check-ins for the same function and person
		if frappe.db.exists(
			"Function Check-In", {"function": self.function, "person": self.person, "name": ("!=", self.name)}
		):
			frappe.throw(_("This person has already been checked in to this function."))

	def before_save(self):
		function_label = (
			frappe.db.get_value("Function", self.function, "function_name") if self.function else ""
		)
		person_label = frappe.db.get_value("Person", self.person, "full_name") if self.person else ""
		parts = [function_label or "", person_label or ""]
		self.title = " - ".join(p for p in parts if p)

		record(self.person, self.function, CHECKED_IN)

	def on_trash(self):
		if remove(self.person, self.function, CHECKED_IN):
			frappe.msgprint(_("The associated attendance record has been removed."), alert=True)


def is_shared_function(function_name):
	"""True when the function is shared with every church."""
	return bool(cint(frappe.db.get_value("Function", function_name, "is_shared")))


def new_security_code():
	return "".join(secrets.choice(SECURITY_CODE_ALPHABET) for _ in range(4))


@frappe.whitelist(methods=["POST"])
def check_in_persons(function_name: str, persons: str | list[str]):
	"""Check *persons* in to a function. New check-ins made in one call share a security code."""
	frappe.has_permission("Function Check-In", "create", throw=True)
	# The inserts below ignore permissions, so the function and every person are checked here.
	frappe.has_permission("Function", doc=function_name, throw=True)
	persons = frappe.parse_json(persons)
	# A shared function is a joint service: any church's people may come.
	if not is_shared_function(function_name):
		for person in persons:
			frappe.has_permission("Person", doc=person, throw=True)

	code = None
	if frappe.db.get_single_value("Check-In Settings", "security_codes"):
		code = new_security_code()

	names = []
	for person in persons:
		existing = frappe.db.get_value(
			"Function Check-In", {"function": function_name, "person": person}, "name"
		)
		if existing:
			record(person, function_name, CHECKED_IN)
			names.append(existing)
			continue
		check_in = frappe.get_doc(
			{
				"doctype": "Function Check-In",
				"function": function_name,
				"person": person,
				"security_code": code,
			}
		).insert(ignore_permissions=True)
		names.append(check_in.name)
	return names


@frappe.whitelist(methods=["POST"])
def print_name_tags(check_ins: str | list[str] | None = None, persons: str | list[str] | None = None):
	"""Name tags for saved check-ins, or plain tags for people who are not checked in."""
	docs = [frappe.get_doc("Function Check-In", name) for name in frappe.parse_json(check_ins) or []]
	for doc in docs:
		doc.check_permission("read")
	for person in frappe.parse_json(persons) or []:
		frappe.has_permission("Person", doc=person, throw=True)
		docs.append(frappe.get_doc({"doctype": "Function Check-In", "person": person}))
	if not docs:
		frappe.throw(_("Nothing to print."))
	# The operator prints at their own campus, so their church's printer is the one.
	settings = frappe.get_single("Check-In Settings").for_church(default_church())
	return settings.print_name_tags(docs)
