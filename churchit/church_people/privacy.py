# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Remove a person's personal data and keep their record, so their giving and attendance still count.

`anonymize` names the person Anonymized and clears what says who they are: names, contact details, letters,
photo, medical notes, anniversary, the login and the IDs of an import. The person leaves their family and
their spouse, because named relatives would say who they are. A family left with no one in it is anonymized too,
since its name and contact details were the person's.

A Birth life event goes, because a birth date identifies a person. The other life events keep their type and
date, so the number of baptisms or new members in a year stays right, and lose their location and notes.

The records that name the person keep the link and lose the copies of the person's name and email addresses in
their text (titles, fetched names, donor emails). Their notes and connection cards are deleted outright, since
everything in them is about the person and nothing in them is counted. The person's change history, comments and attachments are
deleted, because they hold the old values.

A member's Personal Data Deletion Request anonymizes their Person once Frappe has deleted their login
(`anonymize_on_data_deletion`).
"""

import re

import frappe
from frappe import _
from frappe.model.delete_doc import get_linked_docs
from frappe.model.document import Document

from churchit.church_people.derived import BIRTH
from churchit.church_people.doctype.person.person import UNKNOWN_GENDER, get_display_name
from churchit.church_people.membership import INACTIVE, living_people_filter
from churchit.church_people.person_data_map import LinkedRecord, linked_records
from churchit.contacts import CONTACT_TABLES, UNIQUE_VALUE_FIELDS

ANONYMIZED = "Anonymized"
# Person fields that say who the person is.
PERSONAL_FIELDS = (
	"last_name",
	"middle_name",
	"nickname",
	"previous_last_name",
	"prefix",
	"suffix",
	"photo",
	"medical_notes",
	"alergies",
	"anniversary",
	"graduation_year",
	"grade",
	"spouse",
	"marital_status",
	"family",
	"user",
	"team_calendar_key",
	"legacy_system",
	"legacy_id",
)
# Person tables whose every row is personal.
PERSONAL_TABLES = ("emails", "phones", "addresses", "letters")
# Records wholly about the person, by the field naming them, deleted rather than kept.
PERSONAL_DOCTYPES = (("Person Note", "person"), ("Connection Card", "person"))
# Field types where a record that names the person can hold a copy of their name or email address.
TEXT_FIELDTYPES = ("Data", "Small Text", "Text", "Long Text", "Text Editor")
MANAGERS = ("Church Manager", "System Manager")


@frappe.whitelist(methods=["POST"])
def anonymize_person(person: str) -> None:
	"""Remove *person*'s personal data, for a Church Manager or System Manager who may change them."""
	frappe.only_for(MANAGERS, message=True)
	frappe.get_doc("Person", person).check_permission("write")
	anonymize(person)


def anonymize(person: str) -> None:
	"""Remove *person*'s personal data, and the copies of it on the records that name them; keep the record."""
	doc = frappe.get_doc("Person", person)
	names, emails = get_identity(doc)
	family = doc.family
	addresses = [row.address for row in doc.addresses]
	if doc.spouse:
		# The spouse keeps their own marital status and anniversary; only the link to this record goes.
		frappe.db.set_value("Person", {"name": doc.spouse, "spouse": doc.name}, "spouse", None)
	strip_person(doc)
	doc.save(ignore_permissions=True)
	forget(doc, addresses)
	if family and not frappe.db.exists("Family Members", {"parenttype": "Family", "parent": family}):
		anonymize_family(family)
	for child_doctype in UNIQUE_VALUE_FIELDS:
		# Someone who shared this person's email or phone holds it alone from now on.
		frappe.db.set_value(
			child_doctype,
			{"shared_from_type": "Person", "shared_from": person},
			"shared_from",
			None,
			update_modified=False,
		)
	delete_personal_records(person)
	remove_copies(linked_records(person), names, emails)
	doc.add_comment("Info", _("Personal data removed."))


def delete_personal_records(person: str) -> None:
	"""Delete the notes and connection cards about *person*, leaving no copy in Deleted Documents."""
	for doctype, fieldname in PERSONAL_DOCTYPES:
		# church-scope: every record about the one person being anonymized, whichever church holds it
		for name in frappe.get_all(doctype, filters={fieldname: person}, pluck="name"):
			frappe.delete_doc(doctype, name, ignore_permissions=True, delete_permanently=True)


def get_identity(doc: Document) -> tuple[set[str], set[str]]:
	"""The names *doc* goes by and its email addresses, as a copy on another record spells them.

	The names come from the name fields as well as the stored ones, which Frappe's own deletion request may
	have redacted already.
	"""
	names = {
		doc.full_name,
		doc.display_name,
		" ".join(filter(None, (doc.first_name, doc.last_name))),
		get_display_name(doc.first_name, doc.last_name, doc.nickname, doc.suffix),
	}
	emails = {row.email_address.lower() for row in doc.emails if row.email_address}
	return {name for name in names if name and name != ANONYMIZED}, emails


def strip_person(doc: Document) -> None:
	"""Clear what says who *doc* is, keeping the life events and positions a church counts."""
	doc.update({fieldname: None for fieldname in PERSONAL_FIELDS})
	doc.update({"first_name": ANONYMIZED, "gender": UNKNOWN_GENDER, "hide_from_directory": 1})
	for fieldname in PERSONAL_TABLES:
		doc.set(fieldname, [])
	doc.set("life_events", [row for row in doc.life_events if row.event_type != BIRTH])
	for row in doc.life_events:
		row.update({"location": None, "notes": None})
	for row in doc.positions:
		row.notes = None


