# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Webhook Endpoint controller (DATA-MODEL §10).

An external system subscription target. Independent of any Frappe User (locked
decision 4). ONE delivery engine feeds this endpoint from TWO filters:

* ``event_types`` — the closed Tree Event stream (``on_tree_event``); validated
  against the 11-member ``EVENT_TYPES`` so a bogus type can't create a silently-
  never-matching subscription (WEBHOOKS-044).
* ``notification_sources`` — the NON-tree-event notification fan-out seam
  (WS-A3b): a JSON array of source discriminators (``comment`` | ``process`` |
  ``sla`` | ``change_request``). Validated against the same closed set the Webhook
  Delivery ``source`` column allows (minus ``tree_event``, which rides
  ``event_types``).

The dispatcher signs each delivery with ``secret`` (HMAC-SHA256 via
``arbor.core.security``). This controller normalizes both JSON filters, keeps the
Dynamic Link ``target_doctype`` consistent with ``scope``, and guards the secret
so a rotation clears any stale cached value.
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

#: The NON-tree-event notification sources an endpoint may subscribe to — the
#: Webhook Delivery ``source`` Select minus ``tree_event`` (which rides
#: ``event_types``). Kept in sync with the delivery doctype's options.
NOTIFICATION_SOURCES = ("comment", "process", "sla", "change_request")


class WebhookEndpoint(Document):
	def validate(self) -> None:
		self.target_doctype = _SCOPE_TO_DOCTYPE.get(self.scope, "Tree Sheet")
		self._validate_event_types()
		self._validate_notification_sources()

	def _parse_json_list(self, raw) -> list:
		if isinstance(raw, str):
			return frappe.parse_json(raw) if raw.strip() else []
		return raw or []

	def _validate_event_types(self) -> None:
		"""Reject subscriptions to event types outside the closed set (WEBHOOKS-044)
		so a bogus type can't create a silently-never-matching endpoint."""
		types = self._parse_json_list(self.event_types)
		bad = [t for t in types if t not in EVENT_TYPES]
		if bad:
			frappe.throw(
				f"Unknown Tree Event type(s) {bad}; the closed set is {', '.join(EVENT_TYPES)}."
			)

	def _validate_notification_sources(self) -> None:
		"""Reject notification sources outside the closed set so an endpoint can't
		subscribe to a source the fan-out seam will never emit. ``tree_event`` is
		NOT a valid notification source here — it rides ``event_types``."""
		sources = self._parse_json_list(self.notification_sources)
		bad = [s for s in sources if s not in NOTIFICATION_SOURCES]
		if bad:
			frappe.throw(
				f"Unknown notification source(s) {bad}; the closed set is "
				f"{', '.join(NOTIFICATION_SOURCES)}."
			)
