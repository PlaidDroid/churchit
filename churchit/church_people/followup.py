# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Follow-up cards: one person on one workflow, moved through its steps until someone closes it.

`add_card` is the one way code opens a card, and `start_cards` is the one way a trigger
(a connection card, a first check-in, a life event, a membership change, a smart list)
reaches the workflows that start on it. Anything else a church wants to happen when a card
starts or moves is a standard Notification on Follow-up Card.
"""

import json

import frappe
from frappe import _
from frappe.permissions import get_doctype_roles
from frappe.utils import today

from churchit.church_scope import church_filters

CARD = "Follow-up Card"
WORKFLOW = "Follow-up Workflow"
STEP = "Follow-up Step"
TRIGGER = "Follow-up Trigger"
OPEN = "Open"
CLOSED = "Closed"
FOLLOW_UP_ROLE = "Church Follow-up"

CONNECTION_CARD = "Connection Card"
FIRST_CHECK_IN = "First Check-In"
LIFE_EVENT_ADDED = "Life Event Added"
MEMBERSHIP_TYPE_BECOMES = "Membership Type Becomes"
MEMBER_STATUS_BECOMES = "Member Status Becomes"
ON_SMART_LIST = "On Smart List"
# What a trigger row's "Which" value names. follow_up_workflow.js offers the same pickers.
VALUE_TYPES = {
	CONNECTION_CARD: "Connection Card Interest",
	FIRST_CHECK_IN: "Function Type",
	LIFE_EVENT_ADDED: "Life Event Type",
	MEMBERSHIP_TYPE_BECOMES: "Membership Type",
	MEMBER_STATUS_BECOMES: "Member Status",
	ON_SMART_LIST: "Report",
}
# The trigger each membership field starts, by the field a membership_changed event names.
MEMBERSHIP_TRIGGERS = {"membership": MEMBERSHIP_TYPE_BECOMES, "membership_status": MEMBER_STATUS_BECOMES}


def add_card(
	person: str,
	workflow: str,
	reason: str | None = None,
	assignee: str | None = None,
	source=None,
) -> str:
	"""Open a card for *person* on *workflow* and return its name, or the name of the card already open.

	*assignee* (a User) takes the card instead of its step's user, and *source* is the
	document that started it. A person has at most one open card per workflow, so calling
	this twice for the same reason opens one card.
	"""
	# Two jobs for the same person (a connection card and a check-in) wait here instead of both inserting.
	frappe.db.get_value("Person", person, "name", for_update=True)
	existing = frappe.db.get_value(CARD, {"person": person, "workflow": workflow, "status": OPEN})
	if existing:
		return existing

	card = frappe.get_doc(
		{
			"doctype": CARD,
			"person": person,
			"workflow": workflow,
			"reason": reason,
			"source_type": source.doctype if source else None,
			"source_name": source.name if source else None,
		}
	)
	card.flags.assignee = assignee
	card.insert(ignore_permissions=True)
	return card.name


def start_cards(
	trigger: str, person: str, church: str | None, value: str | None = None, source=None
) -> list[str]:
	"""Open a card for *person* on each active workflow of *church* that starts on *trigger*.

	A trigger row with no value starts on every *value*; one with a value starts only on that one.
	"""
	workflows = frappe.get_all(WORKFLOW, filters=church_filters(church, is_active=1), pluck="name")
	if not workflows:
		return []
	rows = frappe.get_all(
		TRIGGER,
		filters={"parenttype": WORKFLOW, "parent": ("in", workflows), "trigger": trigger},
		fields=["parent", "value"],
	)
	starting = sorted({row.parent for row in rows if not row.value or row.value == value})
	reason = describe_trigger(trigger, value)
	return [add_card(person, workflow, reason=reason, source=source) for workflow in starting]


def start_person_cards(trigger: str, person: str, value: str | None) -> list[str]:
	"""start_cards on the workflows of *person*'s own church."""
	return start_cards(trigger, person, frappe.db.get_value("Person", person, "church"), value=value)


def describe_trigger(trigger: str, value: str | None) -> str:
	"""The reason a card started, as its form shows it."""
	return _("{0}: {1}").format(_(trigger), value) if value else _(trigger)


def first_step(workflow: str) -> str | None:
	return frappe.db.get_value(STEP, {"parenttype": WORKFLOW, "parent": workflow}, "step", order_by="idx asc")


def has_card_permission(doc, ptype=None, user=None) -> bool:
	"""has_permission hook for Follow-up Card: the follow-up role reaches only the cards assigned to it.

	Frappe reads any falsy return as a refusal, so every other user gets True.
	"""
	user = user or frappe.session.user
	if not is_follow_up_only(user):
		return True
	if doc.is_new():
		return False
	# The stored assignees: a form sends none, and a caller could send any.
	return user in json.loads(frappe.db.get_value(CARD, doc.name, "_assign") or "[]")


