# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

from collections import Counter

import frappe
from dateutil.relativedelta import relativedelta
from frappe import _
from frappe.model.document import Document
from frappe.utils import cstr, get_link_to_form, getdate, nowdate

from churchit.church_foundations import church_access
from churchit.church_people.doctype.marital_status.marital_status import MARRIED, WIDOWED, WITH_SPOUSE
from churchit.church_people.member_access import refuse_member_edits_beyond_personal_details
from churchit.church_people.membership import INACTIVE, MEMBER
from churchit.church_scope import is_multi_church
from churchit.contacts import primary_email, validate_contact_tables


def years_since(date):
	"""Whole years from ``date`` until today; 0 when ``date`` is empty."""
	if not date:
		return 0
	return max(relativedelta(getdate(nowdate()), getdate(date)).years, 0)


class Person(Document):
	def onload(self):
		self.set_onload("is_primary_contact", self.is_primary_contact)

	def on_update(self):
		self.sync_church_permission()
		self.sync_family_church()
		self.sync_family_members()

	def sync_family_members(self):
		"""Put this person on their family's roster, bringing a newly linked or moving spouse along.

		The rosters follow the person, so the right to edit this person is the one
		that counts, not a right to the Family. Leave the old family before the new
		one is saved, so the new one's labels see who its members are.
		"""
		before = self.get_doc_before_save()
		spouse = self.spouse_moving_along or self.spouse_without_family
		if before and before.family and before.family != self.family:
			self.remove_from_family(before.family, spouse)
		if self.family:
			family = frappe.get_doc("Family", self.family)
			for person in filter(None, (self.name, spouse)):
				if not any(member.member == person for member in family.members):
					family.append("members", {"member": person})
			if self.flags.pop("leads_new_family", False):
				family.primary_contact = self.name
			family.save(ignore_permissions=True)

	def remove_from_family(self, family_name, spouse=None):
		"""Drop this person, and *spouse* when given, from *family_name*'s members, when that family still exists."""
		if not frappe.db.exists("Family", family_name):
			return
		family = frappe.get_doc("Family", family_name)
		for member in [member for member in family.members if member.member in (self.name, spouse)]:
			family.remove(member)
		if family.primary_contact in (self.name, spouse):
			family.primary_contact = None
		family.save(ignore_permissions=True)

	@property
	def is_primary_contact(self):
		return (
			bool(self.family) and frappe.db.get_value("Family", self.family, "primary_contact") == self.name
		)

	def sync_church_permission(self):
		"""Keep the linked user's Church permission pointed at this person's church.

		Only a change of user or church re-points it, so an administrator's
		edits to the permission survive unrelated saves.
		"""
		if not is_multi_church() or not self.user or not self.church:
			return
		before = self.get_doc_before_save()
		if before and before.user == self.user and before.church == self.church:
			return
		church_access.set_user_church(self.user, self.church)

	def sync_family_church(self):
		"""Move the family with its primary contact when they change church."""
		if not is_multi_church() or not self.is_primary_contact:
			return
		if frappe.db.get_value("Family", self.family, "church") != self.church:
			frappe.db.set_value("Family", self.family, "church", self.church)

	def before_save(self):
		# We set this here since virtual fields do not work with
		#   View Settings -> Title Field as of 2025-08-26
		self.full_name = f"{self.first_name}" + ((" " + self.last_name) if self.last_name else "")
		self.marriage_years = years_since(self.anniversary)
		self._recompute_age()
		self.update_membership_from_life_events()

	def _recompute_age(self):
		"""Set age from the Birth row in life_events. Cleared when no Birth event."""
		birth = next(
			(le for le in (self.life_events or []) if le.event_type == "Birth" and le.date),
			None,
		)
		self.age = years_since(birth.date) if birth else None

	def update_membership_from_life_events(self):
		"""Make a person with a new Membership event a Member, and one with a new Death event Inactive.

		Only an added row counts, so a former member keeps the type the office gives them.
		"""
		added = self.added_life_event_types
		if "Membership" in added:
			self.membership = MEMBER
		if "Death" in added:
			self.membership_status = INACTIVE
			self.inactive_reason = self.inactive_reason or _("Deceased")
			self.widow_spouse()
		if self.membership_status != INACTIVE:
			self.inactive_reason = None

	def widow_spouse(self):
		"""Make the spouse of a person who has died Widowed, when they are still married to them."""
		if not self.spouse:
			return
		spouse = frappe.db.get_value("Person", self.spouse, ["spouse", "marital_status"], as_dict=True)
		if spouse and spouse.spouse == self.name and spouse.marital_status == MARRIED:
			frappe.db.set_value("Person", self.spouse, "marital_status", WIDOWED)

	@property
	def added_life_event_types(self):
		"""Life event types with more rows now than before this save."""
		before = self.get_doc_before_save()
		earlier = Counter(row.event_type for row in (before.life_events if before else []))
		now = Counter(row.event_type for row in self.life_events or [])
		return set(now - earlier)

	def on_trash(self):
		if self.spouse:
			self.unlink_spouse(self.spouse)
		# A bulk delete takes the Family out first, and then there is no member row left to remove.
		if self.family:
			self.remove_from_family(self.family)

	def validate(self):
		refuse_member_edits_beyond_personal_details(self)
		# Normalise the emails / phones / addresses tables before anything else
		# reads a primary value off them.
		validate_contact_tables(self)

		self.sync_spouse()
		self.share_family_with_spouse()

	def sync_spouse(self):
		"""Mirror a marriage onto the spouse, and end it on both people when this person is no longer married.

		A widowed person keeps the spouse and anniversary, and the late spouse is left as they are.
		"""
		before = self.get_doc_before_save()
		previous = before.spouse if before else None
		if self.spouse and self.marital_status in WITH_SPOUSE:
			if previous and previous != self.spouse:
				self.unlink_spouse(previous)
			if self.marital_status == MARRIED:
				self.link_spouse()
			return
		if previous:
			self.unlink_spouse(previous, self.marital_status)
			self.anniversary = None
		self.spouse = None

	@property
	def has_new_spouse(self):
		before = self.get_doc_before_save()
		return bool(self.spouse) and (not before or before.spouse != self.spouse)

	@property
	def spouse_without_family(self):
		"""A newly linked spouse with no family yet, who joins this person's."""
		if self.has_new_spouse and not frappe.db.get_value("Person", self.spouse, "family"):
			return self.spouse

	@property
	def spouse_moving_along(self):
		"""The spouse who shared this person's old family, and so moves with them to the new one."""
		before = self.get_doc_before_save()
		has_moved = self.family and before and before.family and before.family != self.family
		if (
			has_moved
			and self.spouse
			and frappe.db.get_value("Person", self.spouse, "family") == before.family
		):
			return self.spouse

	@property
	def household_name(self):
		return f"{self.last_name} - {self.first_name}" if self.last_name else self.first_name

	def share_family_with_spouse(self):
		"""Give a newly linked couple one family: this person's, the spouse's, or a new one this person heads."""
		if self.family or not self.has_new_spouse:
			return
		self.family = frappe.db.get_value("Person", self.spouse, "family")
		if self.family:
			return
		family = frappe.get_doc({"doctype": "Family", "family_name": self.household_name})
		family.insert(ignore_permissions=True)
		self.family = family.name
		self.flags.leads_new_family = True
		if not frappe.flags.in_import:
			family_link = get_link_to_form("Family", family.name, family.family_name)
			frappe.msgprint(f"👨‍👩‍👧‍👦 New family created: {family_link}")

	def link_spouse(self):
		"""Point the spouse back at this person as Married, sharing the anniversary.

		A spouse who already points here keeps their own marital status, so a widow stays Widowed.
		"""
		spouse = frappe.get_doc("Person", self.spouse)
		self.anniversary = self.anniversary or spouse.anniversary
		values = {}
		if spouse.spouse != self.name:
			if spouse.spouse:
				spouse.unlink_spouse(spouse.spouse)
			values.update(spouse=self.name, marital_status=MARRIED)
			if not frappe.flags.in_import:
				frappe.msgprint(f"Spouses have been linked:<br>{self.full_name} 👩‍❤️‍👨 {spouse.full_name}")
		if cstr(spouse.anniversary) != cstr(self.anniversary):
			values.update(anniversary=self.anniversary, marriage_years=years_since(self.anniversary))
		if values:
			frappe.db.set_value("Person", spouse.name, values)

	def unlink_spouse(self, spouse_name, marital_status=None):
		"""Clear the spouse and anniversary on the other person, if they still point here, and give them *marital_status*."""
		spouse = frappe.db.get_value("Person", spouse_name, ["name", "spouse", "full_name"], as_dict=True)
		if not spouse or spouse.spouse != self.name:
			return
		frappe.db.set_value(
			"Person",
			spouse.name,
			{"spouse": None, "marital_status": marital_status, "anniversary": None, "marriage_years": 0},
		)
		if not frappe.flags.in_import:
			frappe.msgprint(f"Spouses have been unlinked:<br>{self.full_name} 💔 {spouse.full_name}")

	@frappe.whitelist()
	def new_family_from_person(self):
		# Check if a family with this person's name already exists
		existing_family = frappe.db.exists("Family", {"family_name": self.household_name})

		if existing_family:
			# Set this person's family to the existing one
			self.family = existing_family
			self.save()
			family_link = get_link_to_form("Family", existing_family, self.household_name)
			frappe.msgprint(
				f"⚠️ The {family_link} family already exists. This person has been added to that family."
			)

			return  # Don't create a new family

		doc = frappe.new_doc("Family")
		doc.family_name = self.household_name
		doc.save()
		self.set("family", doc.name)
		self.flags.leads_new_family = True
		self.save()
		self.reload()
		family_link = get_link_to_form("Family", doc.name, doc.family_name)
		frappe.msgprint(f"👨‍👩‍👧‍👦 New family created: {family_link}")

	@frappe.whitelist()
	def invite_to_portal(self):
		# Block invitation if outgoing email is not configured
		if not frappe.db.exists("Email Account", {"enable_outgoing": 1, "default_outgoing": 1}):
			frappe.throw(
				_(
					"Outgoing email is not configured. Please set up a default "
					"<a href='/app/email-account'>Email Account</a> before inviting portal users."
				),
				title=_("Email Not Configured"),
			)

		# The invitation goes to the address marked primary in the Emails table.
		email = primary_email(self)
		if not email:
			frappe.throw(
				_("Add an email address on the Contact tab before inviting this person to the portal."),
				title=_("No Email Address"),
			)

		# Check if user already exists with this email
		user = frappe.db.exists("User", {"email": email})

		if not user:
			# Create a new portal user
			new_user = frappe.new_doc("User")
			new_user.email = email
			new_user.first_name = self.first_name
			new_user.last_name = self.last_name
			new_user.send_welcome_email = 1
			new_user.enabled = 1
			new_user.append("role_profiles", {"role_profile": "Church User"})
			new_user.save(ignore_permissions=True)

			# Update Person to mark as portal user
			self.user = new_user.name
			self.save(ignore_permissions=True)

			frappe.msgprint(
				f"👤 Portal user created for <a href='/app/user/{new_user.name}'>{self.full_name}</a> "
				f"and an invitation email was sent to {email}.",
				title="Invitation Sent",
				indicator="green",
			)
		else:
			# User already exists, just update the user field
			self.user = user
			self.save(ignore_permissions=True)
			frappe.msgprint(
				f"⚠️ Portal user <a href='/app/user/{user}'>{user}</a> already exists. User is now linked to this person."
			)


