# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""A person's place in the church, on two fields.

`membership` (a Membership Type) is how they are connected: Member, Regular
Attender, Visitor or Contact. `membership_status` (a Member Status) is whether
they still take part: Active or Inactive, for everyone. An active member has both.
"""

import frappe

MEMBER = "Member"
REGULAR_ATTENDER = "Regular Attender"
VISITOR = "Visitor"
CONTACT = "Contact"
MEMBERSHIP_TYPES = (MEMBER, REGULAR_ATTENDER, VISITOR, CONTACT)

ACTIVE = "Active"
INACTIVE = "Inactive"
MEMBER_STATUSES = (ACTIVE, INACTIVE)


def is_active_member(Person):
	"""Query criterion for an active member on the Person table *Person*."""
	return (Person.membership == MEMBER) & (Person.membership_status == ACTIVE)


def living_people_filter(Person=None):
	"""Query criterion for the people who have not died, on the Person table *Person* or an alias of it.

	It is the one rule for who has died, in `frappe.qb` and as an item of a `frappe.get_list` filters list.
	"""
	# church-scope: builds a criterion and reads nothing; the query or get_list it joins applies the scope
	Person = Person or frappe.qb.DocType("Person")
	LifeEvent = frappe.qb.DocType("Life Event")
	deaths = (
		frappe.qb.from_(LifeEvent)
		.select(LifeEvent.parent)
		.where((LifeEvent.parenttype == "Person") & (LifeEvent.event_type == "Death"))
	)
	return Person.name.notin(deaths)
