# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe import _
from frappe.model.document import Document

from churchit.church_people.followup import VALUE_TYPES

SMART_LIST = ("Report Builder", "Person")


class FollowupTrigger(Document):
	"""Something that happens to a person and starts a card on the workflow. Its parent validates it."""

	def set_value_type(self):
		self.value_type = VALUE_TYPES.get(self.trigger)

	def validate_smart_list(self):
		"""On Smart List needs a smart list: a saved Report Builder report on Person."""
		report = (
			frappe.db.get_value("Report", self.value, ["report_type", "ref_doctype"]) if self.value else None
		)
		if tuple(report or ()) != SMART_LIST:
			frappe.throw(
				_("Row {0}: choose a smart list, a saved report of people made with Report Builder.").format(
					self.idx
				)
			)
