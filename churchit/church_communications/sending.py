# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Sending a Church Email: a copy for each person, filled in for them and kept on their timeline.

Placeholders are filled by a sandboxed Jinja environment that holds only the person's names.
Church Managers write Church Emails but not Email Templates, and Frappe's own Jinja
environment would hand them frappe.db.
"""

from typing import NamedTuple

import frappe
from frappe import _
from frappe.core.doctype.sms_settings.sms_settings import send_sms, validate_receiver_nos
from frappe.model.document import Document
from frappe.query_builder.functions import Coalesce
from frappe.utils import create_batch, escape_html, get_formatted_email, now_datetime, validate_email_address
from jinja2 import StrictUndefined, TemplateError, meta, nodes
from jinja2.sandbox import SandboxedEnvironment
from requests import HTTPError

from churchit.church_communications.bounces import bounced_addresses
from churchit.church_communications.newsletter import UNSUBSCRIBE_METHOD, unsubscribed_emails
from churchit.church_scope import session_person
from churchit.contacts import (
	EMAIL_DOCTYPE,
	PHONE_DOCTYPE,
	contact_key,
	get_primary_emails,
	get_primary_phone,
	get_primary_values,
)

PLACEHOLDERS = ("first_name", "last_name", "display_name", "full_name")
# The same names again, as Frappe's Email Templates write them: {{ doc.first_name }}.
DOCUMENT = "doc"

# An email copy waits in Frappe's Email Queue until the commit, so a hundred are committed at a time.
EMAILS_PER_COMMIT = 100
# A text leaves at once, so each is committed as soon as it is sent.
TEXTS_PER_COMMIT = 1

# The Communication delivery statuses a text copy gets, the same ones Frappe gives an email copy.
SENT = "Sent"
ERROR = "Error"
# What a gateway answers when it refuses one text, such as a number that is not valid or that replied
# STOP. Any other failure stops the delivery, so it goes on once the gateway works again.
REFUSED_STATUSES = (400, 422)

TEXT = SandboxedEnvironment(undefined=StrictUndefined)
HTML = SandboxedEnvironment(undefined=StrictUndefined, autoescape=True)


class Recipient(NamedTuple):
	person: str
	address: str


class Delivery:
	"""One Church Email on its way: each recipient gets a copy filled in for them, kept on their timeline."""

	def __init__(self, email: Document):
		self.email = email
		self.attachments = email.get_attachments()

	def send_all(self) -> None:
		"""Send a copy to each recipient who has none yet.

		Each batch is committed, so a delivery that stops loses nothing and a rerun sends nobody a second copy.
		A copy that failed counts as handled too, so it is not sent again either.
		"""
		size = TEXTS_PER_COMMIT if self.email.is_text_message else EMAILS_PER_COMMIT
		for recipients in create_batch(self.email.get_recipients(), size):
			# A second worker on the same email waits here, then finds these copies sent.
			frappe.db.get_value(self.email.doctype, self.email.name, "name", for_update=True)
			handled = get_people_sent(
				self.email.name, [recipient.person for recipient in recipients], include_failed=True
			)
			waiting = [recipient for recipient in recipients if recipient.person not in handled]
			values = get_person_values([recipient.person for recipient in waiting])
			sent = sum(self.send_copy(recipient, values[recipient.person]) for recipient in waiting)
			# Saving the count also moves `modified`, which tells the stalled retry this delivery is alive.
			self.email.db_set("recipients", (self.email.recipients or 0) + sent)
			frappe.db.commit()

	def send_copy(self, recipient: Recipient, values: dict) -> bool:
		"""Send *recipient* their copy and return whether it left. A text that did not leave is marked Error."""
		copy = self.email.get_copy(values)
		communication = self.record(recipient, copy)
		if not self.email.is_text_message:
			self.send_email(recipient.address, copy, communication)
			return True

		is_sent = send_text(recipient.address, copy.text)
		frappe.db.set_value(
			"Communication",
			communication,
			"delivery_status",
			SENT if is_sent else ERROR,
			update_modified=False,
		)
		return is_sent

	def send_test(self) -> str:
		"""Send the reader a copy at once, filled in with their own name, and return where it went."""
		person = session_person()
		values = get_person_values([person])[person] if person else get_user_values(frappe.session.user)
		copy = self.email.get_copy(values)
		if self.email.is_text_message:
			number = get_own_number(person)
			send_sms([number], copy.text, success_msg=False)
			return number
		address = frappe.db.get_value("User", frappe.session.user, "email")
		self.send_email(address, copy, now=True)
		return address

	def record(self, recipient: Recipient, copy: frappe._dict) -> str:
		"""Keep the copy as a Communication on the person's timeline, linked to the Church Email."""
		is_text = self.email.is_text_message
		communication = frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Communication",
				"communication_medium": "SMS" if is_text else "Email",
				"sent_or_received": "Sent",
				"communication_date": now_datetime(),
				"sender": get_formatted_email(frappe.session.user),
				"subject": copy.subject,
				"content": escape_html(copy.text).replace("\n", "<br>") if is_text else copy.message,
				"phone_no": recipient.address if is_text else None,
				"recipients": None if is_text else recipient.address,
				"has_attachment": int(bool(self.attachments) and not is_text),
				"reference_doctype": "Person",
				"reference_name": recipient.person,
			}
		)
		communication.add_link(self.email.doctype, self.email.name)
		# The copy is kept as it was sent, without the sender's signature that Frappe would add.
		communication.flags.skip_add_signature = True
		return communication.insert(ignore_permissions=True).name

	def send_email(
		self, address: str, copy: frappe._dict, communication: str | None = None, now: bool = False
	):
		queued = frappe.sendmail(
			recipients=[address],
			subject=copy.subject,
			message=copy.message,
			attachments=self.attachments,
			reference_doctype=self.email.doctype,
			reference_name=self.email.name,
			communication=communication,
			unsubscribe_method=UNSUBSCRIBE_METHOD,
			unsubscribe_params={"church": self.email.sending_church, "purpose": self.email.purpose},
			unsubscribe_message=_("Unsubscribe"),
			send_priority=0,
			now=now,
		)
		if not queued:
			frappe.throw(_("The email to {0} could not be queued. The Error Log says why.").format(address))


