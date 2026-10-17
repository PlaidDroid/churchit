# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe.desk.form.load import getdoc
from frappe.desk.search import search_link
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_years, getdate, nowdate

from churchit.church_communications.newsletter import email_church, has_unsubscribed, set_subscribed
from churchit.church_people.derived import refresh_daily
from churchit.church_people.doctype.inactive_reason.inactive_reason import DECEASED
from churchit.church_people.doctype.person.person import SOURCE_IMPORT
from churchit.patches.v1_0 import set_inactive_since_from_history
from churchit.tests.helpers import ensure_user, make_person, make_primary_contact


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

	def test_display_name_is_the_name_a_person_goes_by(self):
		person = self._make_person(first_name="Robert", last_name="Smith", nickname="Bob", suffix="Jr.")
		self.assertEqual(person.display_name, "Bob Smith Jr.")
		self.assertEqual(person.full_name, "Robert Smith")

		person.nickname = None
		person.save()
		self.assertEqual(person.display_name, "Robert Smith Jr.")

	def test_display_name_with_only_first_name(self):
		self.assertEqual(self._make_person(first_name="Cher").display_name, "Cher")

	def test_link_search_finds_the_nickname_and_the_previous_last_name(self):
		person = self._make_person(
			first_name="Margaret",
			last_name="_Test Wedded",
			nickname="_Test Peggy",
			previous_last_name="_Test Maiden",
		)
		for text in ("_Test Peggy", "_Test Maiden"):
			self.assertIn(person.name, [result["value"] for result in search_link("Person", text)], text)

	def test_desk_views_of_a_person_are_logged(self):
		person = self._make_person(first_name="_Test Viewed")
		getdoc("Person", person.name)
		self.assertTrue(
			frappe.db.exists("View Log", {"reference_doctype": "Person", "reference_name": person.name})
		)

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

		refresh_daily()

		self.assertEqual(frappe.db.get_value("Person", person.name, ["age", "marriage_years"]), (30, 10))

	def test_age_cleared_without_birth_event(self):
		person = self._make_person(first_name="Ageless")
		self.assertEqual(person.age, 0)

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

	def test_every_connection_names_a_field_that_points_at_a_person(self):
		"""A connection on a field its doctype lacks counts nothing; Frappe finds the field in a child table too."""
		for link in frappe.get_meta("Person").links:
			meta = frappe.get_meta(link.link_doctype)
			tables = [frappe.get_meta(table.options) for table in meta.get_table_fields()]
			fields = [table.get_field(link.link_fieldname) for table in (meta, *tables)]
			self.assertTrue(
				any(
					field and (field.options == "Person" or field.fieldtype == "Dynamic Link")
					for field in fields
				),
				f"{link.link_doctype}.{link.link_fieldname}",
			)


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
		for reason in (DECEASED, "Moved away"):
			_ensure("Inactive Reason", {"reason": reason}, {"reason": reason})

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
		self.assertEqual(person.inactive_reason, DECEASED)
		self.assertEqual(getdate(person.inactive_since), getdate(nowdate()))

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

	def test_becoming_inactive_records_the_day(self):
		person = frappe.get_doc({"doctype": "Person", "first_name": "_Test Leaver"}).insert()
		self.assertIsNone(person.inactive_since)

		person.membership_status = "Inactive"
		person.save()
		self.assertEqual(getdate(person.inactive_since), getdate(nowdate()))

		person.membership_status = "Active"
		person.save()
		self.assertIsNone(person.inactive_since)

	def test_a_person_added_as_inactive_is_dated_too(self):
		person = frappe.get_doc(
			{"doctype": "Person", "first_name": "_Test Lapsed", "membership_status": "Inactive"}
		).insert()
		self.assertEqual(getdate(person.inactive_since), getdate(nowdate()))

	def test_saving_an_inactive_person_again_keeps_the_day(self):
		person = frappe.get_doc(
			{"doctype": "Person", "first_name": "_Test Long Gone", "membership_status": "Inactive"}
		).insert()
		frappe.db.set_value("Person", person.name, "inactive_since", "2020-01-01")
		person.reload()

		person.notes = "Still inactive."
		person.save()

		self.assertEqual(getdate(person.inactive_since), getdate("2020-01-01"))


