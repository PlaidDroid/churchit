# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Server side of the Check-In Station page."""

import re

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, now_datetime, today

from churchit.church_ministries.doctype.function_check_in.function_check_in import (
	check_in_persons,
	is_shared_function,
	print_name_tags,
)
from churchit.church_people import household
from churchit.church_people.doctype.people_settings.people_settings import SHARE_DEFAULTS
from churchit.church_people.doctype.person.person import SOURCE_CHECK_IN
from churchit.church_people.membership import VISITOR, living_people_filter
from churchit.church_scope import (
	church_query_filters,
	default_church,
	has_church_access,
	is_people_directory_private,
	scoped,
)
from churchit.contacts import phone_digits

RESULT_LIMIT = 12


@frappe.whitelist()
def get_station_context():
	"""Functions around today plus the printing settings the station shows."""
	frappe.has_permission("Function Check-In", "create", throw=True)
	settings = frappe.get_single("Check-In Settings").for_church(default_church())
	today = getdate()
	functions = frappe.get_all(
		"Function",
		filters={
			"start_date": ["between", [add_days(today, -1), add_days(today, 7)]],
			"auto_repeat": 0,
			**church_query_filters({}),
		},
		fields=["name", "function_name", "start_date", "start_time"],
		order_by="start_date asc, start_time asc",
	)
	return {
		"today": str(today),
		"functions": functions,
		"name_tag_printing": settings.name_tag_printing,
		"printer_name": settings.printer_name,
		"security_codes": settings.security_codes,
	}


@frappe.whitelist()
def search_people(query: str | None, function: str | None = None):
	"""People matching *query* by name, nickname, family name or phone digits, grouped by family."""
	frappe.has_permission("Person", throw=True)
	query = (query or "").strip()
	if len(query) < 2:
		return []
	scope = get_people_scope(function)
	digits = phone_digits(query)
	if len(digits) >= 4 and not re.search(r"[a-zA-Z]", query):
		matched = find_by_phone(digits, scope)
	else:
		matched = find_by_name(query.split(), scope)
	return group_by_family(matched, scope)


@frappe.whitelist()
def find_card_holder(person: str, function: str | None = None):
	"""The family of the person whose member card was scanned, found by the same rules as a search."""
	frappe.has_permission("Person", throw=True)
	return group_by_family([person], get_people_scope(function))


def get_people_scope(function):
	"""Person filters for the station: every church unless people are private, or at a shared function.

	A station has to find whoever walks in, whichever church they belong to. With
	a private directory that still holds at a joint service, so the results carry
	names, ages and photos only, never contact details.
	"""
	if function:
		frappe.has_permission("Function", doc=function, throw=True)
	if not is_people_directory_private() or (function and is_shared_function(function)):
		return {}
	return church_query_filters({})


def find_by_name(words, scope):
	person = frappe.qb.DocType("Person")
	family = frappe.qb.DocType("Family")
	query = (
		frappe.qb.from_(person)
		.left_join(family)
		.on(person.family == family.name)
		.select(person.name)
		.orderby(person.last_name)
		.orderby(person.first_name)
		.limit(RESULT_LIMIT)
	)
	for word in words:
		like = f"%{word}%"
		query = query.where(
			person.full_name.like(like) | person.nickname.like(like) | family.family_name.like(like)
		)
	if scope:
		query = scoped(query, person, {})
	return query.run(pluck=True)


def find_by_phone(digits, scope):
	"""Match phone numbers on their stored digits, however they are formatted."""
	phones = frappe.get_all(
		"Phone Number",
		filters={"digits": ["like", f"%{digits}%"], "parenttype": ["in", ["Person", "Family"]]},
		fields=["parent", "parenttype"],
		limit=RESULT_LIMIT,
	)
	persons = [row.parent for row in phones if row.parenttype == "Person"]
	families = [row.parent for row in phones if row.parenttype == "Family"]
	if families:
		# church-scope: *scope* is get_people_scope's answer for this station
		persons += frappe.get_all("Person", filters={"family": ["in", families], **scope}, pluck="name")
	return persons


