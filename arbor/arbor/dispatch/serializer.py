"""Canonical Tree Event serialization for delivery (ARCHITECTURE §7;
WEBHOOKS-010/011/015).

The webhook payload IS the serialized Tree Event — the same field set the audit
stream carries: ``type``, ``sheet``, ``payload``, ``actor``, ``actor_type``,
``change_request``, ``timestamp``, ``event_id``. The event's own ``payload`` is
passed through verbatim (no surface re-derives it; WEBHOOKS-011).

``serialize_event_bytes`` returns BYTE-STABLE JSON: ``sort_keys=True`` and a
compact separator so the exact bytes signed are the exact bytes transmitted, and
the receiver can recompute HMAC over the wire bytes (WEBHOOKS-015/028).
"""

from __future__ import annotations

import json
from typing import Any

from .ports import EventView


def serialize_event_dict(event: EventView) -> dict[str, Any]:
    """The canonical delivery dict (also handy for assertions/tests)."""
    return {
        "type": event.type,
        "sheet": event.sheet,
        "payload": event.payload or {},
        "actor": event.actor,
        "actor_type": event.actor_type,
        "change_request": event.change_request,
        "timestamp": event.timestamp,
        "event_id": event.name,
    }


def serialize_event_bytes(event: EventView) -> bytes:
    """Byte-stable JSON body. Stable key ordering, no trailing whitespace drift,
    UTF-8 — so HMAC over these bytes verifies on the receiver (WEBHOOKS-015)."""
    return json.dumps(
        serialize_event_dict(event),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
