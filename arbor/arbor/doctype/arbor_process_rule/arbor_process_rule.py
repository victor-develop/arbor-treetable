# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Arbor Process Rule child table controller.

One trigger->expectation rule of an Arbor Process (the DAG replacement for the
old ordered stage list). A rule reads "On <trigger>: expect (colA [and colB...])
filled within <within_seconds>", where trigger is a row create/update or a
specific ``trigger_column`` create/update. Rules compose into a DAG because a
column EXPECTED by one rule can be the ``trigger_column`` of another.

Pure data; the DAG evaluator + cycle/reachability validation live in
``arbor.core.process`` / ``arbor.core.process_graph``. This controller stays thin.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class ArborProcessRule(Document):
	def validate(self) -> None:
		"""Thin structural guard on ONE rule row (the pure DAG authority —
		cycle/duplicate/reachability — is ``arbor.core.process_graph``). Enforces:
		a column trigger has a non-empty trigger set; trigger_join in {any,all};
		trigger columns belong to the sheet; and no self-trigger (a trigger column
		may not also be one of this rule's expected columns)."""
		if self.trigger_kind != "column":
			return
		triggers = self._trigger_set()
		if not triggers:
			frappe.throw("A column-trigger rule needs at least one trigger column.")
		join = self.trigger_join or "any"
		if join not in ("any", "all"):
			frappe.throw(f"trigger_join must be 'any' or 'all', got {join!r}.")
		expected = set(self._json_list(self.get("expected_columns")))
		# no self-trigger: a trigger column that is also an expected column.
		overlap = [c for c in triggers if c in expected]
		if overlap:
			frappe.throw(f"A column cannot both trigger and be expected by the same rule: {overlap!r}.")
		# trigger columns must belong to the sheet (best-effort; skip when no sheet
		# context, e.g. a partial in-memory row under construction).
		sheet = self._parent_sheet()
		if sheet:
			for col in triggers:
				if not frappe.db.exists("Tree Column", {"name": col, "sheet": sheet}):
					frappe.throw(f"Trigger column {col!r} is not a column of sheet {sheet!r}.")

	# --- helpers ---
	def _trigger_set(self) -> list[str]:
		cols = self._json_list(self.get("trigger_columns"))
		if not cols and self.get("trigger_column"):
			cols = [self.trigger_column]
		return [c for c in cols if c]

	@staticmethod
	def _json_list(raw) -> list[str]:
		if isinstance(raw, str):
			raw = frappe.parse_json(raw) if raw else []
		if isinstance(raw, (list, tuple)):
			return [str(c) for c in raw]
		return []

	def _parent_sheet(self):
		parent = getattr(self, "_parent_doc", None) or (
			frappe.get_cached_doc(self.parenttype, self.parent)
			if self.get("parenttype") and self.get("parent")
			else None
		)
		return getattr(parent, "sheet", None) if parent else None
