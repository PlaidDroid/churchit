# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe import _
from frappe.utils import cint
from frappe.utils.verified_command import verify_request

from churchit.church_communications.doctype.communication_consent.communication_consent import (
	DESK,
	EMAIL,
	GIVEN,
	MEMBER_PORTAL,
	record,
)
from churchit.church_communications.doctype.communication_purpose.communication_purpose import CHURCH_NEWS
from churchit.church_scope import is_multi_church, root_church, session_church, session_person
from churchit.contacts import get_primary_email, primary_email

# Who stopped a church's emails is kept as Frappe's own Email Unsubscribe, made against the church.
UNSUBSCRIBE_REFERENCE = "Church"
UNSUBSCRIBE_METHOD = "/api/method/churchit.church_communications.newsletter.unsubscribe"


def email_church(church: str | None) -> str | None:
	"""The church a Church Email goes out in and whose unsubscribes apply: the root church on a single-church site."""
	return church if church and is_multi_church() else root_church()


def unsubscribed_emails(church: str) -> set[str]:
	"""Addresses that stopped *church*'s emails, or every email the site sends."""
	stopped = frappe.get_all(
		"Email Unsubscribe",
		filters={"reference_doctype": UNSUBSCRIBE_REFERENCE, "reference_name": church},
		pluck="email",
	)
	everything = frappe.get_all("Email Unsubscribe", filters={"global_unsubscribe": 1}, pluck="email")
	return set(stopped) | set(everything)


def is_subscribed(email: str, church: str) -> bool:
	return not frappe.db.exists("Email Unsubscribe", unsubscribe_filters(email, church))


def set_subscribed(email: str, church: str, subscribed: bool):
	"""Start or stop *church*'s emails to *email*.

	Stopping them withdraws Church News from each of the church's people holding the address.
	Starting them again records no consent here: the caller records the one person who asked.
	"""
	from churchit.church_communications import consent  # consent imports this module

	filters = unsubscribe_filters(email, church)
	if subscribed:
		frappe.db.delete("Email Unsubscribe", filters)
	elif not frappe.db.exists("Email Unsubscribe", filters):
		# Saved the usual way, an Email Unsubscribe writes the address onto the Church's timeline.
		unsubscribe = frappe.get_doc({"doctype": "Email Unsubscribe", **filters})
		unsubscribe.set_user_and_timestamp()
		unsubscribe.db_insert()
		# db_insert runs no hooks, so the consent hook is run here.
		consent.record_unsubscribe(unsubscribe)


def unsubscribe_filters(email, church):
	return {"email": email, "reference_doctype": UNSUBSCRIBE_REFERENCE, "reference_name": church}


@frappe.whitelist(allow_guest=True)
def unsubscribe(email: str, church: str, **signed_params):
	"""The link at the foot of every Church Email: stop that church's emails to the address."""
	if not frappe.in_test and not verify_request():
		return

	set_subscribed(email, church, False)
	frappe.db.commit()
	frappe.respond_as_web_page(
		_("Unsubscribed"),
		_("{0} will no longer receive emails from {1}.").format(
			email, frappe.db.get_value("Church", church, "church_name") or church
		),
		indicator_color="green",
	)


def _current_subscriber_email():
	"""Return the email address to manage for the logged-in user.

	Prefers the linked Person's primary email, falling back to the User's own
	email. Returns ``None`` for guests or users with no email on file.
	"""
	user = frappe.session.user
	if user == "Guest":
		return None
	person = session_person()
	email = get_primary_email("Person", person) if person else None
	return email or frappe.db.get_value("User", user, "email")


def _current_subscriber_church():
	return email_church(session_church())


@frappe.whitelist()
def get_subscription_status():
	"""Whether the logged-in member's church emails them, and at which address."""
	email = _current_subscriber_email()
	church = _current_subscriber_church()
	return {
		"email": email,
		"subscribed": bool(email and church) and is_subscribed(email, church),
		"church": frappe.db.get_value("Church", church, "church_name") if church else None,
	}


@frappe.whitelist()
def set_subscription(subscribed: bool):
	"""Start or stop the logged-in member's church emails."""
	email = _current_subscriber_email()
	church = _current_subscriber_church()
	if not email or not church:
		frappe.throw(_("No email address is on file for your account. Please contact the church office."))

	subscribe = bool(cint(subscribed))
	set_subscribed(email, church, subscribe)
	if subscribe and (person := session_person()):
		record([person], EMAIL, GIVEN, CHURCH_NEWS, MEMBER_PORTAL)
	return {"subscribed": subscribe}


def has_unsubscribed(person) -> bool:
	"""Whether the church's emails no longer reach the loaded *person*'s primary email."""
	email = primary_email(person)
	return bool(email) and not is_subscribed(email, email_church(person.church))


def resubscribe(person):
	"""Let the church email the loaded *person* again, for someone who cannot do it in the portal."""
	if email := primary_email(person):
		set_subscribed(email, email_church(person.church), True)
		record([person.name], EMAIL, GIVEN, CHURCH_NEWS, DESK)
