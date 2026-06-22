# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Tree Node Value controller (DATA-MODEL §4).

A single cell, keyed uniquely on ``(node, column)``. Its own DocType (not a JSON
blob) so every cell has an independent audit trail, version, and field-level
permission. Updated only via the ``updateCell`` capability (Axis-2) through
``arbor.core`` + the adapter's ``set_value`` (which manages the version bump and
emits ``NODE_VALUE_UPDATED``). This controller only enforces the uniqueness
constraint locally.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class TreeNodeValue(Document):
	def validate(self) -> None:
		self._validate_unique_cell()

	def _validate_unique_cell(self) -> None:
		"""Enforce (node, column) uniqueness (DATA-MODEL §13)."""
		clash = frappe.db.exists(
			"Tree Node Value",
			{"node": self.node, "column": self.column, "name": ("!=", self.name)},
		)
		if clash:
			frappe.throw(
				f"A value already exists for node {self.node} / column {self.column}",
				frappe.DuplicateEntryError,
			)
