# This source code is freely given for the sake of the gospel (Matthew 10:8)
# and is licensed under MIT No Attribution (MIT-0).

from frappe.model.document import Document
from frappe.query_builder.functions import Coalesce

ACTIVE = "Active"
INACTIVE = "Inactive"
DEFAULT_STATUSES = {
	ACTIVE: "Sent or supported by the church now.",
	"Pending": "Not sent or supported yet, such as a missionary the church is considering.",
	INACTIVE: "No longer sent or supported by the church.",
}


def is_current_missionary(Missionary):
	"""Query criterion for a missionary the church still lists: any status but Inactive."""
	return Coalesce(Missionary.status, "") != INACTIVE


class MissionaryStatus(Document):
	pass
