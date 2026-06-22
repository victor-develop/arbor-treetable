"""Dispatch-lane ports (the injectable seams that keep the dispatchers
deterministically testable WITHOUT a Frappe bench).

The notification + webhook dispatchers react to NEW Tree Event rows. The pure
fan-out logic (subscription/endpoint matching, payload serialization, HMAC
signing via ``arbor.core.security``, retry scheduling via ``arbor.core.backoff``)
lives in :mod:`arbor.dispatch.matcher`, :mod:`arbor.dispatch.notify` and
:mod:`arbor.dispatch.webhook`. Those modules depend ONLY on these Protocols plus
the pure core — never on frappe.

The Frappe binding (:mod:`arbor.dispatch.frappe_dispatch`) supplies real
implementations (the ORM-backed store, ``requests``-backed transport, and the
wall clock); tests supply in-memory doubles (:mod:`arbor.dispatch.testing`).

Nothing here imports frappe.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Read-views the dispatchers operate on. The adapter returns these (or any
# duck-typed object exposing the same attributes); see DATA-MODEL §7/§10/§12.
# ---------------------------------------------------------------------------
@runtime_checkable
class EventView(Protocol):
    """A persisted Tree Event row (DATA-MODEL §12). This is the SAME stream that
    feeds both dispatchers — they are pure consumers, never producers."""

    name: str  # the Tree Event id (== payload event_id, == X-Arbor-Event-Id)
    sheet: str
    type: str
    payload: dict[str, Any]
    actor: Optional[str]
    actor_type: str
    change_request: Optional[str]
    timestamp: Optional[str]


@runtime_checkable
class SubscriptionView(Protocol):
    """A Subscription row (DATA-MODEL §7)."""

    name: str
    subscriber: str
    subscriber_kind: str  # "user" | "external"
    scope: str  # "sheet" | "branch" | "column"
    target: str  # Tree Sheet | Tree Node (branch root) | Tree Column
    event_types: list[str]
    delivery: str  # "in-app" | "email" | "webhook"
    requires_ack: bool


@runtime_checkable
class WebhookEndpointView(Protocol):
    """A Webhook Endpoint row (DATA-MODEL §10)."""

    name: str
    url: str
    secret: str
    event_types: list[str]
    scope: str  # "sheet" | "branch" | "column"
    target: str
    active: bool


# ---------------------------------------------------------------------------
# Injectable side-effect seams.
# ---------------------------------------------------------------------------
class Clock(Protocol):
    """Wall-clock seam. Tests inject a freezable/advanceable clock so
    ``delivered_at`` / ``next_retry_at`` are deterministic (TEST-PLAN §1.2)."""

    def now(self) -> Any:
        """Return the current instant (a ``datetime``; tests may use a stub)."""
        ...


class Transport(Protocol):
    """HTTP transport seam for webhook delivery. Tests inject a programmable
    receiver returning 2xx/4xx/5xx/timeout/malformed and capturing raw body +
    headers (TEST-PLAN §1.2 webhook harness)."""

    def post(
        self, url: str, body: bytes, headers: dict[str, str], timeout: float
    ) -> "TransportResponse":
        """POST ``body`` to ``url``. MUST NOT follow redirects (WEBHOOKS-030).
        On timeout/connection error, raise :class:`TransportTimeout` (treated as
        a retryable failure, WEBHOOKS-023)."""
        ...


class TransportTimeout(Exception):
    """Raised by a :class:`Transport` when the request times out or the
    connection fails — classified as a retryable failure (WEBHOOKS-023)."""


class TransportResponse(Protocol):
    status_code: int
    text: str


# ---------------------------------------------------------------------------
# Persistence seams. The dispatchers never touch frappe directly; they read /
# write Notification, Acknowledgement, Webhook Delivery through these stores.
# ---------------------------------------------------------------------------
class NotificationStore(Protocol):
    """Persistence for the notification/ack ledger (DATA-MODEL §8/§9)."""

    def live_subscriptions(self, sheet: str) -> list[SubscriptionView]:
        """All ACTIVE subscriptions for the sheet (matching reads LIVE rows —
        no back-fill of removed/late subscriptions; NOTIFICATIONS_AND_ACK-037/038)."""
        ...

    def get_node_range(self, node: str) -> Optional[tuple[int, int]]:
        """Return ``(lft, rgt)`` for a Tree Node, or ``None`` if it no longer
        exists (dangling branch target, NOTIFICATIONS_AND_ACK-041)."""
        ...

    def notification_exists(self, tree_event: str, recipient: str, channel: str) -> bool:
        """True if a Notification already exists for this ``(tree_event,
        recipient, channel)`` — keeps fan-out idempotent (NOTIFICATIONS_AND_ACK-014)."""
        ...

    def create_notification(self, data: dict[str, Any]) -> str:
        """Insert a Notification row; return its id."""
        ...

    def count_ack_required(self, *, tree_event: Optional[str] = None,
                           change_request: Optional[str] = None) -> int:
        """Count Notifications with ``requires_ack=1`` for an event OR a CR."""
        ...

    def count_acknowledged(self, *, tree_event: Optional[str] = None,
                           change_request: Optional[str] = None) -> int:
        """Count Acknowledgements whose Notification (ack-required) matches the
        event OR CR."""
        ...


class WebhookStore(Protocol):
    """Persistence for webhook endpoints + the delivery log (DATA-MODEL §10/§11)."""

    def active_endpoints(self, sheet: str) -> list[WebhookEndpointView]:
        """All ACTIVE Webhook Endpoint rows reachable for the sheet. Inactive
        endpoints are excluded so deactivation stops delivery (WEBHOOKS-003)."""
        ...

    def get_endpoint(self, endpoint: str) -> Optional[WebhookEndpointView]:
        """Return the endpoint, or ``None`` if deleted (WEBHOOKS-005)."""
        ...

    def get_node_range(self, node: str) -> Optional[tuple[int, int]]:
        ...

    def delivery_exists(self, endpoint: str, tree_event: str) -> bool:
        """True if a Webhook Delivery already exists for ``(endpoint,
        tree_event)`` — one delivery per pair (WEBHOOKS-032)."""
        ...

    def create_delivery(self, data: dict[str, Any]) -> str:
        """Insert a Webhook Delivery row; return its id."""
        ...

    def update_delivery(self, delivery: str, patch: dict[str, Any]) -> None:
        ...

    def get_delivery(self, delivery: str) -> dict[str, Any]:
        ...

    def due_deliveries(self, now: Any) -> list[dict[str, Any]]:
        """Pending deliveries whose ``next_retry_at <= now`` (the retry runner's
        work queue)."""
        ...

    def claim_delivery(self, delivery: str) -> bool:
        """Atomically claim a delivery for one retry attempt; return False if
        another worker already claimed it (WEBHOOKS-033 concurrency guard)."""
        ...
