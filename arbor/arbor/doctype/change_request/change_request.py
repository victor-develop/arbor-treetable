# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Change Request controller (DATA-MODEL §6).

A deferred capability call: stores ``{target_kind, operation, payload, requester,
resolved_approver}`` and, on approval, REPLAYS the capability handler as the
approver, linking ``resulting_event``. moveNode uses a SINGLE CR with
``payload.co_approvers`` and an ``approvals`` child table; it transitions to
approved only once ``resolved_approver`` AND every co_approver has approved.

The lifecycle state machine and replay logic live in
``arbor.core.change_request`` (create/approve/reject/withdraw), driven through the
adapter. This controller stays thin: it only validates that target_kind/operation
form a coherent pair and that decision fields are consistent with status.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

# Coherent (target_kind, operation) pairs, derived from the capability registry
# (CAPABILITIES.md). Used for a local sanity check only — authority is core's job.
_VALID_PAIRS = {
	("node-structure", "add"),
	("node-structure", "move"),
	("node-structure", "delete"),
	("cell-value", "update"),
	("column-schema", "add"),
	("column-schema", "update"),
	("column-schema", "delete"),
	# Multi-change (batch) CR sentinel: the real changes live in the `changes`
	# child table, each validated as its own capability call.
	("batch", "multi"),
}


class ChangeRequest(Document):
	def validate(self) -> None:
		if (self.target_kind, self.operation) not in _VALID_PAIRS:
			frappe.throw(
				f"({self.target_kind!r}, {self.operation!r}) is not a valid "
				"Change Request target_kind/operation pair."
			)
		if self.status in ("approved", "rejected", "withdrawn") and not self.decided_by:
			# Closed CRs should carry a decider; withdrawn is decided by the requester.
			frappe.msgprint(
				f"Change Request {self.name} is {self.status} without a decided_by.",
				alert=True,
			)
