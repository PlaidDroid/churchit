# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe.tests.utils import FrappeTestCase

from churchit.tests.helpers import ensure, ensure_user


class TestPrayerAccess(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		request_type = ensure("Prayer Request Type", {"type": "Health"})
		cls.private, cls.team, cls.whole = (
			frappe.get_doc(
				{
					"doctype": "Prayer Request",
					"title": f"_Test Access {visibility}",
					"type": request_type,
					"visibility": visibility,
				}
			).insert(ignore_permissions=True)
			for visibility in ("Private", "Prayer Team", "Whole Church")
		)
		cls.member = ensure_user("_test_access_member@example.com", "_Test Member")
		cls.praying = ensure_user(
			"_test_access_team@example.com", "_Test Team", roles=("Church User", "Church Prayer Team")
		)
		cls.staff_praying = ensure_user(
			"_test_access_staff_team@example.com", "_Test Staff", roles=("Church Staff", "Church Prayer Team")
		)
		cls.care = ensure_user("_test_access_care@example.com", "_Test Care", roles=("Church Pastoral Care",))
		cls.own_private = frappe.get_doc(
			{
				"doctype": "Prayer Request",
				"title": "_Test Access Own Private",
				"type": request_type,
				"visibility": "Private",
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Prayer Request", cls.own_private.name, "owner", cls.praying)

	def tearDown(self):
		frappe.set_user("Administrator")

	def readable(self, user):
		"""The test requests *user* lists, and the ones they may open."""
		names = {self.private.name, self.team.name, self.whole.name, self.own_private.name}
		frappe.set_user(user)
		listed = set(frappe.get_list("Prayer Request", filters={"name": ("in", names)}, pluck="name"))
		opened = {name for name in names if frappe.has_permission("Prayer Request", "read", doc=name)}
		frappe.set_user("Administrator")
		return listed, opened

	def test_the_prayer_team_reads_every_request_but_another_persons_private_one(self):
		expected = {self.team.name, self.whole.name, self.own_private.name}
		self.assertEqual(self.readable(self.praying), (expected, expected))

	def test_staff_on_the_prayer_team_still_cannot_read_private_requests(self):
		expected = {self.team.name, self.whole.name}
		self.assertEqual(self.readable(self.staff_praying), (expected, expected))

	def test_a_member_reads_only_their_own_requests(self):
		self.assertEqual(self.readable(self.member), (set(), set()))

	def test_pastoral_care_reads_private_requests(self):
		listed, opened = self.readable(self.care)
		self.assertIn(self.private.name, listed)
		self.assertIn(self.private.name, opened)


class TestVisibilityPatch(FrappeTestCase):
	def test_a_ticked_request_becomes_private_once(self):
		from churchit.patches.v1_0 import set_prayer_request_visibility

		if not frappe.db.has_column("Prayer Request", "is_private"):
			self.skipTest("This site never had Is Private")
		request_type = ensure("Prayer Request Type", {"type": "Health"})
		ticked, plain = (
			frappe.get_doc(
				{"doctype": "Prayer Request", "title": f"_Test Patch {title}", "type": request_type}
			).insert(ignore_permissions=True)
			for title in ("Ticked", "Plain")
		)
		frappe.db.set_value("Prayer Request", ticked.name, "is_private", 1)

		set_prayer_request_visibility.execute()
		self.assertEqual(frappe.db.get_value("Prayer Request", ticked.name, "visibility"), "Private")
		self.assertEqual(frappe.db.get_value("Prayer Request", plain.name, "visibility"), "Whole Church")

		frappe.db.set_value("Prayer Request", ticked.name, "visibility", "Prayer Team")
		set_prayer_request_visibility.execute()
		self.assertEqual(frappe.db.get_value("Prayer Request", ticked.name, "visibility"), "Prayer Team")
