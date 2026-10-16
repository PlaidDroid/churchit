// This source code is freely given for the sake of the gospel (Matthew 10:8)
// and is licensed under MIT No Attribution (MIT-0).

frappe.ui.form.on("Church Email", {
	refresh(frm) {
		if (frm.is_new()) return;

		const can_send = frappe.model.can_write("Church Email");
		if (frm.doc.status === "Draft" && can_send) {
			frm.add_custom_button(__("Send Test Email"), () => send_test(frm));
			frm.add_custom_button(__("Send"), () => send(frm)).addClass("btn-primary");
		} else if (frm.doc.status === "Scheduled") {
			frm.set_intro(
				__("This email will be sent at {0}.", [
					frappe.datetime.str_to_user(frm.doc.send_after),
				]),
				"orange"
			);
			if (can_send) {
				frm.add_custom_button(__("Unschedule"), () =>
					frm.call("unschedule").then(() => frm.reload_doc())
				);
			}
		} else if (frm.doc.status === "Sending") {
			frm.set_intro(
				__("This email is being sent. Reload the page to see when it is done."),
				"blue"
			);
		} else if (frm.doc.status === "Sent") {
			frm.set_intro(
				__("This email has been sent, so it can no longer be changed."),
				"green"
			);
			frm.disable_form();
		}
	},
});

function saved(frm) {
	return frm.is_dirty() ? frm.save() : Promise.resolve();
}

function send_test(frm) {
	saved(frm)
		.then(() => frm.call("send_test"))
		.then(() =>
			frappe.show_alert({
				message: __("Test email sent to {0}", [frappe.session.user_email]),
				indicator: "green",
			})
		);
}

function send(frm) {
	saved(frm)
		.then(() => frm.call("get_recipient_count"))
		.then(({ message: count }) => {
			if (!count) {
				frappe.msgprint(__("No one would receive this email. Check who it is sent to."));
				return;
			}
			const later =
				frm.doc.send_after && frappe.datetime.str_to_obj(frm.doc.send_after) > new Date();
			const question = later
				? __("Send this email to {0} people at {1}?", [
						count,
						frappe.datetime.str_to_user(frm.doc.send_after),
				  ])
				: __("Send this email to {0} people now?", [count]);
			frappe.confirm(question, () => frm.call("send").then(() => frm.reload_doc()));
		});
}
