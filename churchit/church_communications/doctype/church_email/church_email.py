# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, create_batch, get_datetime, now_datetime, validate_email_address

from churchit.church_communications.newsletter import UNSUBSCRIBE_METHOD, email_church, unsubscribed_emails
from churchit.church_ministries.doctype.ministry_team_assignment.ministry_team_assignment import (
	can_send_email,
)
from churchit.church_people.membership import ACTIVE, MEMBER, deceased_people
from churchit.church_scope import default_church, document_church, is_multi_church
from churchit.contacts import get_primary_emails

DRAFT = "Draft"
SCHEDULED = "Scheduled"
SENDING = "Sending"
SENT = "Sent"

ACTIVE_MEMBERS = "Active Members"
GROUP = "Group"

# Frappe queues more than 100 recipients as background batches, which a failed job loses
# without trace, so each call stays at 100 and every row is written with the Sent status.
QUEUE_SIZE = 100
# A delivery job lost to a worker restart is picked up again after this long.
STALLED_AFTER_MINUTES = 15


class ChurchEmail(Document):
	def validate(self):
		if self.send_to != GROUP:
			self.group = None
		self.refuse_another_churches_group()
		self.keep_status()

	def refuse_another_churches_group(self):
		"""The group's members are emailed in this church's name, so it must be this church's group."""
		if not self.group or not is_multi_church():
			return
		church = document_church(self) or default_church()
		if frappe.db.get_value("Group", self.group, "church") != church:
			frappe.throw(_("Group {0} belongs to another church.").format(self.group))

	def keep_status(self):
		"""Only Send, Unschedule and the delivery move the status, and an email on its way stays as it is."""
		before = self.get_doc_before_save()
		if not before:
			self.status = DRAFT
			return
		if before.status in (SENDING, SENT):
			frappe.throw(_("This email has been sent, so it can no longer be changed."))
		self.status = before.status
		if self.status == SCHEDULED and not self.send_after:
			frappe.throw(_("Set Send At, or click Unschedule to keep this email as a draft."))

	@property
	def sending_church(self) -> str:
		return email_church(self.church)

	@frappe.whitelist()
	def get_recipient_count(self) -> int:
		return len(self.get_recipients())

	@frappe.whitelist(methods=["POST"])
	def send_test(self):
		"""Send this email to the reader alone, straight away, the way everyone will get it."""
		self.check_permission("write")
		require_outgoing_email_account()
		self.queue([frappe.db.get_value("User", frappe.session.user, "email")], now=True)

	@frappe.whitelist(methods=["POST"])
	def send(self) -> str:
		"""Send it now, or schedule it when Send At is still ahead. Returns the new status."""
		self.check_permission("write")
		require_outgoing_email_account()
		if frappe.db.get_value(self.doctype, self.name, "status", for_update=True) != DRAFT:
			frappe.throw(_("This email has already been sent or scheduled."))

		if self.send_after and get_datetime(self.send_after) > now_datetime():
			self.db_set("status", SCHEDULED)
			return SCHEDULED

		self.db_set("status", SENDING)
		enqueue_delivery(self.name)
		return SENDING

	@frappe.whitelist(methods=["POST"])
	def unschedule(self):
		"""Turn a scheduled email back into a draft, so it is not sent at Send At."""
		self.check_permission("write")
		if frappe.db.get_value(self.doctype, self.name, "status", for_update=True) == SCHEDULED:
			self.db_set("status", DRAFT)

	def get_recipients(self) -> list[str]:
		"""Primary email of everyone this is for, without anyone who unsubscribed."""
		emails = {email for email in get_primary_emails("Person", self.get_people()).values() if email}
		valid = {email for email in emails if validate_email_address(email)}
		return sorted(valid - unsubscribed_emails(self.sending_church))

	def get_people(self) -> list[str]:
		"""The people this is for, worked out now so that nobody who died or left is included."""
		deceased = deceased_people().run(pluck=True)
		if self.send_to == GROUP:
			# church-scope: the members listed on one group, which validate keeps to this email's church
			members = frappe.get_all(
				"Group Member", filters={"parenttype": "Group", "parent": self.group}, pluck="person"
			)
			return [person for person in members if person and person not in deceased]

		filters = {"membership_status": ACTIVE, "name": ("not in", deceased)}
		if self.send_to == ACTIVE_MEMBERS:
			filters["membership"] = MEMBER
		if is_multi_church():
			filters["church"] = self.church
		# church-scope: the people of this email's own church; a Person is never shared
		return frappe.get_all("Person", filters=filters, pluck="name")

	def get_attachments(self) -> list[dict]:
		files = frappe.get_all(
			"File", filters={"attached_to_doctype": self.doctype, "attached_to_name": self.name}, pluck="name"
		)
		return [{"fid": file} for file in files]

	def queue(self, recipients: list[str], now: bool = False):
		"""Queue the email; each recipient still gets a copy of their own, with their own unsubscribe link."""
		for batch in create_batch(recipients, QUEUE_SIZE):
			queued = frappe.sendmail(
				recipients=batch,
				subject=self.subject,
				message=self.message,
				attachments=self.get_attachments(),
				reference_doctype=self.doctype,
				reference_name=self.name,
				unsubscribe_method=UNSUBSCRIBE_METHOD,
				unsubscribe_params={"church": self.sending_church},
				unsubscribe_message=_("Unsubscribe"),
				send_priority=0,
				now=now,
			)
			if not queued:
				frappe.throw(_("The email could not be queued. The Error Log says why."))


