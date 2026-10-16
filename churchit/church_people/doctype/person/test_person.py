# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_years, getdate, nowdate

from churchit.church_communications.newsletter import email_church, has_unsubscribed, set_subscribed
from churchit.church_people.doctype.person.person import refresh_ages
from churchit.tests.helpers import make_primary_contact


def _ensure(doctype, filters, values):
	name = frappe.db.exists(doctype, filters)
	if name:
		return name
	return frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True).name


class TestPerson(FrappeTestCase):
	def _make_person(self, **values):
		return frappe.get_doc({"doctype": "Person", **values}).insert(ignore_permissions=True)

	def test_full_name_built_from_first_and_last(self):
		person = self._make_person(first_name="John", last_name="Doe")
		self.assertEqual(person.full_name, "John Doe")

	def test_full_name_with_only_first_name(self):
		person = self._make_person(first_name="Madonna")
		self.assertEqual(person.full_name, "Madonna")

	def test_age_computed_from_birth_life_event(self):
		_ensure("Life Event Type", {"type": "Birth"}, {"type": "Birth"})
		person = frappe.get_doc({"doctype": "Person", "first_name": "Aged"})
		person.append("life_events", {"event_type": "Birth", "date": add_years(nowdate(), -30)})
		person.insert(ignore_permissions=True)
		self.assertEqual(person.age, 30)

	def test_daily_refresh_catches_up_a_stale_age_and_marriage(self):
		"""Age and years married are stored on save, so a birthday alone left them a year behind."""
		_ensure("Life Event Type", {"type": "Birth"}, {"type": "Birth"})
		person = self._make_person(
			first_name="_Test Stale",
			anniversary=add_years(nowdate(), -10),
			life_events=[{"event_type": "Birth", "date": add_years(nowdate(), -30)}],
		)
		frappe.db.set_value("Person", person.name, {"age": 29, "marriage_years": 9}, update_modified=False)

		refresh_ages()

		self.assertEqual(frappe.db.get_value("Person", person.name, ["age", "marriage_years"]), (30, 10))

	def test_age_cleared_without_birth_event(self):
		person = self._make_person(first_name="Ageless")
		self.assertIsNone(person.age)

	def test_setting_family_adds_person_to_family_members(self):
		family = frappe.get_doc({"doctype": "Family", "family_name": "Smith Household"}).insert(
			ignore_permissions=True
		)
		person = self._make_person(first_name="Sam", last_name="Smith", family=family.name)

		family.reload()
		members = [m.member for m in family.members]
		self.assertIn(person.name, members)

	def test_leaving_the_family_clears_its_primary_contact(self):
		family = frappe.get_doc({"doctype": "Family", "family_name": "Contact Household"}).insert(
			ignore_permissions=True
		)
		person = make_primary_contact(
			self._make_person(first_name="Head", last_name="Honcho", family=family.name)
		)
		self.assertTrue(person.is_primary_contact)

		person.family = None
		person.save(ignore_permissions=True)
		self.assertFalse(person.is_primary_contact)
		self.assertIsNone(frappe.db.get_value("Family", family.name, "primary_contact"))

	def test_moving_to_another_family_leaves_the_old_one(self):
		old = frappe.get_doc({"doctype": "Family", "family_name": "Left Household"}).insert(
			ignore_permissions=True
		)
		new = frappe.get_doc({"doctype": "Family", "family_name": "Joined Household"}).insert(
			ignore_permissions=True
		)
		person = self._make_person(first_name="Mover", last_name="Along", family=old.name)

		person.family = new.name
		person.save(ignore_permissions=True)
		# Saving the old family used to pull the person back into it.
		old.reload()
		old.save(ignore_permissions=True)

		self.assertNotIn(person.name, [m.member for m in old.members])
		self.assertEqual(frappe.db.get_value("Person", person.name, "family"), new.name)

	def test_deleting_person_removes_them_from_family(self):
		family = frappe.get_doc({"doctype": "Family", "family_name": "Departing Household"}).insert(
			ignore_permissions=True
		)
		person = self._make_person(first_name="Gone", last_name="Soon", family=family.name)
		family.reload()
		self.assertIn(person.name, [m.member for m in family.members])

		person.delete()

		family.reload()
		self.assertNotIn(person.name, [m.member for m in family.members])

	def test_deleting_person_whose_family_is_already_gone(self):
		# A bulk delete removes the Family first; the Person still has to go.
		family = frappe.get_doc({"doctype": "Family", "family_name": "Vanished Household"}).insert(
			ignore_permissions=True
		)
		person = self._make_person(first_name="Orphan", last_name="Record", family=family.name)
		frappe.delete_doc("Family", family.name, force=True, ignore_permissions=True)

		person.delete()

		self.assertFalse(frappe.db.exists("Person", person.name))

	def test_spouse_link_is_reciprocated(self):
		wife = self._make_person(first_name="Jane", last_name="Doe", gender="Female")
		husband = self._make_person(first_name="John", last_name="Doe", gender="Male")

		husband.spouse = wife.name
		husband.marital_status = "Married"
		husband.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("Person", wife.name, "spouse"), husband.name)
		self.assertEqual(frappe.db.get_value("Person", wife.name, "marital_status"), "Married")

	def test_anniversary_and_marriage_years_shared_with_spouse(self):
		wife = self._make_person(first_name="Amy", last_name="Years", anniversary=add_years(nowdate(), -10))
		husband = self._make_person(
			first_name="Al", last_name="Years", spouse=wife.name, marital_status="Married"
		)

		# The husband had no anniversary, so he takes the wife's.
		self.assertEqual(getdate(husband.anniversary), getdate(wife.anniversary))
		self.assertEqual(husband.marriage_years, 10)
		self.assertEqual(frappe.db.get_value("Person", wife.name, "marriage_years"), 10)

	def test_divorce_ends_the_marriage_on_both_sides(self):
		wife = self._make_person(first_name="Ann", last_name="Split", gender="Female")
		husband = self._make_person(
			first_name="Bob",
			last_name="Split",
			gender="Male",
			spouse=wife.name,
			marital_status="Married",
			anniversary="2001-01-01",
		)

		husband.marital_status = "Divorced"
		husband.save(ignore_permissions=True)

		self.assertIsNone(husband.spouse)
		self.assertIsNone(husband.anniversary)
		wife_values = frappe.db.get_value("Person", wife.name, ["spouse", "marital_status", "anniversary"])
		self.assertEqual(wife_values, (None, "Divorced", None))

	def test_a_widow_keeps_the_spouse_and_anniversary(self):
		wife = self._make_person(first_name="Wanda", gender="Female")
		husband = self._make_person(
			first_name="Walt",
			gender="Male",
			spouse=wife.name,
			marital_status="Married",
			anniversary="1980-05-05",
		)
		wife = frappe.get_doc("Person", wife.name)

		wife.marital_status = "Widowed"
		wife.save(ignore_permissions=True)
		husband.reload()
		husband.notes = "Saving the late husband leaves the widow alone."
		husband.save(ignore_permissions=True)

		self.assertEqual(wife.spouse, husband.name)
		self.assertEqual(str(wife.anniversary), "1980-05-05")
		self.assertEqual(frappe.db.get_value("Person", wife.name, "marital_status"), "Widowed")
		self.assertEqual(frappe.db.get_value("Person", husband.name, "spouse"), wife.name)

	def test_changing_spouse_unlinks_the_previous_one(self):
		first = self._make_person(first_name="First", last_name="Wife", gender="Female")
		second = self._make_person(first_name="Second", last_name="Wife", gender="Female")
		husband = self._make_person(
			first_name="Fickle", gender="Male", spouse=first.name, marital_status="Married"
		)

		husband.spouse = second.name
		husband.save(ignore_permissions=True)

		self.assertIsNone(frappe.db.get_value("Person", first.name, "spouse"))
		self.assertEqual(frappe.db.get_value("Person", second.name, "spouse"), husband.name)

	def test_deleting_a_person_unlinks_their_spouse(self):
		wife = self._make_person(first_name="Widow", gender="Female")
		husband = self._make_person(
			first_name="Late", gender="Male", spouse=wife.name, marital_status="Married"
		)

		husband.delete()

		self.assertIsNone(frappe.db.get_value("Person", wife.name, "spouse"))

	def _members(self, family_name):
		return {m.member for m in frappe.get_doc("Family", family_name).members}

	def test_marrying_without_a_family_starts_one(self):
		wife = self._make_person(first_name="Nora", last_name="Newlywed", gender="Female")
		husband = self._make_person(
			first_name="Ned", last_name="Newlywed", gender="Male", spouse=wife.name, marital_status="Married"
		)

		self.assertEqual(frappe.db.get_value("Family", husband.family, "family_name"), "Newlywed - Ned")
		self.assertTrue(husband.is_primary_contact)
		self.assertEqual(frappe.db.get_value("Person", wife.name, "family"), husband.family)
		self.assertEqual(self._members(husband.family), {husband.name, wife.name})

	def test_marrying_into_a_family_joins_it(self):
		family = frappe.get_doc({"doctype": "Family", "family_name": "Joining - Jo"}).insert(
			ignore_permissions=True
		)
		wife = make_primary_contact(self._make_person(first_name="Jo", gender="Female", family=family.name))
		husband = self._make_person(
			first_name="Jim", gender="Male", spouse=wife.name, marital_status="Married"
		)

		self.assertEqual(husband.family, family.name)
		self.assertFalse(husband.is_primary_contact)
		self.assertEqual(self._members(family.name), {husband.name, wife.name})

	def test_marrying_brings_a_spouse_without_a_family_in(self):
		family = frappe.get_doc({"doctype": "Family", "family_name": "Bringing - Bo"}).insert(
			ignore_permissions=True
		)
		husband = make_primary_contact(self._make_person(first_name="Bo", gender="Male", family=family.name))
		wife = self._make_person(first_name="Bea", gender="Female")
		husband.reload()
		husband.spouse = wife.name
		husband.marital_status = "Married"
		husband.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("Person", wife.name, "family"), family.name)
		labels = {
			m.member: m.relationship_to_primary_contact for m in frappe.get_doc("Family", family.name).members
		}
		self.assertEqual(labels, {husband.name: "Husband", wife.name: "Wife"})

	def test_marrying_across_two_families_moves_no_one(self):
		his = frappe.get_doc({"doctype": "Family", "family_name": "His"}).insert(ignore_permissions=True)
		hers = frappe.get_doc({"doctype": "Family", "family_name": "Hers"}).insert(ignore_permissions=True)
		wife = self._make_person(first_name="Her", gender="Female", family=hers.name)
		husband = self._make_person(first_name="Him", gender="Male", family=his.name)
		husband.spouse = wife.name
		husband.marital_status = "Married"
		husband.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("Person", wife.name, "family"), hers.name)
		self.assertEqual(self._members(his.name), {husband.name})

	def test_new_family_from_person_makes_them_its_primary_contact(self):
		person = self._make_person(first_name="Founder", last_name="Fam")
		person.new_family_from_person()

		person.reload()
		self.assertTrue(person.family)
		self.assertTrue(person.is_primary_contact)
		self.assertEqual(frappe.db.get_value("Family", person.family, "family_name"), "Fam - Founder")

	def test_new_family_from_person_joins_an_existing_family_of_the_same_name(self):
		existing = frappe.get_doc({"doctype": "Family", "family_name": "Twin - Joiner"}).insert(
			ignore_permissions=True
		)
		person = self._make_person(first_name="Joiner", last_name="Twin")
		frappe.local.message_log = []
		person.new_family_from_person()

		person.reload()
		self.assertEqual(person.family, existing.name)
		self.assertFalse(person.is_primary_contact)
		messages = " ".join(str(message) for message in frappe.local.message_log)
		self.assertIn(f"/family/{existing.name}", messages)

	def test_invite_to_portal_requires_an_email_address(self):
		person = self._make_person(first_name="Unreachable")
		_ensure(
			"Email Account",
			{"enable_outgoing": 1, "default_outgoing": 1},
			{
				"email_account_name": "_Test Outgoing",
				"email_id": "_test_outgoing@example.com",
				"service": "GMail",
				"enable_outgoing": 1,
				"default_outgoing": 1,
				"no_smtp_authentication": 1,
			},
		)
		with self.assertRaises(frappe.ValidationError):
			person.invite_to_portal()

	def test_user_dashboard_data_links_the_person_doctype(self):
		from churchit.church_people.doctype.person.person import get_user_dashboard_data

		data = get_user_dashboard_data({"transactions": []})
		self.assertIn({"label": "Church", "items": ["Person"]}, data["transactions"])
		self.assertEqual(data["non_standard_fieldnames"]["Person"], "user")


