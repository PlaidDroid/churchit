# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe.model.document import Document


class MinistryTeamPosition(Document):
	def validate(self):
		ministry_name = frappe.db.get_value("Ministry", self.ministry, "ministry_name")
		self.title = f"{self.position_name} ({ministry_name})"
