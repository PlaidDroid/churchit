"""Church Directory Report"""

import calendar

import frappe
from frappe import _
from frappe.query_builder.functions import Coalesce
from frappe.utils import today as frappe_today
from frappe.utils.pdf import inline_private_images
from frappe.utils.weasyprint import import_weasyprint
from markupsafe import Markup
from pypika import Order

from churchit.church_missions.doctype.missionary_status.missionary_status import is_current_missionary
from churchit.church_people.membership import ACTIVE, is_active_member
from churchit.church_people.report.church_directory_report.booklet import impose_booklet
from churchit.church_scope import church_filter, root_church, scoped
from churchit.contacts import primary_address_query, primary_email_query, primary_phone_query
from churchit.query import Day, Month
from churchit.scripture import get_printable_passage
from churchit.utils import set_report_link_titles

ADDRESS_FIELDS = ("address_line1", "address_line2", "city", "state", "pincode")


def execute(filters=None):
	group_by_family = frappe.utils.cint((filters or {}).get("group_by_family", 1))
	if group_by_family:
		columns = get_columns()
		data = get_data(filters)
	else:
		columns = get_individual_columns()
		data = get_individual_data(filters)
	set_report_link_titles(columns, data)
	return columns, data


def get_columns():
	return [
		{"fieldname": "family", "fieldtype": "Link", "label": _("Family"), "options": "Family", "width": 200},
		{
			"fieldname": "head_of_household",
			"fieldtype": "Link",
			"label": _("Head of Household"),
			"options": "Person",
			"width": 180,
		},
		{"fieldname": "city", "fieldtype": "Data", "label": _("City"), "width": 140},
		{"fieldname": "state", "fieldtype": "Data", "label": _("State"), "width": 100},
		{"fieldname": "member_count", "fieldtype": "Int", "label": _("Members"), "width": 80},
	]


def get_individual_columns():
	return [
		{"fieldname": "person", "fieldtype": "Link", "label": _("Person"), "options": "Person", "width": 200},
		{"fieldname": "family", "fieldtype": "Link", "label": _("Family"), "options": "Family", "width": 160},
		{"fieldname": "primary_phone", "fieldtype": "Data", "label": _("Phone"), "width": 130},
		{"fieldname": "email", "fieldtype": "Data", "label": _("Email"), "width": 180},
		{"fieldname": "city", "fieldtype": "Data", "label": _("City"), "width": 120},
		{"fieldname": "state", "fieldtype": "Data", "label": _("State"), "width": 100},
	]


def get_individual_data(filters):
	Person = frappe.qb.DocType("Person")

	query = (
		frappe.qb.from_(Person)
		.select(
			Person.name.as_("person"),
			Person.family,
			primary_phone_query(Person).as_("primary_phone"),
			primary_email_query(Person).as_("email"),
		)
		.orderby(Person.last_name)
		.orderby(Person.first_name)
	)

	return scoped(with_home_address(listed(query, Person, filters), Person), Person, filters).run(
		as_dict=True
	)


def get_data(filters):
	Family = frappe.qb.DocType("Family")
	Address = frappe.qb.DocType("Address")
	Person = frappe.qb.DocType("Person")

	families_query = (
		frappe.qb.from_(Family)
		.left_join(Address)
		.on(Address.name == primary_address_query(Family, "Family"))
		.select(
			Family.name.as_("family_id"),
			Family.family_name,
			Coalesce(Address.city, "").as_("city"),
			Coalesce(Address.state, "").as_("state"),
		)
		.orderby(Family.family_name)
	)
	families = scoped_families(families_query, Family, Person, filters).run(as_dict=True)

	members_query = (
		frappe.qb.from_(Person)
		.select(Person.name, Person.full_name, Person.family, Person.is_head_of_household)
		.where(Person.family.isnotnull() & (Person.family != ""))
	)

	all_members = scoped(listed(members_query, Person, filters), Person, filters).run(as_dict=True)

	members_by_family = {}
	for m in all_members:
		members_by_family.setdefault(m.family, []).append(m)

	result = []
	for family in families:
		members = members_by_family.get(family.family_id, [])
		head = next((m.name for m in members if m.is_head_of_household), None)
		if not head and members:
			head = members[0].name
		result.append(
			{
				"family": family.family_id,
				"head_of_household": head or "",
				"city": family.city,
				"state": family.state,
				"member_count": len(members),
			}
		)

	return result


