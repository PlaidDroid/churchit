# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Email addresses whose mail came back.

Frappe has a Bounced delivery status but nothing sets it, so a bounce report that reaches
the church's inbox marks the address Email Bounced on every record holding it.

Frappe stores a received report as a Communication with the report's text and the headers
of the returned message, but drops its machine-readable part (Action, Status), so the text
decides. Frappe links a report to the sent Communication through `in_reply_to` only when the
report carries an In-Reply-To header, as Gmail and Microsoft 365 reports do and Postfix does not.
"""

import re

import frappe
from frappe.utils import EMAIL_STRING_PATTERN, now_datetime, strip_html

from churchit.contacts import CONTACT_PARENTS, EMAIL_DOCTYPE

BOUNCE_SENDERS = ("mailer-daemon", "postmaster")
ADDRESS = EMAIL_STRING_PATTERN.pattern
# Where Postfix, Gmail and Microsoft 365 reports name an address that failed.
FAILED_RECIPIENTS = re.compile(
	rf"^[ \t]*<{ADDRESS}>:|wasn.t delivered to\s+{ADDRESS}|your message to\s+{ADDRESS}\s+couldn.t be delivered",
	re.IGNORECASE | re.MULTILINE,
)
# Delivery failed for good: an enhanced status code 5.x.x, or the words reports use for it.
# A delay warning carries neither.
FAILED = re.compile(
	r"(?<![\d.])5\.\d{1,3}\.\d{1,3}(?![\d.])|could\s+not\s+be\s+delivered\s+to|couldn.t\s+be\s+delivered"
	r"|wasn.t\s+delivered",
	re.IGNORECASE,
)


def bounced_addresses(emails: list[str]) -> set[str]:
	"""The addresses among *emails* marked Email Bounced on any record, compared without case."""
	if not emails:
		return set()
	rows = frappe.get_all(
		EMAIL_DOCTYPE, filters={"email_address": ("in", emails), "is_bounced": 1}, pluck="email_address"
	)
	bounced = {row.lower() for row in rows}
	return {email for email in emails if email.lower() in bounced}


def record_bounce(doc, method=None):
	"""after_insert on Communication: mark the addresses a received bounce report names.

	A report counts when a mailer-daemon or postmaster address sent it and it says delivery
	failed for good, so a delay warning changes nothing. Each address it names as failed is
	marked on every record holding it, and the sent Communication it answers, when Frappe
	linked one, is marked Bounced. Nothing here raises on mail it cannot read.
	"""
	if not is_from_mail_system(doc):
		return
	text = doc.text_content or strip_html(doc.content or "")
	if not FAILED.search(text):
		return

	mark_bounced(get_failed_addresses(text))
	if doc.in_reply_to:
		frappe.db.set_value(
			"Communication",
			{"name": doc.in_reply_to, "sent_or_received": "Sent"},
			"delivery_status",
			"Bounced",
			update_modified=False,
		)


def is_from_mail_system(doc) -> bool:
	"""True for an email received from a mailer-daemon or postmaster address."""
	local_part = (doc.sender or "").partition("@")[0].lower()
	return (
		doc.sent_or_received == "Received"
		and doc.communication_medium == "Email"
		and local_part in BOUNCE_SENDERS
	)


def get_failed_addresses(text: str) -> set[str]:
	"""The addresses *text* names as failed, in lower case and without a full stop that ends a sentence."""
	return {
		address.lower().rstrip(".")
		for match in FAILED_RECIPIENTS.findall(text)
		for address in match
		if address
	}


def mark_bounced(emails: set[str]):
	"""Tick Email Bounced on every Email Address row holding one of *emails*, on any record.

	Each record's `modified` moves too, so a form opened before the bounce cannot save
	the box unticked again: Frappe refuses that save as made on an older copy.
	"""
	if not emails:
		return
	rows = frappe.get_all(
		EMAIL_DOCTYPE,
		filters={"email_address": ("in", list(emails)), "parenttype": ("in", CONTACT_PARENTS)},
		fields=["name", "parenttype", "parent"],
	)
	if not rows:
		return

	frappe.db.set_value(
		EMAIL_DOCTYPE, {"name": ("in", [row.name for row in rows])}, "is_bounced", 1, update_modified=False
	)
	modified = now_datetime()
	for parenttype in {row.parenttype for row in rows}:
		parents = [row.parent for row in rows if row.parenttype == parenttype]
		frappe.db.set_value(
			parenttype, {"name": ("in", parents)}, "modified", modified, update_modified=False
		)
