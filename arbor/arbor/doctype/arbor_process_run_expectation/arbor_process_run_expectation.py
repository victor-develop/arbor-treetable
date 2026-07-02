# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Arbor Process Run Expectation child table controller.

The per-row, per-(rule, expected-column) ledger under an Arbor Process Run: when
the triggering rule opened the expectation (opened_at, the SLA clock start), when
the expected column was filled (satisfied_at), the deadline (due_at =
opened_at + within_seconds), the breach flags, and the notified_owner idempotency
guard. This is what the flow dashboard aggregates per edge. Pure data; the DAG
evaluator + SLA math live in ``arbor.core.process``.
"""

from __future__ import annotations

from frappe.model.document import Document


class ArborProcessRunExpectation(Document):
	pass