@frappe.whitelist()
def get_directory_html(
	members_only: bool = 0,
	hide_non_active: bool = 0,
	group_by_family: bool = 1,
	show_photos: bool = 0,
	show_roles: bool = 0,
	show_membership: bool = 1,
	show_hoh: bool = 1,
	show_phone: bool = 1,
	show_email: bool = 1,
	show_address: bool = 1,
	show_church_image: bool = 0,
	show_birthdays: bool = 0,
	show_anniversaries: bool = 0,
	show_missionaries: bool = 0,
	blank_back_cover: bool = 0,
	show_page_numbers: bool = 0,
	include_notes_page: bool = 0,
	booklet_printing: bool = 0,
	padding_pages: int = 0,
	church: str | None = None,
):
	"""Generate the full HTML for the church directory, ready to print."""
	filters = {"church": church, "members_only": members_only, "hide_non_active": hide_non_active}
	group_by_family = frappe.utils.cint(group_by_family)
	show_photos = frappe.utils.cint(show_photos)
	show_roles = frappe.utils.cint(show_roles)
	show_membership = frappe.utils.cint(show_membership)
	show_hoh = frappe.utils.cint(show_hoh)
	show_phone = frappe.utils.cint(show_phone)
	show_email = frappe.utils.cint(show_email)
	show_address = frappe.utils.cint(show_address)
	show_church_image = frappe.utils.cint(show_church_image)
	show_birthdays = frappe.utils.cint(show_birthdays)
	show_anniversaries = frappe.utils.cint(show_anniversaries)
	show_missionaries = frappe.utils.cint(show_missionaries)
	blank_back_cover = frappe.utils.cint(blank_back_cover)
	show_page_numbers = frappe.utils.cint(show_page_numbers)
	include_notes_page = frappe.utils.cint(include_notes_page)
	booklet_printing = frappe.utils.cint(booklet_printing)
	padding_pages = min(frappe.utils.cint(padding_pages), 3)

	church_name = header_church(filters)
	church_doc = frappe.get_doc("Church", church_name) if church_name else None
	church_address = None
	if church_doc and church_doc.address:
		church_address = frappe.get_doc("Address", church_doc.address)
	church_verse = get_printable_passage(church_doc.church_verse, church_doc.name) if church_doc else None
	website = church_doc.printed_website if church_doc else None

	Family = frappe.qb.DocType("Family")
	Address = frappe.qb.DocType("Address")
	Person = frappe.qb.DocType("Person")

	families_query = (
		frappe.qb.from_(Family)
		.left_join(Address)
		.on(Address.name == primary_address_query(Family, "Family"))
		.select(
			Family.name.as_("family_id"),
			Family.family_name,
			Family.photo.as_("family_photo"),
			*get_address_columns(Address),
		)
		.orderby(Family.family_name)
	)
	families = scoped_families(families_query, Family, Person, filters).run(as_dict=True)

	members_query = (
		frappe.qb.from_(Person)
		.select(
			Person.name.as_("person_name"),
			Person.full_name,
			Person.last_name,
			primary_phone_query(Person).as_("primary_phone"),
			primary_email_query(Person).as_("email"),
			Person.membership,
			Person.is_head_of_household,
			Person.gender,
			Person.spouse,
			Person.photo,
			Person.family,
		)
		.where(Person.family.isnotnull() & (Person.family != ""))
		.orderby(Person.family)
		.orderby(Person.is_head_of_household, order=Order.desc)
		.orderby(Person.last_name)
		.orderby(Person.first_name)
	)
	if show_missionaries:
		members_query = without_missionaries(members_query, Person, filters)

	all_members = scoped(
		with_home_address(listed(members_query, Person, filters), Person), Person, filters
	).run(as_dict=True)

	# Fetch active positions if requested
	roles_by_person = {}
	if show_roles:
		today = frappe_today()
		Position = frappe.qb.DocType("Position")
		PositionType = frappe.qb.DocType("Position Type")
		active_positions = (
			frappe.qb.from_(Position)
			.join(Person)
			.on(Person.name == Position.parent)
			.left_join(PositionType)
			.on(PositionType.name == Position.position)
			.select(
				Position.parent.as_("person_name"),
				Coalesce(PositionType.position, Position.position).as_("position"),
			)
			.where(
				(Position.parenttype == "Person")
				& Position.position.isnotnull()
				& (Position.start_date <= today)
				& (Position.end_date.isnull() | (Position.end_date >= today))
			)
			.orderby(Position.parent)
			.orderby(Position.start_date)
			.run(as_dict=True)
		)
		for row in active_positions:
			roles_by_person.setdefault(row.person_name, []).append(row.position)

	for m in all_members:
		m["positions"] = roles_by_person.get(m.person_name, [])

	members_by_family = {}
	for m in all_members:
		members_by_family.setdefault(m.family, []).append(m)

	# Compute each non-HoH member's relation to the head of household. Uses
	# the spouse field for husband/wife, then falls back to the Person
	# Relation child table (children, parents, siblings, etc.).
	hoh_by_family = {}
	for family_id, members in members_by_family.items():
		hoh = next((m for m in members if m.is_head_of_household), None) or (members[0] if members else None)
		if hoh:
			hoh_by_family[family_id] = hoh

	hoh_names = [h.person_name for h in hoh_by_family.values()]
	hoh_relations = {}
	if hoh_names:
		Relation = frappe.qb.DocType("Person Relation")
		for row in (
			frappe.qb.from_(Relation)
			.select(
				Relation.parent.as_("hoh"),
				Relation.person.as_("other"),
				Relation.type.as_("relation_type"),
			)
			.where((Relation.parenttype == "Person") & Relation.parent.isin(hoh_names))
			.run(as_dict=True)
		):
			hoh_relations[(row.hoh, row.other)] = row.relation_type

	for family_id, members in members_by_family.items():
		hoh = hoh_by_family.get(family_id)
		if not hoh:
			continue
		for m in members:
			if m.person_name == hoh.person_name:
				m["relation_to_hoh"] = ""
				continue
			if hoh.spouse and m.person_name == hoh.spouse:
				m["relation_to_hoh"] = (
					"Wife" if m.gender == "Female" else "Husband" if m.gender == "Male" else "Spouse"
				)
			else:
				m["relation_to_hoh"] = hoh_relations.get((hoh.person_name, m.person_name), "")

	individuals_query = (
		frappe.qb.from_(Person)
		.select(
			Person.name.as_("person_name"),
			Person.full_name,
			Person.last_name,
			primary_phone_query(Person).as_("primary_phone"),
			primary_email_query(Person).as_("email"),
			Person.membership,
			Person.photo,
		)
		.where(Person.family.isnull() | (Person.family == ""))
		.orderby(Person.last_name)
		.orderby(Person.first_name)
	)
	if show_missionaries:
		individuals_query = without_missionaries(individuals_query, Person, filters)

	individuals_raw = scoped(
		with_home_address(listed(individuals_query, Person, filters), Person), Person, filters
	).run(as_dict=True)

	for p in individuals_raw:
		p["positions"] = roles_by_person.get(p.person_name, [])
		p["is_head_of_household"] = 0

	all_people = list(all_members) + list(individuals_raw)

	# Build merged sorted entry list
	all_entries = []

	hidden_head_families = get_families_with_hidden_head(frappe.utils.cint(hide_non_active))
	if group_by_family:
		for family in families:
			family_name = listed_family_name(family.family_name, family.family_id in hidden_head_families)
			members = members_by_family.get(family.family_id, [])
			for member in members:
				member["has_own_address"] = any(member[field] != family[field] for field in ADDRESS_FIELDS)
			if members:
				all_entries.append(
					{
						"sort_name": family_name,
						"display_name": family_name + " Family",
						"is_individual": False,
						"family_photo": family.family_photo,
						**{field: family[field] for field in ADDRESS_FIELDS},
						"members": members,
					}
				)

	# People without a family, or everyone in a flat list, are entries of their own
	for person in individuals_raw if group_by_family else all_people:
		sort_key = (person.get("last_name") or person.get("full_name") or "").strip()
		all_entries.append(
			{
				"sort_name": sort_key,
				"display_name": person.full_name,
				"is_individual": True,
				"family_photo": None,
				**{field: person[field] for field in ADDRESS_FIELDS},
				"members": [person],
			}
		)

	all_entries.sort(key=lambda e: (e["sort_name"] or "").upper())

	# ── Birthdays ────────────────────────────────────────────────
	birthdays = []
	if show_birthdays:
		LifeEvent = frappe.qb.DocType("Life Event")
		birthdays_query = (
			frappe.qb.from_(Person)
			.join(LifeEvent)
			.on(
				(LifeEvent.parent == Person.name)
				& (LifeEvent.parenttype == "Person")
				& (LifeEvent.event_type == "Birth")
			)
			.select(
				Person.full_name,
				LifeEvent.date.as_("birthday"),
				Month(LifeEvent.date).as_("birth_month"),
				Day(LifeEvent.date).as_("birth_day"),
			)
			.where(LifeEvent.date.isnotnull())
			.orderby(Month(LifeEvent.date))
			.orderby(Day(LifeEvent.date))
			.orderby(Person.last_name)
			.orderby(Person.first_name)
		)

		raw_birthdays = scoped(listed(birthdays_query, Person, filters), Person, filters).run(as_dict=True)
		for row in raw_birthdays:
			row["month_name"] = calendar.month_name[int(row.birth_month)]
			row["month_day"] = f"{calendar.month_name[int(row.birth_month)]} {int(row.birth_day)}"
			birthdays.append(row)

	# ── Anniversaries ────────────────────────────────────────────
	anniversaries = []
	if show_anniversaries:
		Spouse = frappe.qb.DocType("Person").as_("spouse")
		anniversaries_query = (
			frappe.qb.from_(Person)
			.left_join(Spouse)
			.on(Spouse.name == Person.spouse)
			.select(
				Person.name.as_("person_name"),
				Person.spouse.as_("spouse_name"),
				Person.first_name.as_("person_first"),
				Person.last_name.as_("person_last"),
				Person.full_name.as_("person_full"),
				Spouse.first_name.as_("spouse_first"),
				Spouse.last_name.as_("spouse_last"),
				Spouse.full_name.as_("spouse_full"),
				Person.anniversary,
				Month(Person.anniversary).as_("ann_month"),
				Day(Person.anniversary).as_("ann_day"),
			)
			.where(Person.anniversary.isnotnull() & (Person.is_married == 1))
			.where(Coalesce(Spouse.hide_from_directory, 0) == 0)
			.where(Spouse.name.isnull() | Spouse.name.notin(deceased_people()))
			.orderby(Month(Person.anniversary))
			.orderby(Day(Person.anniversary))
			.orderby(Person.last_name)
			.orderby(Person.first_name)
		)
		if frappe.utils.cint(hide_non_active):
			anniversaries_query = anniversaries_query.where(
				Spouse.name.isnull() | (Spouse.membership_status == ACTIVE)
			)

		raw_anniversaries = scoped(listed(anniversaries_query, Person, filters), Person, filters).run(
			as_dict=True
		)
		seen_persons = set()
		for row in raw_anniversaries:
			if row.person_name in seen_persons:
				continue
			seen_persons.add(row.person_name)
			if row.spouse_name:
				seen_persons.add(row.spouse_name)
			row["month_name"] = calendar.month_name[int(row.ann_month)]
			row["month_day"] = f"{calendar.month_name[int(row.ann_month)]} {int(row.ann_day)}"
			if not row.spouse_name:
				row["display_name"] = row.person_full
			elif row.person_last == row.spouse_last:
				row["display_name"] = (
					f"{row.person_first} & {row.spouse_first} {row.person_last or ''}".strip()
				)
			else:
				row["display_name"] = f"{row.person_full} & {row.spouse_full}"
			anniversaries.append(row)

	# ── Missionaries ─────────────────────────────────────────────
	missionaries = []
	if show_missionaries:
		Missionary = frappe.qb.DocType("Missionary")
		missionaries_query = (
			frappe.qb.from_(Missionary)
			.select(
				Missionary.name,
				Missionary.title,
				Missionary.agency,
				Missionary.country,
				primary_email_query(Missionary, "Missionary").as_("email"),
				Missionary.website,
				Missionary.photo,
				Missionary.sensitive,
				Missionary.mission_statement,
			)
			.where(is_current_missionary(Missionary))
			.orderby(Missionary.title)
		)
		missionaries = scoped(missionaries_query, Missionary, filters).run(as_dict=True)
		people = get_missionary_people([m.name for m in missionaries])
		for m in missionaries:
			m["people"] = people.get(m.name, [])
		# Resolve agency hashes to human-readable agency names
		agency_names = list({m.agency for m in missionaries if m.agency})
		if agency_names:
			agency_map = {
				row.name: row.agency_name
				for row in frappe.get_all(
					"Missionary Agency",
					filters=[["name", "in", agency_names]],
					fields=["name", "agency_name"],
				)
			}
			for m in missionaries:
				if m.agency:
					m["agency"] = agency_map.get(m.agency, m.agency)

	generated_date = frappe.utils.formatdate(frappe.utils.nowdate(), "MMMM yyyy")
	church_name = church_doc.church_name if church_doc else ""
	context = {
		"church": church_doc,
		"church_address": church_address,
		"all_entries": all_entries,
		"show_church_label": False,
		"show_photos": show_photos,
		"show_roles": show_roles,
		"show_membership": show_membership,
		"show_hoh": show_hoh,
		"show_phone": show_phone,
		"show_email": show_email,
		"show_address": show_address,
		"show_church_image": show_church_image,
		"birthdays": birthdays,
		"anniversaries": anniversaries,
		"missionaries": missionaries,
		"show_birthdays": show_birthdays,
		"show_anniversaries": show_anniversaries,
		"show_missionaries": show_missionaries,
		"blank_back_cover": blank_back_cover,
		"show_page_numbers": show_page_numbers,
		"include_notes_page": include_notes_page,
		"booklet_printing": booklet_printing,
		"padding_pages": padding_pages,
		"website": website,
		"church_verse": church_verse,
		"generated_date": generated_date,
		# The footer prints the church and its date alone; the cover already names the document.
		"footer": get_css_string("  ·  ".join(part for part in (church_name, generated_date) if part)),
	}

	# Load the shipped template through the Jinja filesystem loader (path-only, so
	# no user-supplied string can ever be rendered as a template).
	return frappe.get_template(
		"churchit/church_people/report/church_directory_report/church_directory.html"
	).render(context)


