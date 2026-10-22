# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe.exceptions import ValidationError
from frappe.tests.utils import FrappeTestCase

from churchit.church_foundations.church_access import set_user_church
from churchit.tests.helpers import RollbackEachTest, ensure_user, make_person, make_two_churches


def _ensure(doctype, filters, values):
	name = frappe.db.exists(doctype, filters)
	if name:
		return name
	return frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True).name


class TestRoomBooking(FrappeTestCase):
	def setUp(self):
		self.gated_room = _ensure(
			"Room",
			{"room_name": "_Test Gated Room"},
			{"room_name": "_Test Gated Room", "is_bookable": 1, "requires_approval": 1},
		)
		self.open_room = _ensure(
			"Room",
			{"room_name": "_Test Open Room"},
			{"room_name": "_Test Open Room", "is_bookable": 1, "requires_approval": 0},
		)
		self.requester = _ensure("Person", {"first_name": "_Test Booker"}, {"first_name": "_Test Booker"})

	def tearDown(self):
		# Bookings outlive the per-test rollback, so clear them here or the conflict
		# check trips on slots left behind by earlier tests in the same run.
		frappe.set_user("Administrator")
		for name in frappe.get_all(
			"Room Booking", filters={"room": ["in", [self.gated_room, self.open_room]]}, pluck="name"
		):
			frappe.delete_doc("Room Booking", name, force=True, ignore_permissions=True)

	def _make_booking(self, room, hour=9, **values):
		return frappe.get_doc(
			{
				"doctype": "Room Booking",
				"room": room,
				"requester": self.requester,
				"purpose": "Test booking",
				"start_datetime": f"2031-03-04 {hour:02d}:00:00",
				"end_datetime": f"2031-03-04 {hour + 1:02d}:00:00",
				**values,
			}
		).insert(ignore_permissions=True)

	def _church_user(self):
		email = "_test_church_user@example.com"
		if not frappe.db.exists("User", email):
			user = frappe.new_doc("User")
			user.update({"email": email, "first_name": "_Test Church User"})
			user.flags.no_welcome_mail = True
			user.append("roles", {"role": "Church User"})
			user.insert(ignore_permissions=True)
		return email

	def test_booking_on_open_room_is_approved_immediately(self):
		self.assertEqual(self._make_booking(self.open_room).status, "Approved")

	def test_booking_on_gated_room_waits_for_approval(self):
		self.assertEqual(self._make_booking(self.gated_room).status, "Requested")

	def test_explicit_status_survives_auto_approval(self):
		booking = self._make_booking(self.open_room, status="Cancelled")
		self.assertEqual(booking.status, "Cancelled")

	def test_requester_cannot_approve_own_gated_booking(self):
		frappe.set_user(self._church_user())
		booking = self._make_booking(self.gated_room)
		self.assertEqual(booking.status, "Requested")
		booking.status = "Approved"
		with self.assertRaises(ValidationError):
			booking.save()

	def test_manager_can_approve_gated_booking(self):
		booking = self._make_booking(self.gated_room)
		booking.status = "Approved"
		booking.save()
		self.assertEqual(booking.status, "Approved")

	def test_approved_booking_can_still_be_edited(self):
		booking = self._make_booking(self.gated_room)
		booking.status = "Approved"
		booking.save()
		booking.notes = "Edited after approval"
		booking.save()  # should not raise
		self.assertEqual(booking.status, "Approved")

	def test_overlapping_booking_is_rejected(self):
		self._make_booking(self.open_room, hour=9)
		with self.assertRaises(ValidationError):
			self._make_booking(self.open_room, hour=9)

	def test_end_before_start_is_rejected(self):
		with self.assertRaises(ValidationError):
			self._make_booking(
				self.open_room, start_datetime="2031-03-04 12:00:00", end_datetime="2031-03-04 11:00:00"
			)


class TestRoomBookingAcrossChurches(RollbackEachTest):
	"""Only a room's own church books it, and every church books a shared room."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_root, cls.branch_a, cls.branch_b = make_two_churches()
		cls.addClassCleanup(frappe.clear_cache)
		cls.staff_b = ensure_user("_test_booking_staff_b@example.com", "Staff B", roles=("Church Staff",))
		set_user_church(cls.staff_b, cls.branch_b)
		cls.requester_b = make_person("_Test Booking", "Requester B", church=cls.branch_b).name
		cls.room_a, cls.shared_room = (
			frappe.get_doc(
				{"doctype": "Room", "room_name": name, "is_bookable": 1, "church": cls.branch_a, **values}
			)
			.insert(ignore_permissions=True)
			.name
			for name, values in (("_Test Branch A Hall", {}), ("_Test Shared Hall B", {"is_shared": 1}))
		)

	def book(self, room):
		return frappe.get_doc(
			{
				"doctype": "Room Booking",
				"room": room,
				"requester": self.requester_b,
				"purpose": "_Test across churches",
				"start_datetime": "2034-02-02 09:00:00",
				"end_datetime": "2034-02-02 10:00:00",
				"church": self.branch_b,
			}
		)

	def test_another_church_cannot_book_a_churchs_room(self):
		"""Frappe checks the create right on the church the caller sends, before it fetches the room's."""
		frappe.set_user(self.staff_b)
		with self.assertRaises(frappe.PermissionError):
			self.book(self.room_a).insert()

	def test_every_church_books_a_shared_room(self):
		frappe.set_user(self.staff_b)
		self.assertEqual(self.book(self.shared_room).insert().church, "")
