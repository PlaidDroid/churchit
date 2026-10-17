# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from churchit.church_people.derived import refresh_daily
from churchit.church_people.doctype.people_settings.people_settings import store_defaults


def save_adult_age(age):
	settings = frappe.get_single("People Settings")
	settings.adult_age = age
	settings.save(ignore_permissions=True)


class TestPeopleSettings(FrappeTestCase):
	def test_a_site_that_never_saved_the_settings_gets_the_default_adult_age(self):
		"""A Single nobody saved stores nothing, so a reader got 0 and everyone with an age was an Adult."""
		frappe.db.delete("Singles", {"doctype": "People Settings"})

		store_defaults()

		self.assertEqual(frappe.db.get_single_value("People Settings", "adult_age"), 18)

	def test_storing_the_defaults_keeps_a_saved_adult_age(self):
		save_adult_age(21)

		store_defaults()

		self.assertEqual(frappe.db.get_single_value("People Settings", "adult_age"), 21)

	def test_changing_the_adult_age_rechecks_every_life_stage(self):
		save_adult_age(18)
		settings = frappe.get_single("People Settings")

		with patch("frappe.enqueue") as enqueue:
			settings.save(ignore_permissions=True)
			enqueue.assert_not_called()

			settings.adult_age = 16
			settings.save(ignore_permissions=True)

		enqueue.assert_called_once_with(refresh_daily, enqueue_after_commit=True)
