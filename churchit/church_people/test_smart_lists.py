# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe

from churchit.church_people import smart_lists
from churchit.church_people.doctype.follow_up_workflow.test_follow_up_workflow import make_smart_list
from churchit.tests.helpers import RollbackEachTest, force_single_church, make_person, make_two_churches


class TestSmartLists(RollbackEachTest):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		force_single_church()

	def test_a_smart_list_is_a_report_builder_report_on_person(self):
		self.assertTrue(smart_lists.is_smart_list(make_smart_list("_Test Smart People").name))
		self.assertFalse(smart_lists.is_smart_list(make_smart_list("_Test Smart Families", "Family").name))
		self.assertFalse(smart_lists.is_smart_list(None))

	def test_the_people_on_a_list_are_the_living_ones_it_matches(self):
		smart_list = make_smart_list("_Test Smart Living").name
		listed = make_person("_Test Smart", "_Test Smart List").name
		make_person("_Test Smart", "Not Listed")
		died = make_person("_Test Smart Died", "_Test Smart List").name
		frappe.db.set_value("Person", died, "is_deceased", 1)

		self.assertEqual(smart_lists.get_people(smart_list, None), [listed])

	def test_another_report_is_refused(self):
		families = make_smart_list("_Test Smart Other", "Family").name
		with self.assertRaisesRegex(frappe.ValidationError, "not a smart list"):
			smart_lists.get_people(families, None)


class TestSmartListChurches(RollbackEachTest):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.root, cls.branch_a, cls.branch_b = make_two_churches()
		cls.addClassCleanup(frappe.clear_cache)

	def test_a_list_read_for_one_church_holds_only_its_people(self):
		smart_list = make_smart_list("_Test Smart Branch").name
		mine = make_person("_Test Smart Mine", "_Test Smart List", church=self.branch_a).name
		make_person("_Test Smart Theirs", "_Test Smart List", church=self.branch_b)

		self.assertEqual(smart_lists.get_people(smart_list, self.branch_a), [mine])