def group_by_family(matched, scope):
	"""Expand living matches to whole families so siblings can be checked in together."""
	if not matched:
		return []
	is_living = living_people_filter()
	# church-scope: *scope* is get_people_scope's answer for this station
	families = frappe.get_all(
		"Person",
		filters=[{"name": ["in", matched], "family": ["is", "set"], **scope}, is_living],
		pluck="family",
		distinct=True,
	)
	or_filters = [["name", "in", matched]]
	if families:
		or_filters.append(["family", "in", families])
	# The church and living filters are the backstop: every person the station lists
	# passes them, whichever of the searches above found them.
	people = frappe.get_all(
		"Person",
		filters=[scope, is_living],
		or_filters=or_filters,
		fields=[
			"name",
			"first_name",
			"last_name",
			"full_name",
			"age",
			"photo",
			"family",
		],
		order_by="age desc, first_name asc",
	)
	family_rows = frappe.get_all(
		"Family", filters={"name": ["in", families]}, fields=["name", "family_name", "primary_contact"]
	)
	family_names = {family.name: family.family_name for family in family_rows}
	primary_contacts = {family.primary_contact for family in family_rows}
	# The primary contact leads each family, then the rest from oldest to youngest
	people.sort(key=lambda row: row.name not in primary_contacts)
	groups = {}
	for row in people:
		row.matched = row.name in matched
		group = groups.setdefault(
			row.family or row.name,
			{"family": row.family, "family_name": family_names.get(row.family), "members": []},
		)
		group["members"].append(row)
	return sorted(groups.values(), key=lambda group: group["members"][0].full_name)


@frappe.whitelist()
def get_check_ins(function: str):
	"""Who is checked in to *function*, newest first, whichever church they belong to."""
	frappe.has_permission("Function Check-In", throw=True)
	frappe.has_permission("Function", doc=function, throw=True)
	# church-scope: keyed to one function the caller may read; its roster spans churches on purpose
	check_in = frappe.qb.DocType("Function Check-In")
	person = frappe.qb.DocType("Person")
	query = (
		frappe.qb.from_(check_in)
		.join(person)
		.on(check_in.person == person.name)
		.select(check_in.name, check_in.person, check_in.security_code, check_in.creation, person.full_name)
		.where(check_in.function == function)
		.orderby(check_in.creation, order=frappe.qb.desc)
	)
	return query.run(as_dict=True)


@frappe.whitelist()
def get_pickup_families(code: str) -> list[dict]:
	"""The families of the people checked in today with pickup tag *code*, with who may collect them.

	The check-ins the caller may read are the gate. The people they name are read whatever their church,
	as at a shared function the station checks in people of every church. Each child carries its check-in
	and, once someone took them, who and when.
	"""
	frappe.has_permission("Function Check-In", throw=True)
	check_ins = frappe.get_list(
		"Function Check-In",
		filters={"security_code": code.strip().upper(), "creation": [">=", today()]},
		fields=[
			"name as check_in",
			"person",
			"function.function_name as function_name",
			"collected_by",
			"collected_by_name",
			"collected_at",
		],
		order_by="creation asc",
	)
	people = get_living_people([row.person for row in check_ins], ["family"])
	families = {}
	for row in check_ins:
		if row.person in people:
			child = frappe._dict({**people[row.person], **row})
			families.setdefault(child.family, []).append(child)
	return [get_pickup_family(family, children) for family, children in families.items()]


def get_pickup_family(family: str | None, children: list[dict]) -> dict:
	"""*family*'s checked-in *children* with its living Adults and authorized pickups, who may collect them."""
	result = {"family": family, "family_name": None, "children": children, "adults": [], "pickups": []}
	if not family:
		return result

	# church-scope: the family of a check-in the caller may read
	pickups = frappe.get_all(
		"Authorized Pickup",
		filters={"parenttype": "Family", "parent": family},
		fields=["person", "relationship", "notes"],
		order_by="idx asc",
	)
	adults = household.members(family, [household.ADULT])
	people = get_living_people(adults + [row.person for row in pickups])
	result.update(
		family_name=frappe.db.get_value("Family", family, "family_name"),
		adults=[people[adult] for adult in adults],
		pickups=[{**row, **people[row.person]} for row in pickups if row.person in people],
	)
	return result


