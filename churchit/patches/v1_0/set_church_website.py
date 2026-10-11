# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Keep the site address on the Church Directory cover and bulletins, now that they print the Church's Website.

Both printed the site's own address. Only a church with the Web Site module on has a
public site to print; branches fall back to the main church's website.
"""

import frappe

from churchit.church_scope import is_feature_enabled, root_church


def execute():
	church = root_church()
	if (
		church
		and is_feature_enabled("enable_website")
		and not frappe.db.get_value("Church", church, "website")
	):
		frappe.db.set_value("Church", church, "website", frappe.utils.get_url(), update_modified=False)
