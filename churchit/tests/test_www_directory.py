# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""The member's /directory page: the living people of their church who do not hide, with only what each one shares."""

import frappe
from frappe.utils import get_html_for_route

from churchit.church_people.derived import BIRTH
from churchit.tests.helpers import (
	RollbackEachTest,
	ensure,
	ensure_user,
	make_address,
	make_person,
	make_primary_contact,
	make_two_churches,
)
from churchit.www.directory import get_context

ROBERT_EMAIL = "_test_directory_robert@example.com"
MARY_EMAIL = "_test_directory_mary@example.com"
ROBERT_PHONE = "555 010 0101"
MARY_PHONE = "555 010 0102"
JOHNSONS = "_Test Johnson, Robert & Mary"


def page_context():
	context = frappe._dict()
	get_context(context)
	return context


def born(date, year_unknown=0):
	return [{"event_type": BIRTH, "date": date, "year_unknown": year_unknown}]


def make_sharer(*args, **values):
	"""make_person sharing only what *values* say, whatever People Settings gives new people."""
	shares = {f"share_{detail}": 0 for detail in ("email", "phone", "address", "birthday")}
	return make_person(*args, **{**shares, **values})


class TestDirectoryPage(RollbackEachTest):
	"""In one church: the member, the Johnsons, David Thompson, someone hidden and someone who died. A stranger
	belongs to another church."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure("Life Event Type", {"type": BIRTH})
		_root, church, other_church = make_two_churches()
		frappe.db.set_single_value("People Settings", "member_directory", 1)
		cls.user = ensure_user("_test_directory_member@example.com", "Member")
		make_sharer("_Test Directory", "Member", church=church, user=cls.user)

		family = frappe.get_doc({"doctype": "Family", "family_name": "_Test Johnson - Robert"})
		family.insert(ignore_permissions=True)
		mary = make_sharer(
			"Mary",
			"_Test Johnson",
			church=church,
			family=family.name,
			photo="/files/_test_directory_mary.png",
			emails=[{"email_address": MARY_EMAIL}],
			phones=[{"phone_number": MARY_PHONE}],
			life_events=born("1982-06-09"),
			share_phone=1,
			share_address=1,
			share_birthday=1,
		)
		make_primary_contact(
			make_sharer(
				"Robert",
				"_Test Johnson",
				church=church,
				family=family.name,
				photo="/private/files/_test_directory_robert.png",
				emails=[{"email_address": ROBERT_EMAIL}],
				phones=[{"phone_number": ROBERT_PHONE}],
				life_events=born("1980-03-03"),
				share_email=1,
				share_birthday=1,
			)
		)
		address = make_address("_Test Directory Home", address_line1="12 Elm Street")
		family.reload()
		family.append("addresses", {"address": address.name, "is_mailing_address": 1})
		family.save(ignore_permissions=True)
		cls.mary = mary.name

		make_sharer(
			"David",
			"_Test Thompson",
			church=church,
			life_events=born("1900-07-04", year_unknown=1),
			share_birthday=1,
		)
		make_sharer("_Test Hidden", "Person", church=church, hide_from_directory=1, share_email=1)
		late = make_sharer("_Test Late", "Person", church=church, share_email=1)
		frappe.db.set_value("Person", late.name, "is_deceased", 1)
		make_sharer("_Test Stranger", "Person", church=other_church, share_email=1)
		make_sharer("_Test Guest", "Person", church=church, membership="Visitor", share_email=1)
		make_sharer("_Test Moved", "Away", church=church, membership_status="Inactive", share_email=1)

	def setUp(self):
		super().setUp()
		frappe.set_user(self.user)

	def serve(self):
		self.addCleanup(setattr, frappe.local, "request", getattr(frappe.local, "request", None))
		return get_html_for_route("directory")

	def entries(self):
		return {entry.title: entry for entry in page_context().entries}

	def test_guests_are_sent_to_login(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.Redirect):
			page_context()
		self.assertEqual(frappe.local.flags.redirect_location, "/login?redirect-to=/directory")

	def test_the_page_is_not_there_until_people_settings_turns_it_on(self):
		frappe.db.set_single_value("People Settings", "member_directory", 0)

		with self.assertRaises(frappe.PageDoesNotExistError):
			page_context()

	def test_it_lists_the_living_people_of_the_members_church_who_do_not_hide(self):
		self.assertEqual(list(self.entries()), [JOHNSONS, "_Test Thompson, David", "Member, _Test Directory"])

	def test_a_family_is_one_entry_with_only_what_each_person_shares(self):
		johnsons = self.entries()[JOHNSONS]

		self.assertEqual(johnsons.emails, [ROBERT_EMAIL])
		self.assertEqual(johnsons.phones, [MARY_PHONE])
		self.assertEqual(johnsons.addresses, ["12 Elm Street, Springfield"])
		self.assertEqual(johnsons.birthdays, ["Robert Mar 3", "Mary Jun 9"])

	def test_someone_who_shares_nothing_shows_by_name_only(self):
		member = self.entries()["Member, _Test Directory"]

		self.assertEqual((member.emails, member.phones, member.addresses, member.birthdays), ([], [], [], []))

	def test_a_birthday_shows_its_day_and_month_even_when_the_year_is_unknown(self):
		self.assertEqual(self.entries()["_Test Thompson, David"].birthdays, ["David Jul 4"])

	def test_a_private_photo_gives_way_to_one_every_member_may_see(self):
		self.assertEqual(self.entries()[JOHNSONS].photo, "/files/_test_directory_mary.png")

		frappe.db.set_value("Person", self.mary, "photo", "/private/files/_test_directory_mary.png")
		self.assertIsNone(self.entries()[JOHNSONS].photo)

	def test_the_page_shows_nothing_a_person_keeps_to_themselves(self):
		html = self.serve()

		self.assertIn('id="directory-search"', html)
		self.assertIn(ROBERT_EMAIL, html)
		for kept in (MARY_EMAIL, ROBERT_PHONE, "_Test Hidden", "_Test Late", "_Test Stranger"):
			self.assertNotIn(kept, html)

	def test_an_account_with_no_person_sees_a_note(self):
		frappe.set_user(ensure_user("_test_directory_nobody@example.com", "Nobody"))

		self.assertEqual(page_context().entries, [])
		self.assertIn("not linked to a member record", self.serve())
