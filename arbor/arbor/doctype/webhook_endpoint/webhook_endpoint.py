# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Webhook Endpoint controller (DATA-MODEL §10).

An external system subscription target. Independent of any Frappe User (locked
decision 4). The webhook dispatcher matches Tree Events against active endpoints,
signs with the ``secret`` (HMAC-SHA256 via ``arbor.core.security``) and logs each
attempt as a Webhook Delivery. This controller only keeps the Dynamic Link
``target_doctype`` consistent with ``scope``.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

from arbor.core.types import EVENT_TYPES

_SCOPE_TO_DOCTYPE = {
	"sheet": "Tree Sheet",
	"branch": "Tree Node",
	"column": "Tree Column",
}


class WebhookEndpoint(Document):
	def validate(self) -> None:
		self.target_doctype = _SCOPE_TO_DOCTYPE.get(self.scope, "Tree Sheet")
		self._validate_event_types()

	def _validate_event_types(self) -> None:
		"""Reject subscriptions to event types outside the closed set (WEBHOOKS-044)
		so a bogus type can't create a silently-never-matching endpoint."""
		raw = self.event_types
		types = frappe.parse_json(raw) if isinstance(raw, str) else (raw or [])
		bad = [t for t in types if t not in EVENT_TYPES]
		if bad:
			frappe.throw(
				f"Unknown Tree Event type(s) {bad}; the closed set is {', '.join(EVENT_TYPES)}."
			)
