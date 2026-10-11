# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Record how a person is connected to the church in Membership, apart from Membership Status.

Membership Status was a member's status and was blank for everyone else. Now it
is Active or Inactive for everyone, and Membership holds Member, Regular
Attender, Visitor or Contact. A status named like a Membership Type (a church's
own "Visitor") moves to Membership. Anyone else with a status or a Membership
life event becomes a Member. Notifications copied from the shipped ones get the
new conditions.
"""

import frappe

from churchit.church_people.membership import ACTIVE, MEMBER, MEMBER_STATUSES, MEMBERSHIP_TYPES

NOTIFICATION_CONDITIONS = {
	'doc.membership_status == "Active" and doc.emails': (
		'doc.membership == "Member" and doc.membership_status == "Active" and doc.emails'
	),
	'doc.membership_status != "Active"': 'doc.membership in (None, "", "Visitor")',
}


def execute():
	add_lookups()
	Person = frappe.qb.DocType("Person")
	LifeEvent = frappe.qb.DocType("Life Event")
	has_no_membership = Person.membership.isnull() | (Person.membership == "")
	has_no_status = Person.membership_status.isnull() | (Person.membership_status == "")

	for membership_type in frappe.get_all("Membership Type", pluck="name"):
		frappe.qb.update(Person).set(Person.membership, membership_type).set(
			Person.membership_status, ACTIVE
		).where(has_no_membership & (Person.membership_status == membership_type)).run()

	with_membership_event = (
		frappe.qb.from_(LifeEvent)
		.select(LifeEvent.parent)
		.where((LifeEvent.parenttype == "Person") & (LifeEvent.event_type == "Membership"))
	)
	frappe.qb.update(Person).set(Person.membership, MEMBER).where(
		has_no_membership & (~has_no_status | Person.name.isin(with_membership_event))
	).run()

	frappe.qb.update(Person).set(Person.membership_status, ACTIVE).where(has_no_status).run()

	update_notifications()


def add_lookups():
	for status in MEMBER_STATUSES:
		if not frappe.db.exists("Member Status", status):
			frappe.get_doc({"doctype": "Member Status", "status": status}).insert(ignore_permissions=True)
	for membership_type in MEMBERSHIP_TYPES:
		if not frappe.db.exists("Membership Type", membership_type):
			frappe.get_doc({"doctype": "Membership Type", "type": membership_type}).insert(
				ignore_permissions=True
			)


def update_notifications():
	for old, new in NOTIFICATION_CONDITIONS.items():
		for name in frappe.get_all(
			"Notification", filters={"document_type": "Person", "condition": old}, pluck="name"
		):
			values = {"condition": new}
			if frappe.db.get_value("Notification", name, "value_changed") == "membership_status":
				values["value_changed"] = "membership"
			frappe.db.set_value("Notification", name, values)