@frappe.whitelist(methods=["POST"])
def release_children(code: str, check_ins: str | list[str], collector: str) -> list[dict]:
	"""Record that *collector* took the children of *check_ins*, checked in today with pickup tag *code*.

	The families the code finds are read again here, so only someone they let collect, and only a child
	of this code, is ever released. Returns the families as they stand after.
	"""
	frappe.has_permission("Function Check-In", "write", throw=True)
	waiting = set(frappe.parse_json(check_ins) if isinstance(check_ins, str) else check_ins)
	for family in get_pickup_families(code):
		collectors = {person["name"]: person["full_name"] for person in family["adults"] + family["pickups"]}
		for child in family["children"]:
			if child.check_in in waiting:
				release(child.check_in, collector, collectors)
				waiting.discard(child.check_in)
	if waiting:
		frappe.throw(
			_("Only a child checked in today with this code can be released."), frappe.PermissionError
		)
	return get_pickup_families(code)


def release(check_in: str, collector: str, collectors: dict[str, str]) -> None:
	"""Store who took one child, once: the row is locked, so two volunteers cannot both release them."""
	if collector not in collectors:
		frappe.throw(
			_("This person may not collect these children. Ask a member of staff."), frappe.PermissionError
		)
	if frappe.db.get_value("Function Check-In", check_in, "collected_by", for_update=True):
		frappe.throw(_("This child has already been collected."))
	doc = frappe.get_doc("Function Check-In", check_in)
	doc.update(
		{
			"collected_by": collector,
			"collected_by_name": collectors[collector],
			"collected_at": now_datetime(),
			"released_by": frappe.session.user,
		}
	)
	doc.save()


def get_living_people(names: list[str], fields: list[str] | None = None) -> dict:
	"""{name: name, full name and photo} of the living people among *names*, whatever their church."""
	if not names:
		return {}
	# church-scope: the people that check-ins or a family the caller may read name
	people = frappe.get_all(
		"Person",
		filters=[{"name": ["in", names]}, living_people_filter()],
		fields=["name", "full_name", "photo", *(fields or [])],
	)
	return {person.name: person for person in people}


@frappe.whitelist(methods=["POST"])
def check_in(function: str, persons: str | list[str], print_tags: bool = False):
	"""Check people in and, when asked, hand their name tags to the printer path."""
	names = check_in_persons(function, persons)
	result = {"check_ins": names}
	if cint(print_tags):
		result["print"] = print_name_tags(check_ins=names)
	return result


@frappe.whitelist(methods=["POST"])
def undo_check_in(name: str):
	frappe.delete_doc("Function Check-In", name)


@frappe.whitelist(methods=["POST"])
def add_visitor(
	first_name: str | None, last_name: str | None = None, phone: str | None = None, family: str | None = None
):
	"""Create a Person on the spot so a first-time visitor can be checked in.

	Checking people in is the right that counts: a check-in volunteer holds no
	Person create, which would otherwise ask them to read every Person lookup too.
	A volunteer adds a visitor only to a Family of a church they can see.
	The visitor never saw the directory's share choices, so they share nothing.
	"""
	frappe.has_permission("Function Check-In", "create", throw=True)
	if not (first_name or "").strip():
		frappe.throw(_("First name is required."))
	if family and not has_church_access(frappe.session.user, frappe.db.get_value("Family", family, "church")):
		frappe.throw(_("{0} belongs to another church.").format(family), frappe.PermissionError)
	person = frappe.get_doc(
		{
			"doctype": "Person",
			"first_name": first_name.strip(),
			"last_name": (last_name or "").strip(),
			"family": family,
			"source": SOURCE_CHECK_IN,
			"membership": VISITOR,
			**dict.fromkeys(SHARE_DEFAULTS, 0),
		}
	)
	if phone:
		person.append("phones", {"phone_number": phone.strip(), "is_primary": 1})
	person.insert(ignore_permissions=True)
	return {"name": person.name, "full_name": person.full_name, "family": person.family}
