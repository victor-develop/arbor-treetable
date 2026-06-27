# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Arbor Cell Draft controller (Feature: personal cell draft box).

A personal, server-persisted staging row for a single cell edit, BEFORE it ever
becomes a Change Request. Drafts are private to their ``user`` (a user only ever
sees / edits their OWN drafts), and there is at most ONE draft per
(user, sheet, node, column) — the ``save_cell_draft`` endpoint upserts on those
four keys.

Drafts are pure UI staging, NOT a governed capability: nothing here routes
through ``arbor.core``. Only the eventual ``submit_cell_drafts`` turns the staged
edits into ONE multi-change Change Request via the executor's ``suggestChanges``.

The controller enforces the (user, sheet, node, column) uniqueness defensively
(raising ``frappe.DuplicateEntryError`` on a second draft for the same cell); the
endpoint's upsert means normal use never hits it.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class ArborCellDraft(Document):
    def validate(self) -> None:
        self._enforce_single_per_cell()

    def _enforce_single_per_cell(self) -> None:
        dupe = frappe.db.exists(
            "Arbor Cell Draft",
            {
                "user": self.user,
                "sheet": self.sheet,
                "node": self.node,
                "column": self.column,
                "name": ["!=", self.name or ""],
            },
        )
        if dupe:
            raise frappe.DuplicateEntryError(
                f"{self.user} already has a draft for cell "
                f"({self.sheet}, {self.node}, {self.column})."
            )
