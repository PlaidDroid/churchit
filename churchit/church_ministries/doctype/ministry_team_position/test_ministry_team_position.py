# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

from frappe.tests.utils import FrappeTestCase

from churchit.tests.helpers import make_person, make_team_position


class TestMinistryTeamPosition(FrappeTestCase):
	def test_the_title_names_the_ministry(self):
		person = make_person("_Test Position", "Person").name
		position = make_team_position("_Test Sound", [person], ministry_name="_Test Worship")
		self.assertEqual(position.title, "_Test Sound (_Test Worship)")