def refresh_ages():
	"""Daily scheduler: bring each stored age and years married up to today.

	Both are worked out on save, so they would otherwise lag a birthday or an anniversary until the next edit.
	"""
	# church-scope: scheduled daily job: recomputes stored figures, shows nobody anything
	births = frappe.get_all(
		"Life Event",
		filters={"parenttype": "Person", "event_type": "Birth", "date": ("is", "set")},
		fields=["parent", "date"],
		order_by="idx asc",
	)
	birth_dates = {}
	for birth in births:
		birth_dates.setdefault(birth.parent, birth.date)

	for person in frappe.get_all("Person", fields=["name", "age", "anniversary", "marriage_years"]):
		current = {
			"age": years_since(birth_dates.get(person.name)),
			"marriage_years": years_since(person.anniversary),
		}
		changed = {field: value for field, value in current.items() if (person[field] or 0) != value}
		if changed:
			frappe.db.set_value("Person", person.name, changed, update_modified=False)


def get_user_dashboard_data(data):
	data["transactions"].append({"label": "Church", "items": ["Person"]})
	data["non_standard_fieldnames"] = data.get("non_standard_fieldnames", {})
	data["non_standard_fieldnames"]["Person"] = "user"
	return data


def get_list_context(context):
	# Only show documents related to the active user
	context.filters = {"user": frappe.session.user}
	# Sort the portal list view by status descending
	context.order_by = "modified desc"
	return context
