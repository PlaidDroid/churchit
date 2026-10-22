# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

import frappe
from frappe import _
from frappe.model.document import Document

from churchit.church_scope import CHURCH_FIELD, has_church_access, refuse_another_churches_record

# Roles that may clear a booking on a room whose "Requires Approval" flag is set.
APPROVER_ROLES = {"Church Manager", "System Manager"}


class RoomBooking(Document):
	def validate(self):
		self.refuse_another_churches_room()
		self.refuse_another_churches_requester()
		if self.end_datetime and self.start_datetime and self.end_datetime <= self.start_datetime:
			frappe.throw(_("End time must be after start time."))
		# No room: the mandatory-field check reports it.
		room = frappe.db.get_value(
			"Room", self.room, ["is_bookable", "requires_approval"], as_dict=True
		) or frappe._dict(is_bookable=1)
		self.refuse_a_room_that_is_not_bookable(room.is_bookable)
		self.set_initial_status(room.requires_approval)
		self.reset_approval(room.requires_approval)
		self.validate_approver(room.requires_approval)
		self.check_conflicts()

	def is_moved(self):
		return any(self.has_value_changed(field) for field in ("room", "start_datetime", "end_datetime"))

	def refuse_another_churches_room(self):
		"""Only a room's own church books it, and every church books a shared room.

		Frappe checks the create right before `church` is fetched from the room, so it never sees
		whose room this is. Writes with ignore_permissions are the app's own and are not refused.
		"""
		if self.flags.ignore_permissions or not self.has_value_changed("room"):
			return
		stored = self.get_doc_before_save() or frappe._dict()
		for room, church in (
			(self.room, frappe.db.get_value("Room", self.room, CHURCH_FIELD)),
			(stored.room, stored.church),
		):
			if not has_church_access(frappe.session.user, church):
				frappe.throw(
					_("{0} belongs to another church, so only that church can book it.").format(room),
					frappe.PermissionError,
				)

	def refuse_another_churches_requester(self):
		if self.flags.ignore_permissions or not self.has_value_changed("requester"):
			return
		refuse_another_churches_record(self, "Person", self.requester)

	def refuse_a_room_that_is_not_bookable(self, is_bookable):
		if not is_bookable and self.is_moved():
			frappe.throw(_("{0} is not bookable.").format(self.room))

	def set_initial_status(self, requires_approval):
		"""Approve bookings outright on rooms that need no manager's OK.

		This keeps "Requested" meaning exactly one thing: waiting on a manager.
		A booking deliberately created as Denied or Cancelled is left alone.
		"""
		if requires_approval or not self.is_new():
			return
		if self.status in (None, "", "Requested"):
			self.status = "Approved"

	def reset_approval(self, requires_approval):
		"""Send a moved booking back for approval, unless a manager moved it."""
		if not requires_approval or self.status != "Approved" or self.is_new():
			return
		if self.get_doc_before_save().status != "Approved" or not self.is_moved():
			return
		if not APPROVER_ROLES & set(frappe.get_roles()):
			self.status = "Requested"

	def validate_approver(self, requires_approval):
		"""Let only a manager move a booking to Approved on an approval-gated room.

		Church Users hold write access to their own bookings, so without this a
		requester could approve the very booking they submitted.
		"""
		if not requires_approval or self.status != "Approved":
			return
		before = self.get_doc_before_save()
		if before and before.status == "Approved":
			return
		if not APPROVER_ROLES & set(frappe.get_roles()):
			frappe.throw(_("Only a Church Manager can approve a booking for this room."))

	def check_conflicts(self):
		if self.status in ("Denied", "Cancelled"):
			return
		# church-scope: a shared room is booked by several churches, so a clash has to be
		# found across all of them, not only the booking church's own diary
		Booking = frappe.qb.DocType("Room Booking")
		conflicts = (
			frappe.qb.from_(Booking)
			.select(Booking.name, Booking.church)
			.where(
				(Booking.room == self.room)
				& (Booking.name != (self.name or ""))
				& Booking.status.isin(["Requested", "Approved"])
				& (Booking.start_datetime < self.end_datetime)
				& (Booking.end_datetime > self.start_datetime)
			)
			.run()
		)
		if not conflicts:
			return
		clashes = [name for name, church in conflicts if has_church_access(frappe.session.user, church)]
		hidden = len(conflicts) - len(clashes)
		if hidden:
			clashes.append(_("{0} booking(s) of another church").format(hidden))
		frappe.throw(
			_("Room is already booked at that time (conflicts with: {0}).").format(", ".join(clashes))
		)
