# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

ROLE_TYPES = {
	"spouse": ("Husband", "Wife"),
	"child": ("Son", "Daughter"),
	"parent": ("Father", "Mother"),
	"sibling": ("Brother", "Sister"),
	"grandchild": ("Grandson", "Granddaughter"),
	"grandparent": ("Grandfather", "Grandmother"),
	"nephew": ("Nephew", "Niece"),
	"uncle": ("Uncle", "Aunt"),
	"sibling_in_law": ("Brother-in-law", "Sister-in-law"),
	"parent_in_law": ("Father-in-law", "Mother-in-law"),
	"step_parent": ("Stepfather", "Stepmother"),
	"step_sibling": ("Stepbrother", "Stepsister"),
	"step_child": ("Stepson", "Stepdaughter"),
	"child_in_law": ("Son-in-law", "Daughter-in-law"),
	"cousin": ("Cousin", "Cousin"),
	"fiance": ("Fiancé", "Fiancée"),
}
ROLE_OF_TYPE = {relation_type: role for role, types in ROLE_TYPES.items() for relation_type in types}

# What someone is to a person who has this role to them.
INVERSE_ROLES = {
	"spouse": "spouse",
	"child": "parent",
	"parent": "child",
	"sibling": "sibling",
	"grandchild": "grandparent",
	"grandparent": "grandchild",
	"nephew": "uncle",
	"uncle": "nephew",
	"sibling_in_law": "sibling_in_law",
	"step_sibling": "step_sibling",
	"step_parent": "step_child",
	"step_child": "step_parent",
	"parent_in_law": "child_in_law",
	"child_in_law": "parent_in_law",
	"cousin": "cousin",
	"fiance": "fiance",
}

# What a member's spouse is to the primary contact, from that member's role.
SPOUSE_ROLES = {
	"child": "child_in_law",
	"child_in_law": "child",
	"sibling": "sibling_in_law",
}

# (person's role, other's role) -> what the other is to the person, both roles read against the primary
# contact. Pairs left out are ambiguous (two grandchildren may be siblings or cousins), so they get none.
MEMBER_ROLE_FOR = {
	("child", "child"): "sibling",
	("child", "spouse"): "parent",
	("spouse", "child"): "child",
	("child", "parent"): "grandparent",
	("parent", "child"): "grandchild",
	("child", "sibling"): "uncle",
	("sibling", "child"): "nephew",
	("sibling", "sibling"): "sibling",
	("sibling", "parent"): "parent",
	("parent", "sibling"): "child",
	("spouse", "sibling"): "sibling_in_law",
	("sibling", "spouse"): "sibling_in_law",
	("spouse", "parent"): "parent_in_law",
	("spouse", "parent_in_law"): "parent",
	("spouse", "grandchild"): "grandchild",
	("grandchild", "spouse"): "grandparent",
	("spouse", "child_in_law"): "child_in_law",
	("child_in_law", "spouse"): "parent_in_law",
	("child", "child_in_law"): "sibling_in_law",
	("child_in_law", "child"): "sibling_in_law",
}


def get_rebased_labels(labels, genders, old_contact, new_contact):
	"""{member: relationship to *new_contact*}, worked out from *labels*, their relationships to *old_contact*.

	A relationship that cannot be worked out is None, and so is the new contact's own.
	"""
	new_role = ROLE_OF_TYPE.get(labels.get(new_contact))
	rebased = {new_contact: None}
	for member, label in labels.items():
		if member == new_contact:
			continue
		if member == old_contact:
			role = INVERSE_ROLES.get(new_role)
		else:
			role = MEMBER_ROLE_FOR.get((new_role, ROLE_OF_TYPE.get(label)))
		rebased[member] = get_gendered_type(role, genders.get(member))
	return rebased


def get_spouse_label(partner_label, gender):
	"""Relationship to the primary contact for the spouse of a member labelled *partner_label*, e.g. a Son's wife is a Daughter-in-law."""
	return get_gendered_type(SPOUSE_ROLES.get(ROLE_OF_TYPE.get(partner_label)), gender)


def get_gendered_type(role, gender):
	"""The relation type for *role* that fits *gender*, or None when *gender* decides it and is unknown."""
	if not role:
		return None
	male, female = ROLE_TYPES[role]
	if male == female:
		return male
	return {"Male": male, "Female": female}.get(gender)
