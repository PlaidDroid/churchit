# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe import _
from frappe.desk.form import assign_to
from frappe.model.document import Document
from frappe.utils import add_days, cint, escape_html, today

from churchit.church_people.followup import CARD, CLOSED, OPEN, STEP, WORKFLOW, first_step, get_open_card
from churchit.church_scope import CHURCH_FIELD, has_church_access
from churchit.contacts import get_primary_email, get_primary_phone


class FollowupCard(Document):
	"""One person on one follow-up workflow. Its step says who it is assigned to and when it is due."""

	def onload(self):
		# Frappe has checked that the reader may open this card. A copy on the card would outlive anonymizing.
		self.set_onload(
			"contact",
			{
				"phone": get_primary_phone("Person", self.person),
				"email": get_primary_email("Person", self.person),
			},
		)
		self.set_onload(
			"steps",
			frappe.get_all(
				STEP,
				filters={"parenttype": WORKFLOW, "parent": self.workflow},
				order_by="idx asc",
				pluck="step",
			),
		)

	def validate(self):
		self.refuse_another_churches_person()
		self.refuse_another_churches_workflow()
		self.step = self.step or first_step(self.workflow)
		if self.status == OPEN or self.has_value_changed("step"):
			self.validate_step()
		if self.status == OPEN and self.has_value_changed("status"):
			self.refuse_second_open_card()
		if self.is_handed_on():
			self.due_on = add_days(today(), cint(self.get_step().due_in_days))
		self.closed_on = (self.closed_on or today()) if self.status == CLOSED else None

	def on_update(self):
		if self.is_handed_on():
			self.hand_to_assignee()
		elif self.status == CLOSED and self.has_value_changed("status"):
			close_todos(self.name)

	def is_handed_on(self) -> bool:
		"""True when this save brings an open card to a step: a new card, a move, or a card reopened."""
		return self.status == OPEN and (self.has_value_changed("step") or self.has_value_changed("status"))

	def get_step(self) -> frappe._dict | None:
		return frappe.db.get_value(
			STEP,
			{"parenttype": WORKFLOW, "parent": self.workflow, "step": self.step},
			["assign_to", "due_in_days", "description"],
			as_dict=True,
		)

	def validate_step(self):
		if not self.get_step():
			frappe.throw(
				_("{0} is not a step of {1}.").format(frappe.bold(self.step), frappe.bold(self.workflow))
			)

	def refuse_another_churches_person(self):
		"""Only the person's own church puts them on a card.

		Frappe checks the create right before `church` is fetched, so it never sees
		whose person this is, and the card's form shows the person's phone and email.
		Writes with ignore_permissions, such as add_card from a job, are the app's own
		and are not refused.
		"""
		if self.flags.ignore_permissions or not self.has_value_changed("person"):
			return
		church = frappe.db.get_value("Person", self.person, CHURCH_FIELD)
		if not has_church_access(frappe.session.user, church):
			frappe.throw(
				_("{0} belongs to another church, so only that church can open a card for them.").format(
					self.person_name or self.person
				),
				frappe.PermissionError,
			)

	def refuse_another_churches_workflow(self):
		"""Only the workflow's own church opens cards on it.

		The card takes its church from the workflow after Frappe checked the create right, and the
		workflow's step users then read the person's phone and email on the card. Jobs open cards with
		ignore_permissions, so a workflow may still follow up a person who visited from another church.
		"""
		if self.flags.ignore_permissions or not self.has_value_changed("workflow"):
			return
		if not has_church_access(frappe.session.user, self.church):
			frappe.throw(
				_("{0} belongs to another church, so only that church can open cards on it.").format(
					self.workflow
				),
				frappe.PermissionError,
			)

	def refuse_second_open_card(self):
		other = get_open_card(self.person, self.workflow, exclude=self.name)
		if other:
			frappe.throw(
				_("{0} already has an open card on {1}: {2}.").format(
					self.person_name or self.person, self.workflow, other
				),
				frappe.DuplicateEntryError,
			)

	def hand_to_assignee(self):
		"""Close the card's open to-dos and assign its step's user, or the one add_card named, due on due_on."""
		close_todos(self.name)
		step = self.get_step()
		assignee = self.flags.assignee or step.assign_to
		if not assignee:
			return
		lines = [f"{self.step}: {self.person_name or self.person}", step.description]
		assign_to._add(
			{
				"assign_to": [assignee],
				"doctype": CARD,
				"name": self.name,
				"date": self.due_on,
				"description": "".join(f"<p>{escape_html(line)}</p>" for line in lines if line),
			},
			ignore_permissions=True,
		)


def close_todos(card: str) -> None:
	"""Close the card's open to-dos, the way an assignee marks one done, so nobody is told it was taken away."""
	for name in frappe.get_all(
		"ToDo", filters={"reference_type": CARD, "reference_name": card, "status": "Open"}, pluck="name"
	):
		todo = frappe.get_doc("ToDo", name)
		todo.status = "Closed"
		todo.save(ignore_permissions=True)
