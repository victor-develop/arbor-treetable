# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Tree Event controller (DATA-MODEL §12).

The append-only, event-sourced log. Every governed mutation emits exactly one
Tree Event, written SOLELY by the adapter's FrappeEventSink (``emit``), which
assigns name/timestamp. Notifications and webhooks are derived consumers — never
writers. The closed set of 11 event types mirrors ``arbor.core.types.EVENT_TYPES``.

Append-only is enforced two ways: the DocType permissions grant no write/delete,
and the controller blocks updates/deletes defensively. ``internalReset`` (not
exposed to the LLM) is the only administrative purge path and bypasses this via
``flags.ignore_arbor_append_only``.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

from arbor.core.types import EVENT_TYPES


class TreeEvent(Document):
	def validate(self) -> None:
		if self.type not in EVENT_TYPES:
			frappe.throw(
				f"{self.type!r} is not a valid Tree Event type; "
				f"the closed set is {', '.join(EVENT_TYPES)}."
			)

	def on_update(self) -> None:
		"""Block mutation of an already-persisted event (append-only).

		``on_update`` runs after the initial insert too (Frappe's post-save
		hooks), where ``is_new()`` is already False — so the initial append is
		distinguished by ``flags.in_insert`` and allowed; any later update throws.
		"""
		if self.flags.get("ignore_arbor_append_only"):
			return
		if self.flags.in_insert:
			return
		frappe.throw("Tree Event is append-only and cannot be modified.")

	def on_trash(self) -> None:
		if self.flags.get("ignore_arbor_append_only"):
			return
		frappe.throw(
			"Tree Event is append-only and cannot be deleted (use internalReset for admin purge)."
		)
