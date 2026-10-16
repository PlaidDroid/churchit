# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe.tests.utils import FrappeTestCase

from churchit.church_people.membership import MEMBER
from churchit.church_people.report.member_demographics import member_demographics
from churchit.tests.helpers import ensure, make_person, make_two_churches


class TestMemberDemographics(FrappeTestCase):
	def test_a_picked_church_counts_only_its_own_members(self):
		_root, branch_a, branch_b = make_two_churches()
		self.addCleanup(frappe.clear_cache)
		ensure("Membership Type", {"type": MEMBER})
		for church in (branch_a, branch_b):
			make_person(
				"_Test Demographics",
				church,
				church=church,
				membership=MEMBER,
				life_events=[{"event_type": "Birth", "date": "1990-06-01"}],
			)

		_columns, rows = member_demographics.execute({"church": branch_a})

		self.assertEqual(sum(row["total"] for row in rows), 1)
