# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from churchit.church_people.doctype.person.person import get_display_name
from churchit.church_people.followup import start_cards
from churchit.church_people.matching import Match, find_matches, record_possible_duplicates
from churchit.church_people.membership import VISITOR
from churchit.church_scope import is_multi_church
from churchit.contacts import EMAIL_DOCTYPE, EMAIL_FIELD, PHONE_DOCTYPE, PHONE_FIELD, get_other_holder

NEW = "New"
PROCESSED = "Processed"
SPAM = "Spam"
# The Follow-up Trigger a card starts workflows on, and the Person source of the people it adds.
CONNECTION_CARD = "Connection Card"
# A card is linked to an existing person only when the match shares one reason from each set.
SHARED_CONTACT = frozenset(("same email", "same phone"))
SAME_FIRST_NAME = frozenset(("same first name", "nickname of first name"))
SAME_LAST_NAME = frozenset(("same last name", "matches previous last name"))


class ConnectionCard(Document):
	"""What a guest filled in on the Connection Card web form, and the person Churchit found or added for it."""

	def validate(self):
		self.full_name = get_display_name(self.first_name, self.last_name)
		self.keep_only_a_published_church()

	def after_insert(self):
		frappe.enqueue_doc(self.doctype, self.name, "process", enqueue_after_commit=True)

	def keep_only_a_published_church(self):
		"""Keep the church the web form carried from the page's address only when it names a published church.

		Anyone can type the address, so any other church is dropped and `ensure_church` settles it.
		"""
		if not (self.is_new() and frappe.flags.in_web_form and self.church):
			return
		if not is_multi_church() or not frappe.db.get_value("Church", self.church, "publish"):
			self.church = None

	def process(self):
		"""Background job: link or add the card's person and start their follow-up, once.

		It runs as whoever sent the card, a guest for the web form, so its writes skip permissions.
		"""
		if frappe.db.get_value(self.doctype, self.name, "status", for_update=True) != NEW:
			return
		if self.website:
			self.db_set("status", SPAM)
			return

		person = self.find_or_add_person()
		self.db_set({"person": person, "status": PROCESSED, "processed_on": now_datetime()})
		# A trigger row with no value matches any card, so a card with no interests still starts those.
		for interest in [row.interest for row in self.interests] or [None]:
			start_cards(CONNECTION_CARD, person, self.church, value=interest, source=self)

	def find_or_add_person(self) -> str:
		"""The one person who strongly matches the card, else a new Visitor with the matches left for staff."""
		matches = find_matches(self.candidate, church=self.church)
		strong = [match for match in matches if is_strong_match(match)]
		if len(strong) == 1:
			return strong[0].person

		person = self.add_person()
		record_possible_duplicates(person, matches)
		return person

	@property
	def candidate(self) -> dict:
		"""The card as the matcher reads a person."""
		return {
			"first_name": self.first_name,
			"last_name": self.last_name,
			"emails": [self.email] if self.email else [],
			"phones": [self.phone] if self.phone else [],
		}

	def add_person(self) -> str:
		"""Add a Visitor from the card. An email or phone another person holds is shared from them."""
		person = frappe.get_doc(
			{
				"doctype": "Person",
				"first_name": self.first_name,
				"last_name": self.last_name,
				"church": self.church,
				"membership": VISITOR,
				"source": CONNECTION_CARD,
			}
		)
		for fieldname, child_doctype, value_field, value in (
			(EMAIL_FIELD, EMAIL_DOCTYPE, "email_address", self.email),
			(PHONE_FIELD, PHONE_DOCTYPE, "phone_number", self.phone),
		):
			if value:
				person.append(
					fieldname,
					{
						value_field: value,
						"shared_from_type": person.doctype,
						"shared_from": get_other_holder(person, child_doctype, value_field, value),
					},
				)
		return person.insert(ignore_permissions=True).name


def is_strong_match(match: Match) -> bool:
	"""True when the match shares the card's email or phone, first name and last name.

	A nickname counts as the first name and a previous last name as the last name.
	"""
	reasons = set(match.reasons)
	return all(reasons & wanted for wanted in (SHARED_CONTACT, SAME_FIRST_NAME, SAME_LAST_NAME))