@frappe.whitelist()
def download_directory_pdf(**options):
	"""The directory as a PDF, rendered here because only Chromium prints page numbers and footers."""
	booklet_printing = frappe.utils.cint(options.get("booklet_printing"))
	document = get_paginated_directory(options)
	if booklet_printing and len(document.pages) % 4:
		# Fill the last sheet with notes pages, which end on the inside back cover
		padding_pages = -len(document.pages) % 4
		document = get_paginated_directory({**options, "padding_pages": padding_pages})
	pdf = document.write_pdf(dpi=200)
	if booklet_printing:
		pdf = impose_booklet(pdf)

	frappe.local.response.filename = f"{_('Church Directory')}.pdf"
	frappe.local.response.filecontent = pdf
	frappe.local.response.type = "pdf"


def get_paginated_directory(options):
	"""The directory laid out into pages, ready to write as a PDF."""
	HTML, _CSS = import_weasyprint()
	html = inline_private_images(frappe.call(get_directory_html, **options))
	return HTML(string=html, base_url=frappe.utils.get_url()).render(dpi=200)


def get_css_string(text):
	"""*text* as a quoted CSS string, with every character that could end the string or the style escaped."""
	escaped = "".join(char if char.isalnum() or char == " " else f"\\{ord(char):x} " for char in text)
	return Markup(f'"{escaped}"')