def require_outgoing_email_account():
	if not can_send_email():
		frappe.throw(_("Set up an outgoing Email Account first."))


def enqueue_delivery(name: str):
	frappe.enqueue(
		deliver,
		queue="long",
		name=name,
		job_id=f"church_email::{name}",
		deduplicate=True,
		enqueue_after_commit=True,
	)


def deliver(name: str):
	"""Background job: queue the email to everyone it is for, once however often the job runs."""
	if frappe.db.get_value("Church Email", name, "status", for_update=True) != SENDING:
		return

	email = frappe.get_doc("Church Email", name)
	try:
		recipients = email.get_recipients()
		email.queue(recipients)
	except Exception as error:
		# Nothing was queued, so the church can send it again once the cause is fixed.
		frappe.db.rollback()
		frappe.db.set_value("Church Email", name, "status", DRAFT)
		email.add_comment("Comment", _("This email was not sent and is a draft again: {0}").format(error))
		frappe.db.commit()
		raise
	email.db_set({"status": SENT, "recipients": len(recipients), "sent_on": now_datetime()})


def send_scheduled():
	"""Scheduler: send each scheduled email whose Send At has come, and retry stalled deliveries."""
	if not can_send_email():
		return
	# church-scope: scheduled job; each email goes to its own church's people
	due = frappe.get_all(
		"Church Email", filters={"status": SCHEDULED, "send_after": ("<=", now_datetime())}, pluck="name"
	)
	stalled = frappe.get_all(
		"Church Email",
		filters={
			"status": SENDING,
			"modified": ("<", add_to_date(now_datetime(), minutes=-STALLED_AFTER_MINUTES)),
		},
		pluck="name",
	)
	for name in due + stalled:
		if claim(name):
			try:
				deliver(name)
			except Exception:
				frappe.log_error(f"Church Email {name} was not sent")
		frappe.db.commit()


def claim(name: str) -> bool:
	"""Move a due email to Sending, or keep a stalled one there, unless another worker got to it."""
	status = frappe.db.get_value("Church Email", name, "status", for_update=True)
	if status == SCHEDULED:
		frappe.db.set_value("Church Email", name, "status", SENDING)
	return status in (SCHEDULED, SENDING)
