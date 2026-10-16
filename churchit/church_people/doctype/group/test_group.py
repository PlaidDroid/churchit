# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe

from churchit.church_people.doctype.group.group import add_people
from churchit.tests.helpers import RollbackEachTest, ensure_user, make_person


class TestGroup(RollbackEachTest):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.member = make_person("_Test Group", "Member")
		cls.member.append("emails", {"email_address": "_test_group_member@example.com"})
		cls.member.save(ignore_permissions=True)
		cls.outsider = make_person("_Test Group", "Outsider")
		cls.user = ensure_user("_test_group_member@example.com", "_Test Group Member")
		frappe.db.set_value("Person", cls.member.name, "user", cls.user)

	def _make_group(self, name, members=(), **values):
		group = frappe.get_doc({"doctype": "Group", "group_name": name, **values})
		for person in members:
			group.append("members", {"person": person})
		return group.insert(ignore_permissions=True)

	def test_portal_access_requires_portal_flag_and_membership(self):
		hidden = self._make_group("_Test Hidden", members=[self.member.name], show_in_portal=0)
		visible = self._make_group("_Test Visible", members=[self.member.name], show_in_portal=1)

		self.assertFalse(visible.has_webform_permission())  # Administrator has no Person
		frappe.set_user(self.user)
		self.assertTrue(visible.has_webform_permission())
		self.assertFalse(hidden.has_webform_permission())

	def test_guests_never_get_portal_access(self):
		group = self._make_group("_Test Guest Group", members=[self.member.name], show_in_portal=1)
		frappe.set_user("Guest")
		self.assertFalse(group.has_webform_permission())

	def test_add_people_adds_new_members_and_skips_existing_ones(self):
		group = self._make_group("_Test Add People", members=[self.member.name])

		result = add_people(group.name, [self.member.name, self.outsider.name, self.outsider.name])

		self.assertEqual(result, {"added": 1, "already_members": 1})
		group.reload()
		self.assertEqual(
			sorted(row.person for row in group.members), sorted([self.member.name, self.outsider.name])
		)

	def test_add_people_needs_the_right_to_edit_the_group(self):
		group = self._make_group("_Test Locked Group")
		frappe.set_user(
			ensure_user("_test_group_staff@example.com", "_Test Group Staff", roles=("Church Staff",))
		)
		with self.assertRaises(frappe.PermissionError):
			add_people(group.name, [self.outsider.name])