def anonymize_family(family: str) -> None:
	"""Strip a family left with no one in it, whose name and contact details were its last member's."""
	doc = frappe.get_doc("Family", family)
	addresses = [row.address for row in doc.addresses]
	doc.update({"family_name": ANONYMIZED, "photo": None, "legacy_system": None, "legacy_id": None})
	for fieldname, *_rest in CONTACT_TABLES:
		doc.set(fieldname, [])
	doc.save(ignore_permissions=True)
	forget(doc, addresses)


def forget(doc: Document, addresses: list[str]) -> None:
	"""Delete *doc*'s change history, comments and attachments, and each of *addresses* nothing links to now."""
	attachments = frappe.get_all(
		"File", filters={"attached_to_doctype": doc.doctype, "attached_to_name": doc.name}, pluck="name"
	)
	for file in attachments:
		frappe.delete_doc("File", file, ignore_permissions=True, delete_permanently=True)
	frappe.db.delete("Version", {"ref_doctype": doc.doctype, "docname": doc.name})
	frappe.db.delete("Comment", {"reference_doctype": doc.doctype, "reference_name": doc.name})
	for address in set(addresses):
		if not get_linked_docs(frappe.get_doc("Address", address)):
			frappe.delete_doc("Address", address, ignore_permissions=True, delete_permanently=True)


def remove_copies(records: list[LinkedRecord], names: set[str], emails: set[str]) -> None:
	"""In the text fields of *records*, write Anonymized for each of *names* and remove each of *emails*."""
	replacements = [(re.compile(rf"(?<!\w){re.escape(name)}(?!\w)"), ANONYMIZED) for name in names]
	replacements += [(re.compile(re.escape(email), re.IGNORECASE), "") for email in emails]
	for record in records:
		scrub_record(record, replacements)


def scrub_record(record: LinkedRecord, replacements: list[tuple[re.Pattern, str]]) -> None:
	"""Apply each (pattern, replacement) of *replacements* to the text fields of *record*."""
	fields = [
		df.fieldname
		for df in frappe.get_meta(record.doctype).fields
		if df.fieldtype in TEXT_FIELDTYPES and not df.is_virtual
	]
	values = frappe.db.get_value(record.doctype, record.name, fields, as_dict=True) if fields else {}
	changed = {}
	for fieldname, value in (values or {}).items():
		scrubbed = value
		for pattern, replacement in replacements if value else ():
			scrubbed = pattern.sub(replacement, scrubbed)
		if scrubbed != value:
			changed[fieldname] = scrubbed.strip() or None
	if changed:
		frappe.db.set_value(record.doctype, record.name, changed, update_modified=False)


def anonymize_on_data_deletion(doc: Document, method=None) -> None:
	"""doc_events on_change of Personal Data Deletion Request: anonymize the Person of a deleted login.

	Frappe gives the login the request's name before it marks the request Deleted.
	"""
	if doc.status != "Deleted":
		return
	# church-scope: the people of one deleted login, whichever church they belong to
	for person in frappe.get_all("Person", filters={"user": doc.name}, pluck="name"):
		anonymize(person)


# People the daily job anonymizes in one run; the rest wait for the next day.
REMOVALS_PER_RUN = 50


def remove_due_personal_data() -> None:
	"""Daily job: remove the personal data of people Inactive longer than People Settings allows.

	A run takes the longest Inactive first, up to `REMOVALS_PER_RUN`, and commits each person, so a person who
	fails is logged and the others still go.
	"""
	years = get_retention_years()
	if not years:
		return
	Person = frappe.qb.DocType("Person")
	# church-scope: People Settings' rule covers every church's people and shows nobody anything
	query = get_due_query(Person, frappe.utils.add_years(frappe.utils.today(), -years))
	for person, inactive_since in (
		query.select(Person.name, Person.inactive_since).limit(REMOVALS_PER_RUN).run()
	):
		try:
			anonymize(person)
			frappe.get_doc("Person", person).add_comment(
				"Info",
				_("Removed because the person has been Inactive since {0}, {1} years or more.").format(
					frappe.utils.formatdate(inactive_since), years
				),
			)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				_("Could not remove the personal data of {0}").format(person),
				reference_doctype="Person",
				reference_name=person,
			)


def get_retention_years() -> int:
	"""People Settings' years Inactive before personal data is removed; 0 removes nothing."""
	return frappe.utils.cint(frappe.db.get_single_value("People Settings", "remove_inactive_after_years"))


def get_due_query(Person, inactive_by):
	"""Query on *Person* for the living people Inactive since *inactive_by* or before whose data is still there.

	It selects nothing and reads every church: the caller picks the columns and the scope.
	"""
	return (
		frappe.qb.from_(Person)
		.where(
			(Person.membership_status == INACTIVE)
			& (Person.inactive_since <= inactive_by)
			& (Person.first_name != ANONYMIZED)
			& living_people_filter(Person)
		)
		.orderby(Person.inactive_since)
		.orderby(Person.name)
	)
