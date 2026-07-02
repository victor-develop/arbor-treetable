# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Arbor Cell Comment controller (Feature: per-cell comments drawer).

A threaded, per-cell comment keyed by the ``(sheet, node, column)`` cell. Comments
are a NON-capability collaboration feature: nothing here routes through
``arbor.core`` and no Tree Event is emitted (the closed 11-EventType set stays
intact). All real authority (who may read / post / resolve / delete) is enforced
by the whitelisted shims in ``arbor.arbor.api`` on top of ``arbor.core.acl`` —
never by Frappe row perms.

Threading is self-referential:

* ``thread_root`` is NULL on the first/root comment of a cell-thread and is set
  to the root's ``name`` on every reply, so a single cell can host multiple
  independent threads.
* ``parent_comment`` is the direct reply target for nested rendering (NULL on a
  root comment).

The controller enforces two invariants defensively (the endpoint normally sets
these correctly before insert):

1. ``body`` must be non-empty after strip.
2. A reply's ``thread_root`` is derived from its ``parent_comment`` (the parent's
   root, or the parent itself if the parent is a root); a comment with no parent
   is a root and carries ``thread_root`` NULL.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class ArborCellComment(Document):
    def validate(self) -> None:
        self._require_body()
        self._derive_thread_root()

    def _require_body(self) -> None:
        if not (self.body or "").strip():
            raise frappe.ValidationError("Comment body must not be empty.")

    def _derive_thread_root(self) -> None:
        if not self.parent_comment:
            # A root comment: no thread_root, no parent.
            self.thread_root = None
            return
        parent = frappe.get_doc("Arbor Cell Comment", self.parent_comment)
        # The parent's root, or the parent itself if the parent is a root.
        self.thread_root = parent.thread_root or parent.name
