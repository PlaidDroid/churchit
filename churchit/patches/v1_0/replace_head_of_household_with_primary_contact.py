# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Make each family's head of household its Primary Contact, and move what the Person Relations said onto the Family.

A blank Relationship to Primary Contact is filled in: Husband or Wife for the primary contact and their
spouse when both are members, else from a Relations row between the member and the primary contact.
Every other Relations row, such as one to a person in another family, is dropped with the Relations table.
"""

import frappe
from frappe.model.utils.rename_field import rename_field
from frappe.query_builder.functions import Coalesce

from churchit.church_people.family_relations import INVERSE_ROLES, ROLE_OF_TYPE, get_gendered_type


def execute():
	if not frappe.db.count("Family Members", {"relationship_to_primary_contact": ("is", "set")}):
		rename_field("Family Members", "relationship_to_head", "relationship_to_primary_contact")
	if frappe.db.has_column("Person", "is_head_of_household"):
		set_primary_contacts()
	relation_types = set(frappe.get_all("Person Relation Type", pluck="name"))
	relations = get_relations() if frappe.db.table_exists("Person Relation") else {}
	for family in get_families():
		label_members(family, relations, relation_types)
	rename_in_notifications()


def set_primary_contacts():
	"""The head of household, when they are on the family's members, becomes its primary contact."""
	Family = frappe.qb.DocType("Family")
	FamilyMember = frappe.qb.DocType("Family Members")
	Person = frappe.qb.DocType("Person")
	heads = (
		frappe.qb.from_(Person)
		.join(FamilyMember)
		.on((FamilyMember.parenttype == "Family") & (FamilyMember.parent == Person.family))
		.select(Person.family, Person.name)
		.where((Person.is_head_of_household == 1) & (FamilyMember.member == Person.name))
		.run()
	)
	for family, person in heads:
		frappe.qb.update(Family).set(Family.primary_contact, person).where(
			(Family.name == family) & (Coalesce(Family.primary_contact, "") == "")
		).run()


def get_relations():
	"""{(person, other): relation type}, what *other* is to *person*."""
	Relation = frappe.qb.DocType("Person Relation")
	rows = (
		frappe.qb.from_(Relation)
		.select(Relation.parent, Relation.person, Relation.type)
		.where(Relation.parenttype == "Person")
		.run()
	)
	return {(person, other): relation_type for person, other, relation_type in rows}


def get_families():
	Family = frappe.qb.DocType("Family")
	FamilyMember = frappe.qb.DocType("Family Members")
	Person = frappe.qb.DocType("Person")
	rows = (
		frappe.qb.from_(Family)
		.join(FamilyMember)
		.on((FamilyMember.parenttype == "Family") & (FamilyMember.parent == Family.name))
		.join(Person)
		.on(Person.name == FamilyMember.member)
		.select(
			Family.name.as_("family"),
			Family.primary_contact,
			FamilyMember.name.as_("row"),
			FamilyMember.member,
			FamilyMember.relationship_to_primary_contact.as_("label"),
			Person.gender,
			Person.spouse,
		)
		.where(Coalesce(Family.primary_contact, "") != "")
		.run(as_dict=True)
	)
	families = {}
	for row in rows:
		families.setdefault(row.family, []).append(row)
	return families.values()


def label_members(members, relations, relation_types):
	"""Fill each blank relationship to the primary contact from the spouse link and the Relations rows."""
	contact = members[0].primary_contact
	by_name = {member.member: member for member in members}
	couple = (contact, by_name[contact].spouse) if contact in by_name else ()
	FamilyMember = frappe.qb.DocType("Family Members")
	for member in members:
		if member.label:
			continue
		if member.member in couple and couple[1] in by_name:
			label = get_gendered_type("spouse", member.gender)
		else:
			label = get_label_from_relations(member, contact, relations)
		if label in relation_types:
			frappe.qb.update(FamilyMember).set(FamilyMember.relationship_to_primary_contact, label).where(
				FamilyMember.name == member.row
			).run()


def get_label_from_relations(member, contact, relations):
	"""What *member* is to *contact*: the contact's own row about them, else the reverse of theirs."""
	label = relations.get((contact, member.member))
	if label:
		return label
	their_row = relations.get((member.member, contact))
	return get_gendered_type(INVERSE_ROLES.get(ROLE_OF_TYPE.get(their_row)), member.gender)


def rename_in_notifications():
	"""Point Notification conditions copied from the Anniversary Greeting at the primary contact."""
	notifications = frappe.get_all(
		"Notification",
		filters={"document_type": "Person", "condition": ["like", "%is_head_of_household%"]},
		fields=["name", "condition"],
	)
	for notification in notifications:
		condition = notification.condition.replace("is_head_of_household", "is_primary_contact")
		frappe.db.set_value("Notification", notification.name, "condition", condition, update_modified=False)
