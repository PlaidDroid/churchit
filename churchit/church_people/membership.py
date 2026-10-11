# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""A person's place in the church, on two fields.

`membership` (a Membership Type) is how they are connected: Member, Regular
Attender, Visitor or Contact. `membership_status` (a Member Status) is whether
they still take part: Active or Inactive, for everyone. An active member has both.
"""

MEMBER = "Member"
REGULAR_ATTENDER = "Regular Attender"
VISITOR = "Visitor"
CONTACT = "Contact"
MEMBERSHIP_TYPES = (MEMBER, REGULAR_ATTENDER, VISITOR, CONTACT)

ACTIVE = "Active"
INACTIVE = "Inactive"
MEMBER_STATUSES = (ACTIVE, INACTIVE)

ACTIVE_MEMBER_FILTERS = {"membership": MEMBER, "membership_status": ACTIVE}


def is_active_member(Person):
	"""Query criterion for an active member on the Person table *Person*."""
	return (Person.membership == MEMBER) & (Person.membership_status == ACTIVE)
