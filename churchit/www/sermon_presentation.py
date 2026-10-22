"""AI was used to generate portions of this page"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import escape_html

from churchit.scripture import get_default_translation, get_passage, is_readable

# Disable caching for sermon presentation page to ensure users always see up-to-date content
no_cache = 1
allow_guest = True


def _parse_display_fields(raw):
	"""Parse display_fields stored as JSON or legacy comma-separated."""
	if not raw:
		return []
	try:
		parsed = json.loads(raw)
		if isinstance(parsed, list):
			return parsed
	except (json.JSONDecodeError, TypeError):
		return [{"fieldname": f.strip(), "show_label": 1, "is_title": 0} for f in raw.split(",") if f.strip()]
	return []


def _render_field(val, df, show_label):
	"""Render a single field value as HTML for the presentation."""
	label = df.label if df else "Field"
	fieldtype = df.fieldtype if df else "Data"

	if fieldtype in ("Text Editor", "HTML"):
		prefix = f"<p><strong>{label}:</strong></p>" if show_label else ""
		return prefix + val

	if fieldtype == "Attach Image":
		img = f'<img src="{val}" alt="{label}" style="max-width:80%;max-height:50vh;border-radius:8px;">'
		return f"<p><strong>{label}:</strong></p>{img}" if show_label else img

	if fieldtype == "Attach":
		link = f'<a href="{val}" target="_blank">{val}</a>'
		return f"<p><strong>{label}:</strong> {link}</p>" if show_label else f"<p>{link}</p>"

	if fieldtype == "Check":
		text = "Yes" if val else "No"
		return f"<p><strong>{label}:</strong> {text}</p>" if show_label else f"<p>{text}</p>"

	return f"<p><strong>{label}:</strong> {val}</p>" if show_label else f"<p>{val}</p>"


def _build_content_from_selected_fields(linked_doc, meta, field_configs):
	"""Build slide title and content HTML from explicitly selected fields."""
	fields_by_name = {df.fieldname: df for df in meta.fields}
	title = ""
	parts = []

	for config in field_configs:
		fieldname = config.get("fieldname") if isinstance(config, dict) else config
		show_label = config.get("show_label", 1) if isinstance(config, dict) else 1
		is_title = config.get("is_title", 0) if isinstance(config, dict) else 0

		val = getattr(linked_doc, fieldname, None)
		if val is None:
			continue

		if is_title:
			title = str(val)
		else:
			parts.append(_render_field(val, fields_by_name.get(fieldname), show_label))

	return title, "\n".join(parts)


def scripture_slide(reference, translation):
	"""A Scripture slide's title and verses, or its reference alone when the viewer may not read the text."""
	translation = translation or get_default_translation()
	if not is_readable(translation):
		return {"title": reference, "content": ""}
	verses = []
	for section in get_passage(reference, translation):
		for verse in section["verses"]:
			lines = escape_html(verse["text"]).replace("\n", "<br>")
			verses.append(f"<sup>{verse['number']}</sup> {lines}")
	return {
		"title": f"{reference} ({translation})",
		"content": f'<p class="slide-scripture">{" ".join(verses)}</p>',
	}


def get_public_slide(slide_type, name) -> dict | None:
	"""The fields `/beliefs` and `/missions` show of a published Belief or Missionary, for any viewer."""
	if slide_type == "Belief":
		return frappe.db.get_value(
			"Belief",
			{"name": name, "publish": 1},
			["title", "belief_statement", "bible_references"],
			as_dict=True,
		)
	if slide_type == "Missionary":
		return frappe.db.get_value(
			"Missionary",
			{"name": name, "publish": 1, "sensitive": 0},
			["title", "agency", "country", "photo", "website"],
			as_dict=True,
		)
	return None


def record_slide(row):
	"""A record slide's title and content: the whole record to a reader of it, else only its public fields."""
	record = None
	if frappe.db.exists(row.slide_type, row.slide):
		record = frappe.get_doc(row.slide_type, row.slide)
		if frappe.has_permission(row.slide_type, "read", doc=record):
			record.apply_fieldlevel_read_permissions()
		else:
			record = get_public_slide(row.slide_type, row.slide)
	if not record:
		return {"title": "", "content": f"<p>{_('This slide is not public')}</p>"}

	field_configs = _parse_display_fields(row.display_fields)
	if not field_configs:
		return {"title": record.get_title() if isinstance(record, Document) else record.title, "content": ""}
	title, content = _build_content_from_selected_fields(
		record, frappe.get_meta(row.slide_type), field_configs
	)
	return {"title": title or row.slide, "content": content}


def get_context(context):
	name = frappe.form_dict.get("name")
	if not name:
		frappe.throw(_("Please specify a sermon name"), frappe.exceptions.ValidationError)

	sermon = frappe.get_doc("Sermon", name)
	if not sermon.publish and not frappe.has_permission("Sermon", doc=sermon):
		frappe.throw(_("This sermon is not available."), frappe.PermissionError)

	slides = []
	for row in sermon.get("slides", []):
		# get(): the published-field scan reads an attribute named notes as Missionary notes.
		slide_data = {"idx": row.idx, "slide_type": row.slide_type, "notes": row.get("notes") or ""}
		if row.scripture:
			slides.append({**slide_data, **scripture_slide(row.scripture, row.translation)})
		else:
			slides.append({**slide_data, **record_slide(row)})

	prepared_by_name = ""
	if sermon.prepared_by:
		prepared_by_name = frappe.get_doc("Person", sermon.prepared_by).get_title()

	context.sermon_title = sermon.title or sermon.name
	context.prepared_by = prepared_by_name
	context.slides = slides
	context.no_header = 1
	context.no_sidebar = 1
	context.no_breadcrumbs = 1
