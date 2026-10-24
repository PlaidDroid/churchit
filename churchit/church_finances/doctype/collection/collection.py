# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe.model.document import Document

from churchit.church_finances.doctype.fund.fund import add_transaction, remove_transactions
from churchit.church_scope import refuse_another_churches_record
from churchit.contacts import get_primary_emails


class Collection(Document):
	def validate(self):
		for donation in self.donations:
			refuse_another_churches_record(self, "Fund", donation.fund)

	def before_save(self):
		parts = [self.function or "", str(self.date or "")]
		self.title = " - ".join(p for p in parts if p)
		self.update_totals()
		self.set_donor_emails()

	def set_donor_emails(self):
		"""Copy each giver's primary email onto their Donation row.

		The Donation Acknowledgment notification reads ``person_email`` off the
		rows it walks. This used to be a ``fetch_from`` on ``person.email``, which
		stopped working when emails moved to a child table, because Frappe can
		fetch across a Link but not into the linked record's own child rows.
		"""
		people = [d.person for d in self.donations if d.person]
		by_person = get_primary_emails("Person", people)
		for donation in self.donations:
			donation.person_email = by_person.get(donation.person) if donation.person else None

	def update_totals(self):
		# Recalculate entered total
		self.total_amount = sum(d.amount or 0 for d in self.donations)

		# Recalculate imbalance
		self.imbalance = (self.total_amount or 0) - (self.expected_total or 0)

		# Recalculate fund totals
		fund_totals = {}
		for donation in self.donations:
			if donation.fund and donation.amount:
				fund_totals.setdefault(donation.fund, 0)
				fund_totals[donation.fund] += donation.amount

		self.fund_totals = []
		for fund, total in fund_totals.items():
			self.append("fund_totals", {"fund": fund, "total": total})

		# Recalculate payment type totals
		payment_type_totals = {}
		for donation in self.donations:
			if donation.payment_type and donation.amount:
				payment_type_totals.setdefault(donation.payment_type, 0)
				payment_type_totals[donation.payment_type] += donation.amount

		self.payment_type_totals = []
		for payment_type, total in payment_type_totals.items():
			self.append("payment_type_totals", {"payment_type": payment_type, "total": total})

	def before_submit(self):
		if self.imbalance != 0:
			frappe.throw(
				f"Imbalance of {frappe.utils.fmt_money(self.imbalance)} must be resolved before submitting."
			)

	def on_submit(self):
		self.update_funds(reverse=False)

	def on_cancel(self):
		self.update_funds(reverse=True)

	def update_funds(self, reverse=False):
		fund_totals = {}
		for donation in self.donations:
			if donation.fund and donation.amount:
				fund_totals[donation.fund] = fund_totals.get(donation.fund, 0) + donation.amount

		if reverse:
			remove_transactions("Collection", self.name)

		messages = []
		for fund, total in sorted(fund_totals.items()):
			if not reverse:
				add_transaction(fund, total, "Collection", self.name, self.date)
			label, balance = frappe.db.get_value("Fund", fund, ["fund", "balance"])
			messages.append(
				f"💸 {label} fund decreased by ${total}"
				if reverse
				else f"💰 {label} fund increased by ${total}"
			)
			if balance < 0:
				messages.append(f"⚠️ {label} fund balance is negative: {balance}")

		if messages:
			frappe.msgprint("<br>".join(messages))
