# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Arbor Sheet Saved View controller (Feature: saved views).

A NAMED, server-persisted ``SheetView`` overlay (hidden / order / width /
collapsed) so an arrangement survives a reload, a new browser, and a new
machine. Ownership is HYBRID: a save creates a ``private`` row (only its
``author`` sees it) and publishing to the sheet (``visibility='sheet'``) is an
explicit second step that makes it pickable by everyone who can read the sheet.

Saved views are PRESENTATION state, NOT a governed capability: nothing here
routes through ``arbor.core`` — no Tree Event, no Change Request. The
authorization (author / admin / sheet structural owner) and the payload
validation live in the endpoints (``arbor.arbor.api``) and in the shared pure
``arbor.arbor.saved_view`` rules, so both adapters behave identically.

The controller enforces the (author, sheet, label) uniqueness defensively — the
picker must never show one user two identically-named views of one sheet — and
re-validates the payload so a row written outside the endpoint (a fixture, the
desk UI) cannot store a shape the frontend would refuse to read.
"""

from __future__ import annotations

import frappe
from arbor.arbor.saved_view import SavedViewError, validate_payload
from frappe.model.document import Document


class ArborSheetSavedView(Document):
    def validate(self) -> None:
        self._enforce_single_label_per_author()
        self._validate_payload()

    def _enforce_single_label_per_author(self) -> None:
        dupe = frappe.db.exists(
            "Arbor Sheet Saved View",
            {
                "author": self.author,
                "sheet": self.sheet,
                "label": self.label,
                "name": ["!=", self.name or ""],
            },
        )
        if dupe:
            raise frappe.DuplicateEntryError(
                f"{self.author} already has a view named {self.label} on {self.sheet}."
            )

    def _validate_payload(self) -> None:
        try:
            self.payload = frappe.as_json(validate_payload(self.payload or {"v": 1, "hidden": [], "order": []}))
        except SavedViewError as exc:
            frappe.throw(str(exc), frappe.ValidationError)
