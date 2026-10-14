# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe

from churchit.church_ministries.report.song_usage.song_usage import execute
from churchit.tests.helpers import RollbackEachTest, make_function, make_two_churches


def make_song(title, ccli=None):
	return (
		frappe.get_doc({"doctype": "Song", "title": title, "ccli": ccli}).insert(ignore_permissions=True).name
	)


def sung(*songs):
	return [{"item_type": "Song", "item": song} for song in songs]


class TestSongUsage(RollbackEachTest):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.hymn = make_song("_Test Usage Hymn", "1234567")
		cls.chorus = make_song("_Test Usage Chorus")
		make_function(
			"_Test Usage Sunday", start_date="2031-03-02", schedule=sung(cls.hymn, cls.chorus, cls.hymn)
		)
		make_function("_Test Usage Next Sunday", start_date="2031-03-09", schedule=sung(cls.hymn))
		make_function("_Test Usage Later", start_date="2031-04-06", schedule=sung(cls.chorus))

	def rows(self, **filters):
		_columns, data = execute({"from_date": "2031-03-01", "to_date": "2031-03-31", **filters})
		return [
			(row.song, row.ccli, row.times_used, str(row.last_used))
			for row in data
			if row.song in (self.hymn, self.chorus)
		]

	def test_counts_each_use_of_a_song_in_the_range_most_used_first(self):
		self.assertEqual(
			self.rows(),
			[(self.hymn, "1234567", 3, "2031-03-09"), (self.chorus, None, 1, "2031-03-02")],
		)

	def test_a_church_sees_only_the_songs_sung_at_its_own_functions(self):
		_root, branch, _other = make_two_churches()
		make_function(
			"_Test Usage Branch Sunday", start_date="2031-03-16", church=branch, schedule=sung(self.chorus)
		)

		self.assertEqual(self.rows(church=branch), [(self.chorus, None, 1, "2031-03-16")])
