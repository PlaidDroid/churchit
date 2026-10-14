# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Give existing sites the Ministry Team portal page and the Ministry Team email templates.

Both are seeded at install only. A menu or template the site already has is left as it is.
"""

import json
from pathlib import Path

import frappe

ROUTE = "ministry-team"
TEMPLATE_FOLDERS = (
	"ministry_team_request_template",
	"ministry_team_reminder_template",
	"ministry_team_decline_template",
)


def execute():
	folder = Path(frappe.get_app_path("churchit", "church_communications", "email_template"))
	for name in TEMPLATE_FOLDERS:
		values = json.loads((folder / name / f"{name}.json").read_text())
		if not frappe.db.exists("Email Template", values["name"]):
			frappe.get_doc(values).insert(ignore_permissions=True)

	settings = frappe.get_doc("Portal Settings")
	if any(row.route == ROUTE for row in settings.menu):
		return
	settings.add_item(
		{
			"title": "Ministry Team",
			"route": ROUTE,
			"reference_doctype": "Ministry Team Assignment",
			"role": "Church User",
		}
	)
	settings.save(ignore_permissions=True)