class TestInactiveSinceFromHistory(FrappeTestCase):
	"""People already Inactive take the day of the tracked change that made them so."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		for status in ("Active", "Inactive"):
			_ensure("Member Status", {"status": status}, {"status": status})

	def set_status(self, person, status, day):
		"""Save *status* on *person* and date the tracked change *day*."""
		person.membership_status = status
		person.save(ignore_version=False)
		version = frappe.get_all(
			"Version",
			filters={"ref_doctype": "Person", "docname": person.name},
			order_by="creation desc",
			limit=1,
			pluck="name",
		)[0]
		frappe.db.set_value("Version", version, "creation", day)

	def test_the_latest_change_to_inactive_gives_the_day(self):
		person = frappe.get_doc({"doctype": "Person", "first_name": "_Test Wanderer"}).insert()
		self.set_status(person, "Inactive", "2020-01-01 10:00:00")
		self.set_status(person, "Active", "2021-01-01 10:00:00")
		self.set_status(person, "Inactive", "2022-03-04 10:00:00")
		frappe.db.set_value("Person", person.name, "inactive_since", None)

		set_inactive_since_from_history.execute()

		self.assertEqual(frappe.db.get_value("Person", person.name, "inactive_since"), getdate("2022-03-04"))

	def test_a_person_with_no_such_change_stays_undated(self):
		person = frappe.get_doc(
			{"doctype": "Person", "first_name": "_Test Undated", "membership_status": "Inactive"}
		).insert()
		frappe.db.set_value("Person", person.name, "inactive_since", None)

		set_inactive_since_from_history.execute()

		self.assertIsNone(frappe.db.get_value("Person", person.name, "inactive_since"))


class TestPersonSource(FrappeTestCase):
	"""How a person was first added, kept as it was."""

	def make_imported_person(self, first_name):
		frappe.flags.in_import = True
		try:
			return make_person(first_name)
		finally:
			frappe.flags.in_import = False

	def test_a_person_added_in_the_desk_comes_from_the_desk(self):
		self.assertEqual(make_person("_Test Typed In").source, "Desk")

	def test_a_data_import_marks_the_people_it_adds(self):
		self.assertEqual(self.make_imported_person("_Test Imported").source, SOURCE_IMPORT)

	def test_saving_never_changes_the_source(self):
		person = self.make_imported_person("_Test Imported Then Edited")
		person.notes = "Edited later in the desk."
		person.save()
		self.assertEqual(frappe.db.get_value("Person", person.name, "source"), SOURCE_IMPORT)

		person.source = "Desk"
		with self.assertRaises(frappe.CannotChangeConstantError):
			person.save()

	def test_a_person_added_before_the_source_was_recorded_keeps_it_blank(self):
		person = make_person("_Test Old Timer")
		frappe.db.set_value("Person", person.name, "source", None)
		person.reload()

		person.notes = "Saved again."
		person.save()

		self.assertIsNone(frappe.db.get_value("Person", person.name, "source"))


class TestPersonMedicalNotes(FrappeTestCase):
	"""Managers, the office and check-in volunteers read medical notes; only the first two change them."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.person = make_person("_Test Patient", medical_notes="_Test Diabetic")

	def tearDown(self):
		frappe.set_user("Administrator")

	def as_role(self, role, person=None):
		"""*person* as a user holding only *role* loads them."""
		frappe.set_user(ensure_user(f"_test_medical_{frappe.scrub(role)}@example.com", role, roles=(role,)))
		return frappe.get_doc("Person", (person or self.person).name)

	def test_who_reads_the_medical_notes(self):
		for role, notes in (
			("Church Manager", "_Test Diabetic"),
			("Church People Editor", "_Test Diabetic"),
			("Church Check-In", "_Test Diabetic"),
			("Church Staff", None),
		):
			person = self.as_role(role)
			person.apply_fieldlevel_read_permissions()
			self.assertEqual(person.get("medical_notes"), notes, role)

	def test_who_changes_the_medical_notes(self):
		for role, can_change in (
			("Church Manager", True),
			("Church People Editor", True),
			("Church Check-In", False),
		):
			person = self.as_role(role)
			self.assertEqual(
				person.has_permlevel_access_to("medical_notes", permission_type="write"), can_change
			)

	def test_the_office_saves_a_change(self):
		person = self.as_role(
			"Church People Editor", make_person("_Test Insulin", medical_notes="_Test Diabetic")
		)
		person.medical_notes = "_Test Diabetic, carries insulin"
		person.save()
		self.assertEqual(
			frappe.db.get_value("Person", person.name, "medical_notes"), "_Test Diabetic, carries insulin"
		)


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