def card_query_conditions(user=None, doctype=None) -> str:
	"""permission_query_conditions for Follow-up Card: the follow-up role lists only the cards assigned to it."""
	user = user or frappe.session.user
	if not is_follow_up_only(user):
		return ""
	# Frappe keeps the assignees in `_assign` as a JSON list; a quoted name cannot match a longer one.
	quoted = json.dumps(user).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
	return f"`tabFollow-up Card`.`_assign` like {frappe.db.escape(f'%{quoted}%')}"


def is_follow_up_only(user: str) -> bool:
	"""True when *user* holds rights on Follow-up Cards only through the Church Follow-up role."""
	return set(get_doctype_roles(CARD)) & set(frappe.get_roles(user)) == {FOLLOW_UP_ROLE}


def on_membership_changed(
	person: str, field: str, old: str | None = None, new: str | None = None
) -> list[str]:
	"""churchit_people_events handler: a new membership type or status starts the workflows waiting for it."""
	trigger = MEMBERSHIP_TRIGGERS.get(field)
	if not (trigger and new):
		return []
	return start_person_cards(trigger, person, new)


def on_life_event_added(person: str, event_type: str | None = None, date=None) -> list[str]:
	"""churchit_people_events handler: a new life event starts the workflows waiting for its type."""
	return start_person_cards(LIFE_EVENT_ADDED, person, event_type)


def on_check_in(doc, method=None) -> None:
	"""after_insert on Function Check-In: a person's first check-in ever starts the First Check-In workflows.

	The cards start after the check-in commits, so a follow-up problem never stops the Check-In Station.
	"""
	# church-scope: a first check-in is the first at any church
	if frappe.db.exists("Function Check-In", {"person": doc.person, "name": ("!=", doc.name)}):
		return
	frappe.enqueue(start_check_in_cards, enqueue_after_commit=True, check_in=doc.name)


def start_check_in_cards(check_in: str) -> list[str]:
	"""Background job: start the First Check-In workflows of the church that held the function.

	A function shared with every church belongs to none of them, so the person's own church follows them up.
	"""
	doc = frappe.get_doc("Function Check-In", check_in)
	church = doc.church or frappe.db.get_value("Person", doc.person, "church")
	return start_cards(FIRST_CHECK_IN, doc.person, church, value=doc.function_type, source=doc)


def wake_snoozed_cards() -> None:
	"""Daily job: clear the snooze of every card whose date has come, so it is back on its board."""
	# church-scope: each card wakes on its own date; nothing here is shown to a user
	for name in frappe.get_all(CARD, filters={"snoozed_until": ("<=", today())}, pluck="name"):
		card = frappe.get_doc(CARD, name)
		card.snoozed_until = None
		card.save(ignore_permissions=True)


def start_smart_list_cards() -> None:
	"""Daily job: open a card for each person new to the smart list of an On Smart List trigger."""
	# church-scope: every church's workflows; each list is read inside its own workflow's church
	workflows = frappe.get_all(WORKFLOW, filters={"is_active": 1}, pluck="name")
	rows = frappe.get_all(
		TRIGGER,
		filters={"parenttype": WORKFLOW, "parent": ("in", workflows), "trigger": ON_SMART_LIST},
		fields=["parent", "value"],
	)
	for row in rows:
		add_smart_list_people(row.parent, row.value)


def add_smart_list_people(workflow: str, report: str) -> list[str]:
	"""Open a card on *workflow* for each living person of its church on the saved list *report*.

	A person the list added once is never added again, even after their card is closed and they
	drop off the list and come back.
	"""
	church = frappe.db.get_value(WORKFLOW, workflow, "church")
	source = frappe.get_doc("Report", report)
	filters = source.get_standard_report_filters(
		json.loads(source.json or "{}"), church_filters(church, is_deceased=0)
	)
	people = frappe.get_all("Person", filters=filters, pluck="name")
	added_before = set(
		frappe.get_all(
			CARD,
			filters={"workflow": workflow, "source_type": "Report", "source_name": report},
			pluck="person",
		)
	)
	reason = describe_trigger(ON_SMART_LIST, report)
	return [
		add_card(person, workflow, reason=reason, source=source)
		for person in people
		if person not in added_before
	]


def close_repeated_cards(person: str) -> list[str]:
	"""Close every open card of *person* but the oldest on each workflow, and return the closed ones.

	Merging two people can leave the one kept with two open cards on one workflow.
	"""
	# church-scope: one person's cards, each closed on its own workflow
	cards = frappe.get_all(
		CARD, filters={"person": person, "status": OPEN}, fields=["name", "workflow"], order_by="creation asc"
	)
	kept = {}
	closed = []
	for card in cards:
		if card.workflow not in kept:
			kept[card.workflow] = card.name
			continue
		doc = frappe.get_doc(CARD, card.name)
		doc.status = CLOSED
		doc.outcome = _("Closed when two people were merged. {0} carries on.").format(kept[card.workflow])
		doc.save(ignore_permissions=True)
		closed.append(card.name)
	return closed
