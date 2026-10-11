# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""Add Fiancé and Fiancée, each the other's reverse, to the relation types of existing sites.

A type the church already made under the same name is left alone.
"""

import frappe

RELATION_TYPES = ("Fiancé", "Fiancée")


def execute():
	for relation_type in RELATION_TYPES:
		if not frappe.db.exists("Person Relation Type", relation_type):
			frappe.get_doc({"doctype": "Person Relation Type", "type": relation_type}).insert(
				ignore_permissions=True
			)
