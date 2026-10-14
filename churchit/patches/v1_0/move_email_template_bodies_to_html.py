# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Give the four starter Email Templates the body their emails have been sent without.

They shipped with HTML ticked but their text in the plain body, and an HTML template sends
its HTML body, so every email from them went out empty. A template whose HTML body a church
already filled in keeps it.
"""

import frappe

TEMPLATES = ("Birthday Greeting", "Donation Acknowledgment", "New Member Welcome", "Visitor Follow-Up")


def execute():
	for template in frappe.get_all(
		"Email Template",
		filters={"name": ("in", TEMPLATES), "use_html": 1},
		fields=["name", "response", "response_html"],
	):
		if template.response and not template.response_html:
			frappe.db.set_value(
				"Email Template", template.name, "response_html", template.response, update_modified=False
			)
