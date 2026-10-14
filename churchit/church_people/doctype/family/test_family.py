# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe.tests.utils import FrappeTestCase

from churchit.tests.helpers import make_primary_contact


class TestFamily(FrappeTestCase):
	def _make_person(self, first_name, **values):
		return frappe.get_doc({"doctype": "Person", "first_name": first_name, **values}).insert(
			ignore_permissions=True
		)

	def _labels(self, family_name):
		return {
			member.member: member.relationship_to_primary_contact
			for member in frappe.get_doc("Family", family_name).members
		}

	def test_primary_contact_and_spouse_are_labelled_in_members(self):
		family = frappe.get_doc({"doctype": "Family", "family_name": "Labelled Household"}).insert(
			ignore_permissions=True
		)
		head = make_primary_contact(self._make_person("Head", gender="Male", family=family.name))
		wife = self._make_person("Wife", gender="Female", family=family.name)
		head.reload()
		head.spouse = wife.name
		head.marital_status = "Married"
		head.save(ignore_permissions=True)

		self.assertEqual(self._labels(family.name), {head.name: "Husband", wife.name: "Wife"})

	def test_primary_contact_must_be_a_member(self):
		outsider = self._make_person("Outsider")
		family = frappe.get_doc(
			{"doctype": "Family", "family_name": "Closed Household", "primary_contact": outsider.name}
		)

		self.assertRaises(frappe.ValidationError, family.insert, ignore_permissions=True)

	def test_adding_member_sets_person_family(self):
		person = self._make_person("Linked")
		family = frappe.get_doc({"doctype": "Family", "family_name": "Linked Household"})
		family.append("members", {"member": person.name})
		family.insert(ignore_permissions=True)

		# Family.before_save back-links the Person to this family.
		self.assertEqual(frappe.db.get_value("Person", person.name, "family"), family.name)

	def test_removing_member_clears_person_family(self):
		person = self._make_person("Unlinked")
		family = frappe.get_doc({"doctype": "Family", "family_name": "Unlinked Household"})
		family.append("members", {"member": person.name})
		family.insert(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Person", person.name, "family"), family.name)

		family.members = []
		family.save(ignore_permissions=True)
		self.assertIsNone(frappe.db.get_value("Person", person.name, "family"))

	def _make_household(self, family_name):
		"""A father as primary contact, his linked wife, his mother and two sons labelled on the Family."""
		family = frappe.get_doc({"doctype": "Family", "family_name": family_name}).insert(
			ignore_permissions=True
		)
		dad = make_primary_contact(self._make_person("Dad", gender="Male", family=family.name))
		mom = self._make_person(
			"Mom", gender="Female", family=family.name, marital_status="Married", spouse=dad.name
		)
		gran = self._make_person("Gran", gender="Female", family=family.name)
		bob = self._make_person("Bob", gender="Male", family=family.name)
		bill = self._make_person("Bill", gender="Male", family=family.name)
		family.reload()
		labels = {gran.name: "Mother", bob.name: "Son", bill.name: "Son"}
		for member in family.members:
			member.relationship_to_primary_contact = labels.get(
				member.member, member.relationship_to_primary_contact
			)
		family.save(ignore_permissions=True)
		return family, dad, mom, gran, bob, bill

	def test_changing_primary_contact_relates_members_to_them_and_renames_the_family(self):
		family, dad, mom, gran, bob, bill = self._make_household("Doe - Dad")
		family.reload()
		family.primary_contact = mom.name
		frappe.local.message_log = []
		family.save(ignore_permissions=True)

		self.assertEqual(
			self._labels(family.name),
			{
				dad.name: "Husband",
				mom.name: "Wife",
				gran.name: "Mother-in-law",
				bob.name: "Son",
				bill.name: "Son",
			},
		)
		self.assertEqual(family.family_name, "Doe - Mom")
		self.assertIn("Doe - Mom", str(frappe.local.message_log))

	def test_a_son_as_primary_contact_relates_his_parents_grandmother_and_brother(self):
		family, dad, mom, gran, bob, bill = self._make_household("Son - Dad")
		family.reload()
		family.primary_contact = bob.name
		family.save(ignore_permissions=True)

		self.assertEqual(
			self._labels(family.name),
			{
				dad.name: "Father",
				mom.name: "Mother",
				gran.name: "Grandmother",
				bob.name: None,
				bill.name: "Brother",
			},
		)

	def test_a_relationship_that_cannot_be_worked_out_is_cleared_and_named(self):
		family, _dad, mom, _gran, _bob, _bill = self._make_household("Cousin - Dad")
		cal = self._make_person("Cal", gender="Male", family=family.name)
		family.reload()
		for member in family.members:
			if member.member == cal.name:
				member.relationship_to_primary_contact = "Cousin"
		family.primary_contact = mom.name
		frappe.local.message_log = []
		family.save(ignore_permissions=True)

		self.assertIsNone(self._labels(family.name)[cal.name])
		self.assertIn("Set the relationship by hand for: Cal.", str(frappe.local.message_log))

	def test_sons_wife_is_labelled_as_an_in_law(self):
		family, _dad, _mom, _gran, bob, _bill = self._make_household("In-law Household")
		jane = self._make_person("Jane", gender="Female")
		bob = frappe.get_doc("Person", bob.name)
		bob.marital_status = "Married"
		bob.spouse = jane.name
		bob.save(ignore_permissions=True)

		self.assertEqual(self._labels(family.name)[jane.name], "Daughter-in-law")

	def test_married_son_starts_his_own_family_with_his_wife(self):
		family, _dad, _mom, _gran, bob, _bill = self._make_household("Parents Household")
		jane = self._make_person("Jane", gender="Female")
		bob = frappe.get_doc("Person", bob.name)
		bob.last_name = "Parents"
		bob.marital_status = "Married"
		bob.spouse = jane.name
		bob.save(ignore_permissions=True)

		bob.new_family_from_person()

		self.assertNotEqual(bob.family, family.name)
		self.assertEqual(frappe.db.get_value("Family", bob.family, "primary_contact"), bob.name)
		self.assertEqual(self._labels(bob.family), {bob.name: "Husband", jane.name: "Wife"})
		self.assertNotIn(bob.name, self._labels(family.name))
		self.assertNotIn(jane.name, self._labels(family.name))
		self.assertEqual(frappe.db.get_value("Person", jane.name, "family"), bob.family)
