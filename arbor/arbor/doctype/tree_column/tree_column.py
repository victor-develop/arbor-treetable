# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Tree Column controller (DATA-MODEL §2).

One row per column; the meta-model schema of a sheet. ``column_owner`` + the
``editors`` child table are the ENTIRE Axis-2 authority (no separate grant
DocType). Mutation routing/ACL is owned by ``arbor.core`` + the adapter; this
controller only enforces the two locked integrity constraints:

  - ``(sheet, field)`` unique
  - exactly one ``is_label=1`` per sheet
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class TreeColumn(Document):
	def validate(self) -> None:
		self._validate_unique_field()
		self._validate_single_label()

	def _validate_unique_field(self) -> None:
		"""Enforce (sheet, field) uniqueness (DATA-MODEL §13)."""
		clash = frappe.db.exists(
			"Tree Column",
			{"sheet": self.sheet, "field": self.field, "name": ("!=", self.name)},
		)
		if clash:
			frappe.throw(
				f"Column field {self.field!r} already exists on sheet {self.sheet}",
				frappe.DuplicateEntryError,
			)

	def _validate_single_label(self) -> None:
		"""Enforce exactly-one is_label per sheet (DATA-MODEL §13)."""
		if not self.is_label:
			return
		other = frappe.db.exists(
			"Tree Column",
			{"sheet": self.sheet, "is_label": 1, "name": ("!=", self.name)},
		)
		if other:
			frappe.throw(
				f"Sheet {self.sheet} already has a label column ({other}); "
				"exactly one is_label column is allowed per sheet."
			)
