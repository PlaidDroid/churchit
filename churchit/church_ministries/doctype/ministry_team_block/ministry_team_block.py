# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from churchit.church_scope import CHURCH_FIELD, has_church_access


class MinistryTeamBlock(Document):
	def validate(self):
		self.refuse_another_churches_person()
		if getdate(self.to_date) < getdate(self.from_date):
			frappe.throw(_("To Date cannot be before From Date."))

	def refuse_another_churches_person(self):
		"""Only the person's own church blocks them from serving: scheduling reads every church's blocks.

		Frappe checks the user's rights before `church` is fetched from the person, on the church the
		caller sent. A member's own block (`add_own_block`) and other writes with ignore_permissions
		are not refused.
		"""
		if self.flags.ignore_permissions or not self.has_value_changed("person"):
			return
		before = self.get_doc_before_save()
		churches = (frappe.db.get_value("Person", self.person, CHURCH_FIELD), before and before.church)
		if not all(has_church_access(frappe.session.user, church) for church in churches):
			frappe.throw(
				_("{0} belongs to another church, so only that church can block them from serving.").format(
					self.title or self.person
				),
				frappe.PermissionError,
			)


def get_blocks_covering(person: str, from_date, to_date) -> list[frappe._dict]:
	"""Blocks that keep *person* from serving on any day from *from_date* to *to_date*.

	A block of someone in the same family counts when it covers the whole household.
	"""
	Block = frappe.qb.DocType("Ministry Team Block")
	covers_person = Block.person == person
	family = frappe.db.get_value("Person", person, "family")
	if family:
		household = frappe.get_all("Person", filters={"family": family}, pluck="name")
		covers_person = covers_person | (Block.person.isin(household) & (Block.whole_household == 1))

	# church-scope: blocks on the one person being scheduled, wherever their church is
	return (
		frappe.qb.from_(Block)
		.select(Block.name, Block.person, Block.from_date, Block.to_date, Block.reason)
		.where(covers_person & (Block.from_date <= to_date) & (Block.to_date >= from_date))
		.orderby(Block.from_date)
		.run(as_dict=True)
	)
