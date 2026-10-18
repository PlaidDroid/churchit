# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import json
from unittest.mock import patch

import frappe
from frappe.website.doctype.personal_data_download_request.personal_data_download_request import (
	get_user_data,
)

from churchit.church_foundations.church_access import set_user_church
from churchit.church_people.privacy import ANONYMIZED, anonymize_person
from churchit.church_people.test_merge import email, life_event, make_family, phone
from churchit.tests.helpers import (
	RollbackEachTest,
	ensure,
	ensure_user,
	make_address,
	make_person,
	make_primary_contact,
	make_two_churches,
)


class TestAnonymizePerson(RollbackEachTest):
	def make_member(self, **values):
		return make_person(
			"_Test Hidden",
			"Member",
			nickname="Hid",
			notes="Prefers the early service",
			medical_notes="Peanuts",
			photo="https://example.com/_test_hidden.jpg",
			emails=[email("_test.hidden@example.com")],
			phones=[phone("555-010-0099")],
			addresses=[
				{
					"address": make_address("_Test Hidden Home").name,
					"address_type": ensure("Address Type", {"type": "Home"}),
				}
			],
			life_events=[
				life_event("Birth", "1980-02-03"),
				{**life_event("Baptism", "2001-04-01"), "location": "River", "notes": "With family"},
			],
			**values,
		)

	def test_the_record_stays_without_the_persons_details(self):
		person = self.make_member(user=ensure_user("_test_hidden@example.com", "Hidden"))
		address = person.addresses[0].address

		anonymize_person(person.name)

		person.reload()
		self.assertEqual(
			(person.first_name, person.full_name, person.last_name), (ANONYMIZED, ANONYMIZED, None)
		)
		for fieldname in ("nickname", "notes", "medical_notes", "photo", "user", "birth_date"):
			self.assertFalse(person.get(fieldname), fieldname)
		self.assertEqual((person.emails, person.phones, person.addresses), ([], [], []))
		self.assertEqual(
			[(row.event_type, str(row.date), row.location, row.notes) for row in person.life_events],
			[("Baptism", "2001-04-01", None, None)],
		)
		self.assertTrue(person.hide_from_directory)
		self.assertFalse(frappe.db.exists("Address", address))
		self.assertFalse(frappe.db.exists("Version", {"ref_doctype": "Person", "docname": person.name}))

	def test_records_that_name_the_person_keep_the_link_and_lose_the_copies(self):
		person = self.make_member()
		check = frappe.get_doc(
			{
				"doctype": "Background Check",
				"person": person.name,
				"check_type": ensure("Background Check Type", {"type": "_Test Check"}),
				"status": "Requested",
				"requested_on": "2026-01-01",
			}
		).insert(ignore_permissions=True)
		care = frappe.get_doc(
			{
				"doctype": "Care Request",
				"person": person.name,
				"category": ensure("Care Request Type", {"type": "_Test Care"}),
				"description": "_Test Hidden Member asked for a visit; write to _TEST.hidden@example.com.",
			}
		).insert(ignore_permissions=True)

		anonymize_person(person.name)

		self.assertEqual(
			frappe.db.get_value("Background Check", check.name, ["person", "person_name"]),
			(person.name, ANONYMIZED),
		)
		self.assertEqual(
			frappe.db.get_value("Care Request", care.name, "description"),
			"Anonymized asked for a visit; write to .",
		)

	def test_the_person_leaves_their_family_and_spouse(self):
		person = self.make_member()
		wife = make_person(
			"_Test Wife", "Member", spouse=person.name, marital_status="Married", anniversary="2005-06-01"
		)
		family = frappe.get_doc("Family", wife.family)
		make_primary_contact(person.reload())

		anonymize_person(person.name)

		self.assertEqual(
			frappe.db.get_value("Person", wife.name, ["spouse", "marital_status"], as_dict=True),
			{"spouse": None, "marital_status": "Married"},
		)
		family.reload()
		self.assertEqual(
			([row.member for row in family.members], family.primary_contact), ([wife.name], None)
		)
		self.assertFalse(frappe.db.get_value("Person", person.name, "family"))

	def test_a_family_left_empty_is_anonymized(self):
		person = self.make_member()
		family = make_family("_Test Hidden Household", person, emails=[email("_test.household@example.com")])

		anonymize_person(person.name)

		family.reload()
		self.assertEqual((family.family_name, family.emails, family.members), (ANONYMIZED, [], []))

	def test_only_a_manager_may_anonymize(self):
		person = self.make_member()
		frappe.set_user(ensure_user("_test_privacy_editor@example.com", "Editor", ["Church People Editor"]))

		self.assertRaises(frappe.PermissionError, anonymize_person, person.name)

	def test_a_manager_may_not_anonymize_another_churchs_person(self):
		_root, branch_a, branch_b = make_two_churches()
		person = self.make_member(church=branch_b)
		manager = ensure_user("_test_privacy_manager@example.com", "Manager", ["Church Manager"])
		set_user_church(manager, branch_a)
		frappe.set_user(manager)

		self.assertRaises(frappe.PermissionError, anonymize_person, person.name)


class TestPersonalDataRequests(RollbackEachTest):
	def setUp(self):
		super().setUp()
		# Frappe redacts the login's full name wherever it appears, as a portal invitation names the login.
		self.user = ensure_user("_test_gdpr@example.com", "_Test Gdpr Member")
		self.person = make_person("_Test Gdpr", "Member", user=self.user, emails=[email(self.user)])

	def test_a_data_download_holds_the_person_and_their_email_rows(self):
		data = json.loads(get_user_data(self.user))

		self.assertEqual([row["name"] for row in data["Person"]], [self.person.name])
		self.assertEqual([row["parent"] for row in data["Email Address"]], [self.person.name])

	@patch("frappe.sendmail")
	def test_a_deletion_request_anonymizes_the_person(self, _sendmail):
		request = frappe.get_doc({"doctype": "Personal Data Deletion Request", "email": self.user})
		request.insert(ignore_permissions=True)
		request.status = "Pending Approval"
		request.save(ignore_permissions=True)

		request.trigger_data_deletion()

		self.person.reload()
		self.assertEqual(
			(self.person.first_name, self.person.user, self.person.emails), (ANONYMIZED, None, [])
		)
