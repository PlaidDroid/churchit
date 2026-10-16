# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_years, today

from churchit.church_people.number_card.birthdays_this_week import birthdays_this_week
from churchit.church_people.report.person_birthdays_this_month import person_birthdays_this_month
from churchit.church_people.report.person_birthdays_this_week import person_birthdays_this_week
from churchit.tests.helpers import ensure, make_person


class TestBirthdayLists(FrappeTestCase):
	def test_people_who_have_died_are_left_out(self):
		for event_type in ("Birth", "Death"):
			ensure("Life Event Type", {"type": event_type})
		birth = {"event_type": "Birth", "date": add_years(today(), -70)}
		living = make_person("_Test Birthday", "Living", life_events=[birth])
		count = birthdays_this_week.get_count()
		departed = make_person(
			"_Test Birthday", "Departed", life_events=[birth, {"event_type": "Death", "date": "2024-01-01"}]
		)

		for report in (person_birthdays_this_week, person_birthdays_this_month):
			with self.subTest(report.__name__):
				names = [row["name"] for row in report.execute({})[1]]
				self.assertIn(living.name, names)
				self.assertNotIn(departed.name, names)
		self.assertEqual(birthdays_this_week.get_count(), count)