def get_address_columns(Address):
	"""The printed address fields, blank when there is no address."""
	return [Coalesce(getattr(Address, field), "").as_(field) for field in ADDRESS_FIELDS]


def with_home_address(query, Person):
	"""*query* with each person's primary address, else their family's."""
	Family = frappe.qb.DocType("Family")
	Address = frappe.qb.DocType("Address")
	return (
		query.left_join(Family)
		.on(Family.name == Person.family)
		.left_join(Address)
		.on(Address.name == Coalesce(primary_address_query(Person), primary_address_query(Family, "Family")))
		.select(*get_address_columns(Address))
	)


def listed(query, Person, filters):
	"""*query* without the deceased, the people who asked to be left out of the directory, and those *filters* leave out."""
	filters = filters or {}
	query = query.where((Person.hide_from_directory == 0) & Person.name.notin(deceased_people()))
	if frappe.utils.cint(filters.get("members_only")):
		query = query.where(is_active_member(Person))
	if frappe.utils.cint(filters.get("hide_non_active")):
		query = query.where(Person.membership_status == ACTIVE)
	return query


def get_families_with_hidden_head(hide_non_active=False):
	"""Families whose head of household is hidden, deceased, or not Active when *hide_non_active*."""
	Person = frappe.qb.DocType("Person")
	is_hidden = (Person.hide_from_directory == 1) | Person.name.isin(deceased_people())
	if hide_non_active:
		is_hidden |= Coalesce(Person.membership_status, "") != ACTIVE
	# church-scope: only used to shorten names of families the scoped queries already listed
	heads = (
		frappe.qb.from_(Person).select(Person.family).where((Person.is_head_of_household == 1) & is_hidden)
	)
	return set(heads.run(pluck=True))


