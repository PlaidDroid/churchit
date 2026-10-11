# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe

from churchit.tests.helpers import RollbackEachTest, ensure, make_person


class TestMemberTransfer(RollbackEachTest):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure("Member Status", {"status": "Active"})
		ensure("Member Status", {"status": "Inactive"})
		ensure("Membership Type", {"type": "Member"})
		ensure("Membership Type", {"type": "Contact"})

	def transfer_out(self, person, status):
		return frappe.get_doc(
			{
				"doctype": "Member Transfer",
				"person": person,
				"direction": "Out",
				"other_church": "_Test Elsewhere Church",
				"status": status,
			}
		).insert()

	def test_confirming_a_transfer_out_ends_the_membership(self):
		person = make_person("_Test Leaving", "Member", membership="Member")
		transfer = self.transfer_out(person.name, "Requested")
		self.assertEqual(frappe.db.get_value("Person", person.name, "membership"), "Member")

		transfer.status = "Confirmed"
		transfer.save()

		person.reload()
		self.assertEqual(person.membership, "Contact")
		self.assertEqual(person.membership_status, "Inactive")
		self.assertEqual(person.inactive_reason, "Transferred to _Test Elsewhere Church")