def render(source: str | None, values: dict, html: bool = False) -> str:
	"""*source* with its placeholders filled in from *values*. In an HTML message the names are escaped."""
	return (HTML if html else TEXT).from_string(source or "").render(values)


def check_placeholders(source: str | None, label: str) -> None:
	"""Refuse *source* unless it compiles and uses only the person placeholders."""
	if not source:
		return
	try:
		unknown = get_unknown_names(TEXT.parse(source))
		if not unknown:
			render(source, get_values({field: field for field in PLACEHOLDERS}))
	except TemplateError as error:
		frappe.throw(_("{0} cannot be filled in for each person: {1}").format(label, error.message))
	if unknown:
		frappe.throw(
			_("{0} uses {1}, which a Church Email cannot fill in. Use only these placeholders: {2}.").format(
				label,
				", ".join(f"{{{{ {name} }}}}" for name in sorted(unknown)),
				", ".join(f"{{{{ {field} }}}}" for field in PLACEHOLDERS),
			)
		)


def get_unknown_names(template: nodes.Template) -> set[str]:
	"""The names *template* reads that are not placeholders, `doc.<name>` included."""
	names = meta.find_undeclared_variables(template) - {*PLACEHOLDERS, DOCUMENT}
	fields = {
		f"{DOCUMENT}.{node.attr}"
		for node in template.find_all(nodes.Getattr)
		if isinstance(node.node, nodes.Name) and node.node.name == DOCUMENT and node.attr not in PLACEHOLDERS
	}
	return names | fields


def get_values(names: dict) -> dict:
	"""The placeholders of one person, each at the top and under `doc`, blank where not on file."""
	values = {field: names.get(field) or "" for field in PLACEHOLDERS}
	return {**values, DOCUMENT: values}


