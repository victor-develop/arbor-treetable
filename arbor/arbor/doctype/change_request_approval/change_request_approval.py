# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Change Request Approval child table controller.

One row per user who has approved the parent Change Request. Powers moveNode's
single-CR multi-approval (resolved_approver AND every co_approver must approve
before the CR transitions to approved and the handler replays). The core
(``arbor.core.change_request``) tracks the approval set on the CR dict; the
adapter mirrors it into this child table. Pure data; no logic.
"""

from __future__ import annotations

from frappe.model.document import Document


class ChangeRequestApproval(Document):
	pass