class TestPersonDerivedFields(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		for event_type in ("Birth", "Death"):
			_ensure("Life Event Type", {"type": event_type}, {"type": event_type})
		settings = frappe.get_single("People Settings")
		settings.adult_age = 18
		settings.save(ignore_permissions=True)

	def _make_person(self, first_name, *life_events, **values):
		return frappe.get_doc(
			{"doctype": "Person", "first_name": first_name, "life_events": list(life_events), **values}
		).insert(ignore_permissions=True)

	def _stored(self, person, *fields):
		return frappe.db.get_value("Person", person.name, fields, as_dict=True)

	def test_the_birth_event_gives_the_birth_date(self):
		person = self._make_person("_Test Born", {"event_type": "Birth", "date": "1990-05-01"})

		self.assertEqual(
			self._stored(person, "birth_date", "birth_month", "birth_day"),
			{"birth_date": getdate("1990-05-01"), "birth_month": 5, "birth_day": 1},
		)

	def test_a_birthday_without_a_year_gives_no_birth_date_or_age(self):
		person = self._make_person(
			"_Test Yearless", {"event_type": "Birth", "date": "1990-07-04", "year_unknown": 1}
		)

		self.assertEqual(
			self._stored(person, "birth_date", "birth_month", "birth_day", "age"),
			{"birth_date": None, "birth_month": 7, "birth_day": 4, "age": 0},
		)

	def test_removing_the_birth_event_clears_the_birth_fields(self):
		person = self._make_person("_Test Unborn", {"event_type": "Birth", "date": "1990-05-01"})
		person.life_events = []
		person.save()

		self.assertEqual(
			self._stored(person, "birth_date", "birth_month", "birth_day", "age"),
			{"birth_date": None, "birth_month": 0, "birth_day": 0, "age": 0},
		)

	def test_saving_someone_with_no_known_age_records_no_age_change(self):
		"""An unknown age stays 0 in memory as in the column, so a save logged a change from 0 to nothing."""
		person = self._make_person("_Test Quiet")
		person.alergies = "_Test Pollen"
		person.save(ignore_version=False)

		versions = frappe.get_all(
			"Version", filters={"ref_doctype": "Person", "docname": person.name}, pluck="data"
		)
		changed = [change[0] for data in versions for change in frappe.parse_json(data).get("changed", [])]
		self.assertIn("alergies", changed)
		self.assertNotIn("age", changed)

	def test_a_death_event_marks_the_person_deceased_and_stops_their_age(self):
		person = self._make_person("_Test Mortal", {"event_type": "Birth", "date": "1950-03-01"})
		person.append("life_events", {"event_type": "Death", "date": "2000-02-28"})
		person.save()

		self.assertEqual(
			self._stored(person, "is_deceased", "deceased_date", "age"),
			{"is_deceased": 1, "deceased_date": getdate("2000-02-28"), "age": 49},
		)

	def test_without_a_household_role_the_life_stage_follows_the_age(self):
		child = self._make_person("_Test Young", {"event_type": "Birth", "date": add_years(nowdate(), -10)})
		adult = self._make_person("_Test Grown", {"event_type": "Birth", "date": add_years(nowdate(), -18)})
		unknown = self._make_person("_Test Unknown Age")

		self.assertEqual((child.life_stage, adult.life_stage, unknown.life_stage), ("Child", "Adult", None))

	def test_the_household_role_comes_before_the_age(self):
		family = frappe.get_doc({"doctype": "Family", "family_name": "_Test Role Household"}).insert(
			ignore_permissions=True
		)
		person = self._make_person(
			"_Test Dependent", {"event_type": "Birth", "date": "1990-05-01"}, family=family.name
		)
		frappe.db.set_value(
			"Family Members", {"parent": family.name, "member": person.name}, "household_role", "Child"
		)

		person.reload()
		person.save()

		self.assertEqual(person.life_stage, "Child")