def get_person_values(people: list[str]) -> dict[str, dict]:
	"""The placeholders of each of *people*."""
	if not people:
		return {}
	# church-scope: the names of people already chosen as recipients, read by name
	rows = frappe.get_all("Person", filters={"name": ("in", people)}, fields=["name", *PLACEHOLDERS])
	return {row.name: get_values(row) for row in rows}


def get_user_values(user: str) -> dict:
	"""The placeholders of a login with no Person, for a test sent to them."""
	names = frappe.db.get_value("User", user, ["first_name", "last_name", "full_name"], as_dict=True)
	return get_values({**names, "display_name": names.full_name})


def get_own_number(person: str | None) -> str:
	"""The reader's mobile number, else their Person's primary phone, for a test text."""
	number = frappe.db.get_value("User", frappe.session.user, "mobile_no") or (
		person and get_primary_phone("Person", person)
	)
	if not number:
		frappe.throw(_("Add a mobile number to your user, or a phone to your Person, to get a test text."))
	return number


def get_email_recipients(people: list[str], church: str) -> list[Recipient]:
	"""Each of *people* at their primary email, each address once.

	Leaves out an address that is not valid, that bounced, or that stopped *church*'s emails or every email.
	"""
	emails = {
		person: validate_email_address(email)
		for person, email in get_primary_emails("Person", people).items()
	}
	refused = unsubscribed_emails(church) | bounced_addresses([email for email in emails.values() if email])
	usable = {person: email for person, email in emails.items() if email and email not in refused}
	return get_unique_recipients(people, usable, EMAIL_DOCTYPE)


def get_text_recipients(people: list[str]) -> list[Recipient]:
	"""Each of *people* at their primary phone, each number once."""
	numbers = get_primary_values(PHONE_DOCTYPE, "phone_number", "Person", people)
	return get_unique_recipients(people, numbers, PHONE_DOCTYPE)


def get_unique_recipients(
	people: list[str], addresses: dict[str, str], child_doctype: str
) -> list[Recipient]:
	"""*people* in order at their *addresses*, leaving out anyone whose address someone before them has.

	A household that shares one address gets one copy.
	"""
	taken = set()
	recipients = []
	for person in people:
		address = addresses.get(person)
		if address and (key := contact_key(child_doctype, address)) not in taken:
			taken.add(key)
			recipients.append(Recipient(person, address))
	return recipients


def send_text(number: str, text: str) -> bool:
	"""Send *text* to *number* and return whether it left.

	Frappe's gateway raises when it is refused and logs each number it took in SMS Log, so a text that is
	not in SMS Log did not leave. An app that sends texts through the `send_sms` hook keeps its own record.
	"""
	started = now_datetime()
	try:
		send_sms([number], text, success_msg=False)
	except HTTPError as error:
		if error.response is None or error.response.status_code not in REFUSED_STATUSES:
			raise
		return False

	if frappe.get_hooks("send_sms"):
		return True
	[logged_number] = validate_receiver_nos([number])
	return bool(frappe.db.exists("SMS Log", {"sent_to": logged_number, "creation": (">=", started)}))


def get_people_sent(email: str, people: list[str] | None = None, include_failed: bool = False) -> set[str]:
	"""The people who got a copy of the Church Email *email*: a Communication on their timeline linked to it.

	A copy that failed is left out, unless *include_failed* asks for everyone a copy was made for.
	"""
	Communication = frappe.qb.DocType("Communication")
	Link = frappe.qb.DocType("Communication Link")
	query = (
		frappe.qb.from_(Communication)
		.join(Link)
		.on(Link.parent == Communication.name)
		.select(Communication.reference_name)
		.where((Link.parenttype == "Communication") & (Link.link_doctype == "Church Email"))
		.where((Link.link_name == email) & (Communication.reference_doctype == "Person"))
	)
	if people is not None:
		query = query.where(Communication.reference_name.isin(people))
	if not include_failed:
		query = query.where(Coalesce(Communication.delivery_status, "") != ERROR)
	return set(query.run(pluck=True))


def can_send_texts() -> bool:
	"""True when the site can send texts: SMS Settings has a gateway, or an app sends them."""
	return bool(frappe.get_hooks("send_sms") or frappe.db.get_single_value("SMS Settings", "sms_gateway_url"))
