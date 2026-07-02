"""Canonical delivery serialization (ARCHITECTURE §7; WEBHOOKS-010/011/015).

Two payload shapes ride the SAME signed/backoff/retry delivery engine:

* **Tree Event** — the webhook payload IS the serialized Tree Event: the same
  field set the audit stream carries (``type``, ``sheet``, ``payload``,
  ``actor``, ``actor_type``, ``change_request``, ``timestamp``, ``event_id``).
  The event's own ``payload`` is passed through verbatim (WEBHOOKS-011).
* **Notification** — the notification fan-out seam (WS-A3b) reaches webhooks with
  a NON-tree-event payload for comment/process/sla/change_request notifications
  that never become Tree Events. It carries the SAME envelope keys plus a
  ``source`` discriminator so a consumer distinguishes the two shapes on ONE URL
  without a second code path. ``type`` is a display string (e.g. ``COMMENT_ADDED``)
  and is deliberately NOT constrained to the closed EVENT_TYPES set.

Both ``serialize_*_bytes`` helpers return BYTE-STABLE JSON: ``sort_keys=True`` and
a compact separator so the exact bytes signed are the exact bytes transmitted,
and the receiver can recompute HMAC over the wire bytes (WEBHOOKS-015/028).
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .ports import EventView

#: The delivery source discriminator carried in a notification-shaped payload's
#: ``source`` key. Tree-event payloads carry the reserved default below.
TREE_EVENT_SOURCE = "tree_event"


def _stable_bytes(body: Mapping[str, Any]) -> bytes:
    """Byte-stable JSON: stable key ordering, no whitespace drift, UTF-8 — so an
    HMAC over these bytes verifies on the receiver (WEBHOOKS-015)."""
    return json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def serialize_event_dict(event: EventView) -> dict[str, Any]:
    """The canonical Tree Event delivery dict (also handy for assertions/tests)."""
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
    """Byte-stable Tree Event JSON body (WEBHOOKS-015)."""
    return _stable_bytes(serialize_event_dict(event))


def serialize_notification_dict(
    *,
    event_id: str,
    source: str,
    sheet: str | None,
    type: str,
    payload: Mapping[str, Any] | None = None,
    actor: str | None = None,
    actor_type: str = "system",
    timestamp: str | None = None,
) -> dict[str, Any]:
    """The canonical NON-tree-event (notification) delivery dict.

    Mirrors the Tree Event envelope so a consumer parses ONE shape, keyed off the
    ``source`` discriminator (``comment`` | ``process`` | ``sla`` |
    ``change_request``). ``event_id`` is the source's stable id (the Notification
    name) and doubles as the ``X-Arbor-Event-Id`` idempotency key. ``type`` is a
    display string, deliberately NOT a closed EVENT_TYPES member; ``change_request``
    is always ``None`` at the top level (a CR notification carries its link inside
    ``payload``, keeping the tree-event ``change_request`` slot meaning "this
    delivery replays a CR-gated write")."""
    return {
        "type": type,
        "sheet": sheet,
        "payload": dict(payload or {}),
        "actor": actor,
        "actor_type": actor_type,
        "change_request": None,
        "timestamp": timestamp,
        "event_id": event_id,
        "source": source,
    }


def serialize_notification_bytes(**kwargs: Any) -> bytes:
    """Byte-stable notification JSON body (WEBHOOKS-015). Same signing contract as
    :func:`serialize_event_bytes`."""
    return _stable_bytes(serialize_notification_dict(**kwargs))
