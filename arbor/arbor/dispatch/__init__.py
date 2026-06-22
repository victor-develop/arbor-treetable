"""Arbor dispatch lane — the notification + webhook dispatchers.

Both dispatchers are PURE CONSUMERS of the append-only Tree Event stream
(ARCHITECTURE §6/§7); the SAME new Tree Event row feeds both (DRY). They reuse
the core (``arbor.core.security`` HMAC, ``arbor.core.backoff`` retry schedule) and
a single shared scope matcher, and contain ZERO mutation logic — they never write
to the Tree Event stream.

Layering (ports & adapters):

* :mod:`arbor.dispatch.matcher`      — the ONE scope matcher (sheet|branch|column).
* :mod:`arbor.dispatch.serializer`   — byte-stable Tree Event payload for signing.
* :mod:`arbor.dispatch.notify`       — NotificationDispatcher + accountability.
* :mod:`arbor.dispatch.webhook`      — WebhookDispatcher (HMAC + retry runner).
* :mod:`arbor.dispatch.ports`        — injectable seams (Clock/Transport/Stores).
* :mod:`arbor.dispatch.testing`      — in-memory doubles (bench-free determinism).
* :mod:`arbor.dispatch.frappe_dispatch` — the Frappe adapter (doc_event +
  scheduler entrypoints, ORM-backed stores). The only file importing frappe.
"""

from __future__ import annotations

from .matcher import selector_matches
from .notify import Accountability, NotificationDispatcher
from .serializer import serialize_event_bytes, serialize_event_dict
from .webhook import (
    DELIVERED,
    EXHAUSTED,
    FAILED,
    PENDING,
    WebhookDispatcher,
    compute_next_retry_offset,
    is_success,
)

__all__ = [
    "NotificationDispatcher",
    "Accountability",
    "WebhookDispatcher",
    "selector_matches",
    "serialize_event_bytes",
    "serialize_event_dict",
    "is_success",
    "compute_next_retry_offset",
    "PENDING",
    "DELIVERED",
    "FAILED",
    "EXHAUSTED",
]
