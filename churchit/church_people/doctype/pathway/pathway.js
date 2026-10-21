// This source code is freely given for the sake of the gospel (Matthew 10:8)
// and is licensed under MIT No Attribution (MIT-0).

frappe.ui.form.on("Pathway", {
	setup(frm) {
		frm.set_query("smart_list", "steps", () => ({
			filters: { report_type: "Report Builder", ref_doctype: "Person" },
		}));
	},
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Open Progress"), () =>
			frappe.set_route("query-report", "Pathway Progress", { pathway: frm.doc.name })
		);
	},
});
