# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

"""The Espresso colour tokens churchit's stylesheets read still exist in frappe.

A token frappe drops or renames makes every rule that reads it compute to
nothing, so a background turns transparent with no error anywhere.
"""

import os
import re

import frappe
from frappe.tests.utils import FrappeTestCase

ESPRESSO_TOKEN = r"--(?:surface|ink|outline)-[a-z0-9-]+"


def tokens_read_by_churchit() -> dict[str, list[str]]:
	public_dir = frappe.get_app_path("churchit", "public")
	readers = {}
	for root, _dirs, files in os.walk(public_dir):
		for name in files:
			if not name.endswith((".css", ".scss")):
				continue
			path = os.path.join(root, name)
			for token in re.findall(rf"var\(({ESPRESSO_TOKEN})", open(path, encoding="utf-8").read()):
				readers.setdefault(token, []).append(os.path.relpath(path, public_dir))
	return readers


class TestThemeTokens(FrappeTestCase):
	def test_every_espresso_token_churchit_reads_is_defined_by_frappe(self):
		colors = open(frappe.get_app_path("frappe", "public", "css", "espresso", "colors.css")).read()
		defined = set(re.findall(rf"({ESPRESSO_TOKEN})\s*:", colors))

		missing = {token: paths for token, paths in tokens_read_by_churchit().items() if token not in defined}
		self.assertEqual(missing, {}, "frappe no longer defines these tokens; use its current name for each.")
