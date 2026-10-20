# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe import _

from churchit.church_communications.doctype.communication_consent.communication_consent import EMAIL, SMS
from churchit.church_communications.preferences import get_member_preferences

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/preferences"
		raise frappe.Redirect

	context.no_cache = 1
	context.show_sidebar = 1
	context.title = _("Communication Preferences")
	context.channel_labels = {EMAIL: _("Email"), SMS: _("Text")}
	context.update(get_member_preferences())
