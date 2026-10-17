# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_years, getdate, nowdate

from churchit.church_people.derived import (
	ADULT,
	CHILD,
	get_derived_values,
	life_stage_for,
	refresh_daily,
)
from churchit.church_people.membership import living_people_filter
from churchit.patches.v1_0 import derive_birth_death_and_life_stage
from churchit.tests.helpers import ensure, make_person


def row(event_type, date=None, year_unknown=0):
	return frappe._dict(event_type=event_type, date=date, year_unknown=year_unknown)


def set_adult_age(age):
	settings = frappe.get_single("People Settings")
	settings.adult_age = age
	settings.save(ignore_permissions=True)


class TestDerivedValues(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		set_adult_age(18)

	def test_the_first_dated_birth_gives_the_birth_fields_and_age(self):
		born = getdate(add_years(nowdate(), -30))
		values = get_derived_values([row("Birth"), row("Birth", born), row("Birth", "1900-01-01")], None)

		self.assertEqual(values["birth_date"], born)
		self.assertEqual((values["birth_month"], values["birth_day"]), (born.month, born.day))
		self.assertEqual(values["age"], 30)
		self.assertEqual(values["life_stage"], ADULT)

	def test_a_birth_year_nobody_knows_gives_the_birthday_but_no_age(self):
		values = get_derived_values([row("Birth", "1900-07-04", year_unknown=1)], None)

		self.assertEqual((values["birth_month"], values["birth_day"]), (7, 4))
		self.assertIsNone(values["birth_date"])
		self.assertEqual(values["age"], 0)
		self.assertIsNone(values["life_stage"])

	def test_no_birth_clears_the_birth_fields(self):
		values = get_derived_values([row("Baptism", "2000-01-01")], None)

		self.assertEqual(
			(values["birth_date"], values["birth_month"], values["birth_day"], values["age"]),
			(None, 0, 0, 0),
		)
		self.assertEqual((values["is_deceased"], values["deceased_date"]), (0, None))

	def test_a_death_stops_the_age_at_the_death(self):
		values = get_derived_values([row("Birth", "1950-03-01"), row("Death", "2000-02-28")], None)

		self.assertEqual((values["is_deceased"], values["deceased_date"]), (1, "2000-02-28"))
		self.assertEqual(values["age"], 49)

	def test_a_death_with_no_date_leaves_the_age_unknown(self):
		values = get_derived_values([row("Birth", "1950-03-01"), row("Death")], None)

		self.assertEqual((values["is_deceased"], values["deceased_date"]), (1, None))
		self.assertEqual(values["age"], 0)
		self.assertIsNone(values["life_stage"])


class TestLifeStage(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		set_adult_age(18)

	def test_the_household_role_comes_before_the_age(self):
		self.assertEqual(life_stage_for(CHILD, 40), CHILD)
		self.assertEqual(life_stage_for(ADULT, 5), ADULT)
		self.assertEqual(life_stage_for("Other Adult", None), ADULT)

	def test_without_a_role_the_age_is_measured_against_the_adult_age(self):
		self.assertEqual(life_stage_for(None, 17), CHILD)
		self.assertEqual(life_stage_for(None, 18), ADULT)

		set_adult_age(21)
		self.addCleanup(set_adult_age, 18)

		self.assertEqual(life_stage_for(None, 18), CHILD)

	def test_neither_role_nor_age_leaves_it_unknown(self):
		self.assertIsNone(life_stage_for(None, None))


class TestRefreshDaily(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		set_adult_age(18)
		for event_type in ("Birth", "Death"):
			ensure("Life Event Type", {"type": event_type})

	def stored(self, person, *fields):
		return frappe.db.get_value("Person", person.name, fields, as_dict=True)

	def test_a_birthday_brings_the_age_and_life_stage_up_to_date(self):
		person = make_person("_Test Coming Of Age", life_events=[row("Birth", add_years(nowdate(), -18))])
		frappe.db.set_value("Person", person.name, {"age": 17, "life_stage": CHILD}, update_modified=False)

		refresh_daily()

		self.assertEqual(self.stored(person, "age", "life_stage"), {"age": 18, "life_stage": ADULT})

	def test_a_birth_year_nobody_knows_keeps_no_age(self):
		person = make_person("_Test Ageless", life_events=[row("Birth", "1990-07-04", year_unknown=1)])
		frappe.db.set_value("Person", person.name, "age", 36, update_modified=False)

		refresh_daily()

		self.assertEqual(
			self.stored(person, "age", "birth_date", "birth_month"),
			{"age": 0, "birth_date": None, "birth_month": 7},
		)

	def test_the_age_of_someone_who_died_stays_at_their_death(self):
		person = make_person(
			"_Test Departed", life_events=[row("Birth", "1950-03-01"), row("Death", "2000-02-28")]
		)
		frappe.db.set_value("Person", person.name, "age", 76, update_modified=False)

		refresh_daily()

		self.assertEqual(self.stored(person, "age").age, 49)

	def test_the_patch_derives_the_fields_of_people_saved_before_they_existed(self):
		person = make_person(
			"_Test Before", life_events=[row("Birth", "1950-03-01"), row("Death", "2000-02-28")]
		)
		blank = {
			"birth_date": None,
			"birth_month": 0,
			"birth_day": 0,
			"is_deceased": 0,
			"deceased_date": None,
			"age": 0,
			"life_stage": None,
		}
		frappe.db.set_value("Person", person.name, blank, update_modified=False)
		modified = frappe.db.get_value("Person", person.name, "modified")
		frappe.db.delete("Singles", {"doctype": "People Settings"})

		derive_birth_death_and_life_stage.execute()

		self.assertEqual(
			self.stored(person, *blank, "modified"),
			{
				"birth_date": getdate("1950-03-01"),
				"birth_month": 3,
				"birth_day": 1,
				"is_deceased": 1,
				"deceased_date": getdate("2000-02-28"),
				"age": 49,
				"life_stage": ADULT,
				"modified": modified,
			},
		)
		self.assertEqual(frappe.db.get_single_value("People Settings", "adult_age"), 18)


class TestLivingPeopleFilter(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure("Life Event Type", {"type": "Death"})
		cls.living = make_person("_Test Living", "_Test Filter")
		cls.dead = make_person("_Test Dead", "_Test Filter", life_events=[row("Death", "2020-01-01")])

	def test_the_filter_leaves_out_people_who_died_in_a_query(self):
		Person = frappe.qb.DocType("Person")
		names = (
			frappe.qb.from_(Person)
			.select(Person.name)
			.where((Person.last_name == "_Test Filter") & living_people_filter(Person))
			.run(pluck=True)
		)
		self.assertEqual(names, [self.living.name])

	def test_the_filter_leaves_out_people_who_died_in_get_list(self):
		names = frappe.get_list(
			"Person", filters=[{"last_name": "_Test Filter"}, living_people_filter()], pluck="name"
		)
		self.assertEqual(names, [self.living.name])

	def test_the_filter_reads_the_deceased_box(self):
		frappe.db.set_value("Person", self.dead.name, "is_deceased", 0, update_modified=False)
		self.addCleanup(
			frappe.db.set_value, "Person", self.dead.name, "is_deceased", 1, update_modified=False
		)

		names = frappe.get_list(
			"Person", filters=[{"last_name": "_Test Filter"}, living_people_filter()], pluck="name"
		)
		self.assertCountEqual(names, [self.living.name, self.dead.name])
