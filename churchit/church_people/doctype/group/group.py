# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe.model.document import Document


class Group(Document):
	def has_webform_permission(self):
		"""Portal access: only members of a portal-visible group may view it."""
		if not self.show_in_portal:
			return False
		user = frappe.session.user
		if user == "Guest":
			return False
		person = frappe.db.get_value("Person", {"user": user}, "name")
		if not person:
			return False
		return bool(frappe.db.exists("Group Member", {"parent": self.name, "person": person}))


@frappe.whitelist()
def add_people(group: str, people: str | list[str], group_role: str | None = None):
	"""Add *people* to a Group, skipping anyone already in it."""
	doc = frappe.get_doc("Group", group)
	doc.check_permission("write")
	people = list(dict.fromkeys(frappe.parse_json(people)))
	existing = {member.person for member in doc.members}
	added = [person for person in people if person not in existing]
	for person in added:
		# `person` is a plain Link, which user permissions never measure against its church.
		frappe.has_permission("Person", doc=person, throw=True)
		doc.append("members", {"person": person, "group_role": group_role})
	if added:
		doc.save()
	return {"added": len(added), "already_members": len(people) - len(added)}
