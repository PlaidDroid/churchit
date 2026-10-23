# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import json
from unittest.mock import patch

import frappe
from frappe.utils import add_days, add_years, getdate, today
from frappe.website.doctype.personal_data_download_request.personal_data_download_request import (
	get_user_data,
)

from churchit.church_communications.doctype.communication_purpose.communication_purpose import (
	create_communication_purposes,
)
from churchit.church_communications.sending import Delivery, Recipient, get_people_sent
from churchit.church_foundations.church_access import set_user_church
from churchit.church_people.privacy import ANONYMIZED, anonymize_person, remove_due_personal_data
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
		for fieldname in ("nickname", "medical_notes", "photo", "user", "birth_date"):
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

	def test_the_login_is_disabled_so_signing_in_adds_no_one(self):
		user = ensure_user("_test_hidden@example.com", "Hidden")
		person = self.make_member(user=user)
		frappe.set_user(ensure_user("_test_privacy_manager@example.com", "Manager", ["Church Manager"]))

		anonymize_person(person.name)

		self.assertEqual(frappe.db.get_value("User", user, "enabled"), 0)

	def test_messages_to_the_person_lose_their_address_number_and_text_and_still_count(self):
		person = self.make_member()
		create_communication_purposes()
		email = frappe.get_doc(
			{"doctype": "Church Email", "subject": "_Test Privacy", "message": "Dear {{ first_name }}"}
		).insert()
		text = frappe.get_doc(
			{"doctype": "Church Email", "channel": "Text Message", "text_message": "Hi {{ first_name }}"}
		).insert()
		Delivery(email).record(
			Recipient(person.name, "_test.hidden@example.com"),
			frappe._dict(subject="_Test Privacy", message="Dear _Test Hidden"),
		)
		Delivery(text).record(Recipient(person.name, "555-010-0099"), frappe._dict(text="Hi _Test Hidden"))

		anonymize_person(person.name)

		self.assertEqual(
			frappe.get_all(
				"Communication",
				filters={"reference_doctype": "Person", "reference_name": person.name},
				fields=["recipients", "phone_no", "content"],
			),
			[{"recipients": None, "phone_no": None, "content": ""}] * 2,
		)
		self.assertEqual(
			(get_people_sent(email.name), get_people_sent(text.name)), ({person.name}, {person.name})
		)

	def test_no_copy_of_a_comment_or_message_is_left_for_the_list_view(self):
		person = self.make_member()
		person.add_comment("Comment", "_Test Hidden asked us to pray about the peanuts.")
		frappe.get_doc(
			{
				"doctype": "Communication",
				"subject": "_Test Privacy",
				"content": "Dear _Test Hidden, how are the peanuts?",
				"reference_doctype": "Person",
				"reference_name": person.name,
			}
		).insert(ignore_permissions=True)
		self.assertIn("peanuts", frappe.db.get_value("Person", person.name, "_comments"))

		anonymize_person(person.name)

		self.assertEqual(json.loads(frappe.db.get_value("Person", person.name, "_comments")), [])

	def test_attachments_of_the_person_and_of_records_about_them_are_deleted(self):
		person = self.make_member()
		other = make_person("_Test Cared For", "Member")
		check = frappe.get_doc(
			{
				"doctype": "Background Check",
				"person": person.name,
				"check_type": ensure("Background Check Type", {"type": "_Test Check"}),
				"status": "Requested",
				"requested_on": "2026-01-01",
			}
		).insert(ignore_permissions=True)
		# The person is only the caregiver here; the request and its files are about someone else.
		care = frappe.get_doc(
			{
				"doctype": "Care Request",
				"person": other.name,
				"caregiver": person.name,
				"category": ensure("Care Request Type", {"type": "_Test Care"}),
				"description": "Meals for a week.",
			}
		).insert(ignore_permissions=True)
		files = {
			"person": attach("Person", person.name, "letter"),
			"report": attach("Background Check", check.name, "report", field="report"),
			"care": attach("Care Request", care.name, "meal_plan"),
		}

		anonymize_person(person.name)

		self.assertEqual(
			{key: bool(frappe.db.exists("File", file)) for key, file in files.items()},
			{"person": False, "report": False, "care": True},
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

	def test_a_family_that_keeps_members_loses_the_persons_first_name(self):
		person = self.make_member()
		family = make_family("Member - _Test Hidden", person, make_person("_Test Wife", "Member"))

		anonymize_person(person.name)

		self.assertEqual(frappe.db.get_value("Family", family.name, "family_name"), "Member")

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

	def test_the_pickup_restrictions_on_the_person_as_a_child_are_deleted(self):
		person = self.make_member()
		restriction = frappe.get_doc(
			{
				"doctype": "Pickup Restriction",
				"child": person.name,
				"restricted_name": "_Test Someone",
				"description": "_Test Call the office",
			}
		).insert(ignore_permissions=True)

		anonymize_person(person.name)

		self.assertFalse(frappe.db.exists("Pickup Restriction", restriction.name))


class TestPersonalDataRequests(RollbackEachTest):
	def setUp(self):
		super().setUp()
		# Frappe redacts the login's full name wherever it appears, as a portal invitation names the login.
		self.user = ensure_user("_test_gdpr@example.com", "_Test Gdpr Member")
		self.person = make_person("_Test Gdpr", "Member", user=self.user, emails=[email(self.user)])

	@patch("frappe.sendmail")
	def delete_account(self, _sendmail):
		request = frappe.get_doc({"doctype": "Personal Data Deletion Request", "email": self.user})
		request.insert(ignore_permissions=True)
		request.status = "Pending Approval"
		request.save(ignore_permissions=True)
		request.trigger_data_deletion()

	def make_spouse_and_family_sharing_the_address(self):
		"""A spouse holding the member's address as Shared From, and a family holding it too."""
		spouse = make_person(
			"_Test Gdpr Spouse",
			"Member",
			emails=[email(self.user, shared_from_type="Person", shared_from=self.person.name)],
		)
		return spouse, make_family("_Test Gdpr Household", spouse, emails=[email(self.user)])

	def test_a_data_download_holds_the_person_and_no_one_elses_rows(self):
		spouse, family = self.make_spouse_and_family_sharing_the_address()

		data = json.loads(get_user_data(self.user))

		self.assertEqual([row["name"] for row in data["Person"]], [self.person.name])
		self.assertEqual(
			{row["parent"] for row in data.get("Email Address", [])} & {spouse.name, family.name}, set()
		)

	def test_a_deletion_request_anonymizes_the_person(self):
		self.delete_account()

		self.person.reload()
		self.assertEqual(
			(self.person.first_name, self.person.user, self.person.emails), (ANONYMIZED, None, [])
		)

	def test_a_deletion_request_leaves_other_peoples_rows_of_the_address(self):
		spouse, family = self.make_spouse_and_family_sharing_the_address()

		self.delete_account()

		for doctype, name in (("Person", spouse.name), ("Family", family.name)):
			self.assertEqual(
				[row.email_address for row in frappe.get_doc(doctype, name).emails], [self.user], doctype
			)

	def test_a_deletion_request_clears_the_address_from_giving_records(self):
		fund = ensure("Fund", {"fund": "_Test Privacy Fund"})
		collection = frappe.get_doc(
			{
				"doctype": "Collection",
				"date": today(),
				"expected_total": 10,
				"donations": [
					{
						"person": self.person.name,
						"fund": fund,
						"payment_type": ensure("Payment Type", {"type": "_Test Privacy Cash"}),
						"amount": 10,
					}
				],
			}
		).insert(ignore_permissions=True)
		statement = frappe.get_doc(
			{
				"doctype": "Giving Statement",
				"person": self.person.name,
				"from_date": "2029-01-01",
				"to_date": "2029-12-31",
			}
		).insert(ignore_permissions=True)
		gift = frappe.get_doc(
			{"doctype": "Online Donation", "amount": 25, "fund": fund, "person": self.person.name}
		).insert(ignore_permissions=True)
		copies = (
			("Donation", collection.donations[0].name, "person_email"),
			("Giving Statement", statement.name, "email"),
			("Online Donation", gift.name, "email"),
		)
		for doctype, name, fieldname in copies:
			frappe.db.set_value(doctype, name, fieldname, self.user)

		self.delete_account()

		for doctype, name, fieldname in copies:
			self.assertFalse(frappe.db.get_value(doctype, name, fieldname), doctype)


class TestRemoveDuePersonalData(RollbackEachTest):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		for status in ("Active", "Inactive"):
			ensure("Member Status", {"status": status})

	def setUp(self):
		super().setUp()
		frappe.db.set_single_value("People Settings", "remove_inactive_after_years", 5)
		self.boundary = add_years(today(), -5)
		self.due = self.make_inactive("Due", self.boundary)
		self.not_yet = self.make_inactive("Not Yet", add_days(self.boundary, 1))
		self.deceased = self.make_inactive("Deceased", add_years(self.boundary, -10))
		frappe.db.set_value("Person", self.deceased, "is_deceased", 1)

	def make_inactive(self, first_name, since):
		person = make_person(f"_Test Retention {first_name}", "Person", membership_status="Inactive").name
		frappe.db.set_value("Person", person, "inactive_since", since)
		return person

	def run_job(self):
		with patch.object(frappe.db, "commit"):
			remove_due_personal_data()
		return {
			person: frappe.db.get_value("Person", person, "first_name")
			for person in (self.due, self.not_yet, self.deceased)
		}

	def test_zero_years_removes_nothing(self):
		frappe.db.set_single_value("People Settings", "remove_inactive_after_years", 0)

		self.assertNotIn(ANONYMIZED, self.run_job().values())
		self.assertEqual(due_rows({}), [])

	def test_a_person_inactive_exactly_the_years_set_is_removed_and_a_day_less_is_kept(self):
		names = self.run_job()

		self.assertEqual(names[self.due], ANONYMIZED)
		self.assertEqual(names[self.not_yet], "_Test Retention Not Yet")
		comments = frappe.get_all(
			"Comment", filters={"reference_doctype": "Person", "reference_name": self.due}, pluck="content"
		)
		self.assertTrue(any("Inactive since" in comment for comment in comments))

	def test_people_who_have_died_are_kept(self):
		self.assertEqual(self.run_job()[self.deceased], "_Test Retention Deceased")

	def test_the_report_lists_who_is_due_by_a_date_with_their_removal_date(self):
		rows = {row.person: row.removal_date for row in due_rows({"due_by": add_days(today(), 1)})}

		self.assertEqual(rows.get(self.due), getdate(today()))
		self.assertEqual(rows.get(self.not_yet), getdate(add_days(today(), 1)))
		self.assertNotIn(self.deceased, rows)


def attach(doctype, name, title, field=None):
	"""A File attached to the record, kept at a web address so nothing is written to disk."""
	return (
		frappe.get_doc(
			{
				"doctype": "File",
				"file_url": f"https://example.com/_test_{title}.pdf",
				"attached_to_doctype": doctype,
				"attached_to_name": name,
				"attached_to_field": field,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def due_rows(filters):
	from churchit.church_people.report.personal_data_due_for_removal.personal_data_due_for_removal import (
		execute,
	)

	return execute(filters)[1]
