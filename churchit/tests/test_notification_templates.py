# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Shipped notifications rendered the way Frappe sends them.

A field name the document does not have renders as blank rather than failing,
so a wrong one goes unnoticed until someone reads the email.
"""

import json
from pathlib import Path

import frappe
from frappe.email.doctype.notification.notification import get_context
from frappe.tests.utils import FrappeTestCase

from churchit.tests.helpers import ensure, make_person


def shipped_templates(module, name):
	"""The notification's JSON, and its Markdown file, which a standard notification sends instead."""
	folder = Path(frappe.get_app_path("churchit", module, "notification", frappe.scrub(name)))
	notification = json.loads((folder / f"{frappe.scrub(name)}.json").read_text())
	markdown = folder / f"{frappe.scrub(name)}.md"
	return notification, markdown.read_text() if markdown.exists() else None


class TestNotificationTemplates(FrappeTestCase):
	def test_expense_pending_approval_names_the_fund_and_notes(self):
		fund = ensure("Fund", {"fund": "_Test Notice Fund"})
		expense = frappe.get_doc(
			{"doctype": "Expense", "amount": 25, "associated_fund": fund, "notes": "_Test hall repairs"}
		)
		notification, markdown = shipped_templates("church_finances", "Expense Pending Approval")

		for template in (notification["message"], markdown):
			rendered = frappe.render_template(template, get_context(expense))
			self.assertIn("_Test Notice Fund", rendered)
			self.assertIn("_Test hall repairs", rendered)

	def test_position_term_ending_names_the_person_and_position(self):
		position = ensure("Position Type", {"position": "_Test Notice Elder"})
		person = make_person("_Test Notice", "Holder")
		person.append(
			"positions", {"position": position, "start_date": "2030-01-01", "end_date": "2031-01-01"}
		)
		person.save(ignore_permissions=True)
		context = get_context(person.positions[0])
		notification, _markdown = shipped_templates("church_communications", "Position Term Ending")

		for template in (notification["subject"], notification["message"]):
			rendered = frappe.render_template(template, context)
			self.assertIn("_Test Notice Elder", rendered)
			self.assertIn("_Test Notice Holder", rendered)

	def test_visitation_follow_up_due_reaches_the_visitor(self):
		"""Visited By holds a Person, which Frappe would drop as a recipient for not being an email."""
		visitor = make_person("_Test Notice", "Visitor")
		visitor.append("emails", {"email_address": "_test_notice_visitor@example.com", "is_primary": 1})
		visitor.save(ignore_permissions=True)
		visit = frappe.get_doc(
			{
				"doctype": "Visitation Log",
				"person": make_person("_Test Notice", "Visited").name,
				"visited_by": visitor.name,
				"status": "Scheduled",
			}
		)
		notification, _markdown = shipped_templates("church_communications", "Visitation Follow-Up Due")

		_recipients, cc, _bcc = frappe.get_doc(notification).get_list_of_recipients(visit, get_context(visit))

		self.assertIn("_test_notice_visitor@example.com", cc)


class TestShippedEmailTemplates(FrappeTestCase):
	def test_every_shipped_email_template_sends_a_body(self):
		"""An HTML template sends its HTML body, so text left in the plain body goes out as nothing."""
		folder = Path(frappe.get_app_path("churchit", "church_communications", "email_template"))
		for source in sorted(folder.glob("*/*.json")):
			template = frappe.get_doc(json.loads(source.read_text()))
			with self.subTest(template.name):
				message = template.get_formatted_email({"first_name": "_Test Reader", "assignments": []})[
					"message"
				]
				self.assertIn("<p>", message)
