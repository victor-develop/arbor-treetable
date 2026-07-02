# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Arbor Process controller (process DAG dataModel).

A per-sheet SET of trigger->expectation rules forming a DAG. Each rule reads
"On <trigger>: expect (colA [and colB...]) filled within <within_seconds>". When
enabled, each in-scope node gets an Arbor Process Run whose per-(rule,
expected-column) Expectation ledger tracks fills + SLA. The DAG evaluator +
cycle/reachability validation live in ``arbor.core.process`` /
``arbor.core.process_graph`` and the registry capabilities (defineProcess/
enableProcess/disableProcess); this controller stays thin: it enforces the
invariants the schema alone cannot express.

Invariants checked here (defensive; core is the authority):
- at most ONE enabled process per sheet;
- a non-empty rule set with unique rule keys when the process is enabled.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class ArborProcess(Document):
	def validate(self) -> None:
		if self.enabled:
			self._assert_single_enabled_per_sheet()
			self._assert_rules_coherent()

	def _assert_single_enabled_per_sheet(self) -> None:
		others = frappe.get_all(
			"Arbor Process",
			filters={"sheet": self.sheet, "enabled": 1, "name": ["!=", self.name or ""]},
			pluck="name",
		)
		if others:
			frappe.throw(
				f"Sheet {self.sheet!r} already has an enabled process "
				f"({others[0]}); exactly one enabled process is allowed per sheet."
			)

	def _assert_rules_coherent(self) -> None:
		rules = self.get("rules") or []
		if not rules:
			frappe.throw("An enabled process must declare at least one rule.")
		keys = [r.rule_key for r in rules]
		if len(set(keys)) != len(keys):
			frappe.throw("A process may not repeat a rule key.")
