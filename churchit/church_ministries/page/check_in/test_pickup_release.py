# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe

from churchit.church_ministries.page.check_in.check_in import get_pickup_families, release_children
from churchit.church_ministries.page.check_in.test_check_in import check_in_with_code
from churchit.church_people.test_household import make_household, set_adult_age
from churchit.tests.helpers import RollbackEachTest, ensure, ensure_user, make_function, make_person


class TestPickupRelease(RollbackEachTest):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		set_adult_age(18)
		cls.volunteer = ensure_user(
			"_test_release_volunteer@example.com", "ReleaseVolunteer", roles=("Church Check-In",)
		)

	def setUp(self):
		super().setUp()
		function = make_function("_Test Release Service", start_date=frappe.utils.today()).name
		self.family, self.father, _mother, self.son, self.grandmother = make_household(
			"_Test Release Household", "Releaseton"
		)
		self.daughter = make_person("Dot", "Releaseton", family=self.family).name
		self.aunt = make_person("Ada", "_Test Release Aunt").name
		family = frappe.get_doc("Family", self.family)
		family.append(
			"authorized_pickups",
			{"person": self.aunt, "relationship": ensure("Person Relation Type", {"type": "Aunt"})},
		)
		family.save(ignore_permissions=True)
		self.son_check_in = check_in_with_code(function, self.son, "R4LS").name
		self.daughter_check_in = check_in_with_code(function, self.daughter, "R4LS").name
		frappe.set_user(self.volunteer)
		self.addCleanup(frappe.set_user, "Administrator")

	def test_the_volunteer_records_who_collected_each_ticked_child(self):
		(family,) = release_children("r4ls", [self.son_check_in], self.father)

		collected = frappe.db.get_value(
			"Function Check-In",
			self.son_check_in,
			["collected_by", "collected_by_name", "released_by", "collected_at"],
			as_dict=True,
		)
		self.assertEqual(
			(collected.collected_by, collected.collected_by_name, collected.released_by),
			(self.father, "John Releaseton", self.volunteer),
		)
		self.assertTrue(collected.collected_at)
		by_check_in = {child.check_in: child.collected_by for child in family["children"]}
		self.assertEqual(by_check_in, {self.son_check_in: self.father, self.daughter_check_in: None})

	def test_an_authorized_pickup_may_collect_and_a_child_is_released_once(self):
		release_children("R4LS", frappe.as_json([self.daughter_check_in]), self.aunt)
		self.assertEqual(
			frappe.db.get_value("Function Check-In", self.daughter_check_in, "collected_by"), self.aunt
		)

		with self.assertRaisesRegex(frappe.ValidationError, "already been collected"):
			release_children("R4LS", [self.daughter_check_in], self.father)

	def test_no_one_else_takes_the_children(self):
		stranger = make_person("Sly", "_Test Release Stranger").name
		for collector in (stranger, self.grandmother):
			with self.subTest(collector), self.assertRaises(frappe.PermissionError):
				release_children("R4LS", [self.son_check_in], collector)
		self.assertIsNone(frappe.db.get_value("Function Check-In", self.son_check_in, "collected_by"))

	def test_only_a_child_of_this_code_is_released(self):
		with self.assertRaises(frappe.PermissionError):
			release_children("ZZZZ", [self.son_check_in], self.father)
		self.assertIsNone(frappe.db.get_value("Function Check-In", self.son_check_in, "collected_by"))

	def test_only_the_check_in_right_may_release(self):
		staff = ensure_user("_test_release_staff@example.com", "ReleaseStaff", roles=("Church Staff",))
		frappe.set_user(staff)
		with self.assertRaises(frappe.PermissionError):
			release_children("R4LS", [self.son_check_in], self.father)

	def test_the_code_shows_who_took_a_child_and_when(self):
		release_children("R4LS", [self.son_check_in], self.father)

		(family,) = get_pickup_families("R4LS")
		(son,) = [child for child in family["children"] if child.person == self.son]
		self.assertEqual((son.collected_by_name, bool(son.collected_at)), ("John Releaseton", True))