class TestPersonMembership(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		for status in ("Active", "Inactive"):
			_ensure("Member Status", {"status": status}, {"status": status})
		for membership_type in ("Member", "Contact"):
			_ensure("Membership Type", {"type": membership_type}, {"type": membership_type})
		for event_type in ("Membership", "Death"):
			_ensure("Life Event Type", {"type": event_type}, {"type": event_type})

	def test_a_new_person_is_active(self):
		person = frappe.get_doc({"doctype": "Person", "first_name": "_Test Newcomer"}).insert()
		self.assertEqual(person.membership_status, "Active")

	def test_a_membership_event_makes_the_person_a_member(self):
		person = frappe.get_doc({"doctype": "Person", "first_name": "_Test Joiner"}).insert()
		person.append("life_events", {"event_type": "Membership", "date": nowdate()})
		person.save()
		self.assertEqual(person.membership, "Member")

	def test_an_existing_membership_event_leaves_a_changed_membership_alone(self):
		person = frappe.get_doc(
			{
				"doctype": "Person",
				"first_name": "_Test Former",
				"life_events": [{"event_type": "Membership", "date": "2010-01-01"}],
			}
		).insert()
		person.membership = "Contact"
		person.save()
		self.assertEqual(person.membership, "Contact")

	def test_a_death_event_widows_the_spouse(self):
		wife = frappe.get_doc(
			{"doctype": "Person", "first_name": "_Test Survivor", "gender": "Female"}
		).insert()
		husband = frappe.get_doc(
			{
				"doctype": "Person",
				"first_name": "_Test Late",
				"spouse": wife.name,
				"marital_status": "Married",
			}
		).insert()

		husband.append("life_events", {"event_type": "Death", "date": nowdate()})
		husband.save()

		self.assertEqual(frappe.db.get_value("Person", wife.name, "marital_status"), "Widowed")
		self.assertEqual(frappe.db.get_value("Person", wife.name, "spouse"), husband.name)

	def test_a_death_event_makes_the_person_inactive(self):
		person = frappe.get_doc({"doctype": "Person", "first_name": "_Test Departed"}).insert()
		person.append("life_events", {"event_type": "Death", "date": nowdate()})
		person.save()
		self.assertEqual(person.membership_status, "Inactive")
		self.assertEqual(person.inactive_reason, "Deceased")

	def test_the_inactive_reason_goes_when_the_person_is_active_again(self):
		person = frappe.get_doc(
			{
				"doctype": "Person",
				"first_name": "_Test Returner",
				"membership_status": "Inactive",
				"inactive_reason": "Moved away",
			}
		).insert()
		person.membership_status = "Active"
		person.save()
		self.assertIsNone(person.inactive_reason)


class TestPersonChurchEmails(FrappeTestCase):
	def test_the_office_can_resubscribe_someone_who_unsubscribed(self):
		person = frappe.get_doc(
			{
				"doctype": "Person",
				"first_name": "_Test Resubscribe",
				"emails": [{"email_address": "_test_resubscribe@example.com", "is_primary": 1}],
			}
		).insert(ignore_permissions=True)
		set_subscribed("_test_resubscribe@example.com", email_church(person.church), False)
		person.run_method("onload")
		self.assertTrue(person.get_onload().has_unsubscribed)

		person.resubscribe_to_church_emails()

		self.assertFalse(has_unsubscribed(person))
