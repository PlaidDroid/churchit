# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_years, getdate, nowdate

from churchit.church_scope import CHURCH_FIELD, has_church_access

RESULT_STATUSES = ("Cleared", "Not Cleared", "Expired")


class BackgroundCheck(Document):
	def validate(self):
		self.refuse_another_churches_person()
		if self.status in RESULT_STATUSES and not self.completed_on:
			frappe.throw(f"Completed On is required when the status is {self.status}.")
		if self.completed_on and not self.expires_on:
			self.expires_on = self.default_expiry
		if self.status == "Cleared" and self.is_past_expiry:
			self.status = "Expired"

	def refuse_another_churches_person(self):
		"""Only the person's own church records their background checks.

		Frappe checks the create right before `church` is fetched from the person, and the write right on
		the check as the caller sent it, so it sees neither whose person this is nor whose check it was.
		`is_cleared` reads a person's checks whatever their church. Writes with ignore_permissions are the
		app's own and are not refused.
		"""
		if self.flags.ignore_permissions or not self.has_value_changed("person"):
			return
		before = self.get_doc_before_save()
		if before and not has_church_access(frappe.session.user, before.church):
			frappe.throw(_("This background check belongs to another church."), frappe.PermissionError)
		church = frappe.db.get_value("Person", self.person, CHURCH_FIELD)
		if not has_church_access(frappe.session.user, church):
			frappe.throw(
				_(
					"{0} belongs to another church, so only that church can record their background checks."
				).format(self.person_name or self.person),
				frappe.PermissionError,
			)

	@property
	def default_expiry(self):
		"""Expiry from the check type's validity, or None for checks that never expire."""
		years = frappe.db.get_value("Background Check Type", self.check_type, "valid_for_years")
		if not years:
			return None
		return add_years(self.completed_on, years)

	@property
	def is_past_expiry(self):
		return bool(self.expires_on) and getdate(self.expires_on) < getdate(nowdate())


def expire_background_checks():
	"""Daily scheduler: mark cleared checks whose expiry date has passed as Expired."""
	# church-scope: scheduled daily job: flips a status, shows nobody anything
	expired = frappe.get_all(
		"Background Check",
		filters={"status": "Cleared", "expires_on": ["<", nowdate()]},
		pluck="name",
	)
	for name in expired:
		frappe.db.set_value("Background Check", name, "status", "Expired")


def get_current_check_types(person: str, on_date) -> set[str]:
	"""The types of *person*'s cleared checks that have not expired by *on_date*."""
	# church-scope: answers about the one person being placed, wherever their church is
	checks = frappe.get_all(
		"Background Check",
		filters={"person": person, "status": "Cleared"},
		fields=["check_type", "expires_on"],
	)
	return {
		check.check_type
		for check in checks
		if not check.expires_on or getdate(check.expires_on) >= getdate(on_date)
	}


def is_cleared(person: str, on_date, check_types: list[str] | None = None) -> bool:
	"""True when *person* holds a cleared check, current on *on_date*, of each of *check_types*, or of any type when none are given."""
	current = get_current_check_types(person, on_date)
	return set(check_types) <= current if check_types else bool(current)
