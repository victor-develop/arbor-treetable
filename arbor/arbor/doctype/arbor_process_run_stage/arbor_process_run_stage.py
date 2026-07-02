# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Arbor Process Run Stage child table controller.

The per-row, per-stage timestamp ledger under an Arbor Process Run: entered_at,
filled_at, due_at (entered_at + SLA), breached flags, and the notified_owner
idempotency guard. This is what the Kanban dashboard aggregates (row-created->A,
A->B durations, in/out of SLA). Pure data; the SLA math + advance logic live in
``arbor.core.process``.
"""

from __future__ import annotations

from frappe.model.document import Document


class ArborProcessRunStage(Document):
	pass