def deceased_people():
	"""Subquery of the people with a Death life event."""
	LifeEvent = frappe.qb.DocType("Life Event")
	return (
		frappe.qb.from_(LifeEvent)
		.select(LifeEvent.parent)
		.where((LifeEvent.parenttype == "Person") & (LifeEvent.event_type == "Death"))
	)


def listed_family_name(family_name, is_head_hidden):
	"""*family_name*, cut to the surname when the head it is named after is hidden ("Doe - John" to "Doe")."""
	if not is_head_hidden:
		return family_name
	return family_name.rpartition(" - ")[0].strip() or family_name


def without_missionaries(query, Person, filters):
	"""*query* without the people the missionaries section already lists."""
	Missionary = frappe.qb.DocType("Missionary")
	MissionaryPerson = frappe.qb.DocType("Missionary Person")
	missionary_people = (
		frappe.qb.from_(MissionaryPerson)
		.join(Missionary)
		.on(Missionary.name == MissionaryPerson.parent)
		.select(MissionaryPerson.person)
		.where((MissionaryPerson.parenttype == "Missionary") & is_current_missionary(Missionary))
	)
	return query.where(Person.name.notin(scoped(missionary_people, Missionary, filters)))


def get_missionary_people(missionaries):
	"""{missionary: [full names]} of the people on each missionary, in form order, without hidden or deceased people."""
	if not missionaries:
		return {}
	MissionaryPerson = frappe.qb.DocType("Missionary Person")
	Person = frappe.qb.DocType("Person")
	# church-scope: only the people of missionaries the scoped query already listed
	rows = (
		frappe.qb.from_(MissionaryPerson)
		.join(Person)
		.on(Person.name == MissionaryPerson.person)
		.select(MissionaryPerson.parent, Person.full_name)
		.where(
			(MissionaryPerson.parenttype == "Missionary")
			& MissionaryPerson.parent.isin(missionaries)
			& (Person.hide_from_directory == 0)
			& Person.name.notin(deceased_people())
		)
		.orderby(MissionaryPerson.idx)
		.run(as_dict=True)
	)
	people = {}
	for row in rows:
		people.setdefault(row.parent, []).append(row.full_name)
	return people


def header_church(filters):
	"""The church named in the directory header: the one selected or allowed, else the root."""
	churches = church_filter(filters)
	return churches[0] if churches and len(churches) == 1 else root_church()


def scoped_families(query, Family, Person, filters):
	"""Families with at least one member the user may see.

	A family belongs to its head of household's church, so a member who moved
	to a branch would otherwise vanish from that branch's directory.
	"""
	churches = church_filter(filters)
	if churches is None:
		return query
	members = frappe.qb.from_(Person).select(Person.family).where(Person.church.isin(churches))
	return query.where(Family.name.isin(members))
