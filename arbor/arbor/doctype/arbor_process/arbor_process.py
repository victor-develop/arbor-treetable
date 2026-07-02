# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Arbor Process controller (Area 3 dataModel).

A per-sheet ordered list of column stages (A -> B -> C). When enabled, each
in-scope node gets an Arbor Process Run tracking which stage is active. The
stage machine + governance live in ``arbor.core.process`` and the registry
capabilities (defineProcess/enableProcess/disableProcess); this controller stays
thin: it enforces the invariants the schema alone cannot express.

Invariants checked here (defensive; core is the authority):
- at most ONE enabled process per sheet;
- a non-empty, unique stage column ordering when the process is enabled.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class ArborProcess(Document):
	def validate(self) -> None:
		if self.enabled:
			self._assert_single_enabled_per_sheet()
			self._assert_stage_columns_coherent()

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

	def _assert_stage_columns_coherent(self) -> None:
		stages = self.get("stages") or []
		if not stages:
			frappe.throw("An enabled process must declare at least one stage.")
		columns = [s.column for s in stages]
		if len(set(columns)) != len(columns):
			frappe.throw("A process may not repeat a stage column.")
