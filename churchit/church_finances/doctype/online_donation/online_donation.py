# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

from itertools import groupby

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, fmt_money, formatdate, now, nowdate

from churchit.church_finances.payments import get_integration_request, is_charge_confirmed, notify_finance
from churchit.church_scope import refuse_another_churches_record
from churchit.contacts import get_primary_email

# Statuses reported by Payments-app gateway controllers that mean the
# payment succeeded. Different gateways use slightly different words.
PAID_STATUSES = ("Completed", "Paid")

# Payment Type used for online gifts (created in after_install / patch).
ONLINE_PAYMENT_TYPE = "Online"


class OnlineDonation(Document):
	def validate(self):
		refuse_another_churches_record(self, "Fund", self.fund)

	@property
	def charged_amount(self) -> float:
		"""The whole payment: the gift and the processing fee the giver chose to cover."""
		return flt(flt(self.amount) + flt(self.fee_amount), self.precision("amount"))

	def on_payment_authorized(self, status=None):
		"""Callback invoked by the Payments-app gateway controller once the
		donor finishes paying. Records the gift in the Finance module on
		success and returns the URL the donor should land on.

		See ``payments`` gateway controllers, e.g.
		``StripeSettings.finalize_request`` which calls
		``run_method("on_payment_authorized", status)``.
		"""
		if status in PAID_STATUSES:
			self.record_charge()
		else:
			self.db_set("status", "Failed")

		return f"/give?success={self.name}"

	@frappe.whitelist(methods=["POST"])
	def record_charged_gift(self):
		"""Record a Pending gift the gateway charged when its callback never arrived."""
		self.reload()
		self.check_permission("write")
		if self.status != "Pending":
			frappe.throw(_("Only a Pending gift can be recorded. This gift is {0}.").format(_(self.status)))
		if not get_integration_request(self):
			frappe.throw(_("No completed payment was found for this gift."))

		self.record_charge()

	def record_charge(self):
		"""Record the gift once, and only when the gateway charged its amount in its currency.

		The checkout page sends the amount from the browser, so the gift's own amount
		is not proof of what the donor paid. The gateway's request log is.
		"""
		if frappe.db.get_value(self.doctype, self.name, "status", for_update=True) == "Paid":
			return

		request = get_integration_request(self)
		if request:
			self.transaction_reference = request.name

		if not is_charge_confirmed(self, self.charged_amount, self.currency):
			self.db_set({"status": "Needs Review", "transaction_reference": self.transaction_reference})
			notify_finance([self], self.get_charge_problem(request))
			return

		self.record_donation()

	def get_charge_problem(self, request) -> str:
		"""Why *request*, the newest completed charge, cannot record this gift."""
		if not request:
			return _("No completed payment was found for online gift {0}.").format(self.name)

		charged = frappe.parse_json(request.data or "{}")
		return _(
			"Online gift {0} was charged {1}, but the gift is {2}. Check the payment before you record it."
		).format(
			self.name,
			fmt_money(charged.get("amount"), currency=charged.get("currency")),
			fmt_money(self.charged_amount, currency=self.currency),
		)

	def record_donation(self):
		"""Create and submit a Collection holding this single gift so it flows
		through the existing Fund / report / dashboard logic, then mark this
		record Paid and email a receipt."""
		collection = frappe.new_doc("Collection")
		collection.date = now()
		# The gift settles the church, not the session: a gateway calls back with
		# whatever session finished the payment, and a shared fund names no church.
		collection.church = self.church
		collection.append(
			"donations",
			{
				"amount": self.charged_amount,
				"payment_type": ONLINE_PAYMENT_TYPE,
				"fund": self.fund,
				# blank person => recorded as an anonymous gift
				"person": self.person or None,
				"notes": self.notes,
			},
		)
		# A single-donation collection balances exactly, so before_submit() passes.
		collection.expected_total = self.charged_amount
		collection.insert(ignore_permissions=True)
		collection.submit()

		self.status = "Paid"
		self.collection = collection.name
		self.save(ignore_permissions=True)

		self.send_acknowledgment()

	def send_acknowledgment(self):
		"""Email the giver a receipt using the shipped "Donation Acknowledgment"
		Email Template. Best-effort: a mail failure must not undo the recorded
		gift."""
		recipient = self.email
		first_name = (self.donor_name or "").split(" ")[0]
		if self.person:
			person = frappe.db.get_value("Person", self.person, ["first_name"], as_dict=True)
			if person:
				recipient = recipient or get_primary_email("Person", self.person)
				first_name = person.first_name or first_name

		if not recipient:
			return

		context = {
			"first_name": first_name or _("Friend"),
			"amount": fmt_money(self.charged_amount, currency=self.currency),
			"fund": frappe.db.get_value("Fund", self.fund, "fund") or self.fund,
			"date": formatdate(nowdate()),
			"doc": self,
		}

		subject = _("Receipt for your gift")
		message = _("<p>Dear {0},</p><p>Thank you for your gift of {1} to the {2} fund.</p>").format(
			context["first_name"], context["amount"], context["fund"]
		)
		if frappe.db.exists("Email Template", "Donation Acknowledgment"):
			template = frappe.get_doc("Email Template", "Donation Acknowledgment")
			# Render via the framework's own helper so the Jinja evaluation happens
			# inside Frappe core against the normal email template
			formatted = template.get_formatted_email(context)
			subject = formatted["subject"]
			message = formatted["message"]

		try:
			frappe.sendmail(
				recipients=[recipient],
				subject=subject,
				message=message,
				reference_doctype=self.doctype,
				reference_name=self.name,
			)
		except Exception:
			frappe.log_error(
				title="Online giving receipt failed",
				message=frappe.get_traceback(),
			)


def alert_unrecorded_gifts():
	"""Daily: tell Finance about gifts that a gateway charged but that are still Pending.

	A callback that fails stores no transaction reference, so the gift is found
	through the request log that names it.
	"""
	Gift = frappe.qb.DocType("Online Donation")
	Request = frappe.qb.DocType("Integration Request")
	# church-scope: a job over every church; notify_finance alerts only each gift's own church
	gifts = (
		frappe.qb.from_(Gift)
		.join(Request)
		.on(Request.reference_docname == Gift.name)
		.where(
			(Request.reference_doctype == "Online Donation")
			& (Request.status == "Completed")
			& (Gift.status == "Pending")
		)
		.select(Gift.name, Gift.church)
		.distinct()
		.orderby(Gift.church)
		.orderby(Gift.name)
		.run(as_dict=True)
	)

	for _church, rows in groupby(gifts, key=lambda gift: gift.church):
		rows = [frappe._dict(gift, doctype="Online Donation") for gift in rows]
		notify_finance(
			rows,
			_(
				"These online gifts were charged but are still Pending: {0}. Open each one and record it."
			).format(", ".join(gift.name for gift in rows)),
		)
