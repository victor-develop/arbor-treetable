"""Pure in-memory doubles for the dispatch seams — bench-free determinism.

Mirrors ``arbor.core.testing``: an in-memory ``NotificationStore`` +
``WebhookStore``, a freezable ``FakeClock``, and a programmable ``FakeTransport``
HTTP receiver. These let the notification + webhook dispatchers be exercised
end-to-end without a Frappe site (TEST-PLAN §1.2 harness).

Nothing here imports frappe.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from .ports import TransportTimeout


# ---------------------------------------------------------------------------
# View doubles (duck-typed to the *View protocols in ports.py)
# ---------------------------------------------------------------------------
@dataclass
class FakeEvent:
    name: str
    sheet: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    actor: Optional[str] = None
    actor_type: str = "human"
    change_request: Optional[str] = None
    timestamp: Optional[str] = None


@dataclass
class FakeSubscription:
    name: str
    subscriber: str
    scope: str
    target: str
    event_types: list[str]
    delivery: str = "in-app"
    requires_ack: bool = False
    subscriber_kind: str = "user"


@dataclass
class FakeEndpoint:
    name: str
    url: str
    secret: str
    event_types: list[str]
    scope: str
    target: str
    active: bool = True


# ---------------------------------------------------------------------------
# Clock + Transport
# ---------------------------------------------------------------------------
class FakeClock:
    """Freezable/advanceable clock. ``advance(seconds)`` moves it forward so
    backoff/`delivered_at` are deterministic."""

    def __init__(self, start: Optional[datetime] = None) -> None:
        self._now = start or datetime(2026, 1, 1, 0, 0, 0)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)

    def set(self, when: datetime) -> None:
        self._now = when


@dataclass
class FakeResponse:
    status_code: int
    text: str = ""


class FakeTransport:
    """Programmable HTTP receiver. Configure per-call outcomes via ``responses``
    (a list consumed in order) or a single ``default`` outcome. Captures every
    request (url, body, headers) for assertion. Use the sentinel ``TIMEOUT`` to
    simulate a timeout/connection failure.
    """

    TIMEOUT = object()

    def __init__(
        self,
        responses: Optional[list[Any]] = None,
        default: Any = None,
    ) -> None:
        self._responses = list(responses or [])
        self._default = default if default is not None else FakeResponse(200, "OK")
        self.requests: list[dict[str, Any]] = []

    def post(self, url, body, headers, timeout):
        self.requests.append(
            {"url": url, "body": body, "headers": dict(headers), "timeout": timeout}
        )
        outcome = self._responses.pop(0) if self._responses else self._default
        if outcome is self.TIMEOUT:
            raise TransportTimeout("simulated timeout")
        if isinstance(outcome, int):
            return FakeResponse(outcome, "")
        return outcome


# ---------------------------------------------------------------------------
# In-memory NotificationStore
# ---------------------------------------------------------------------------
class InMemoryNotificationStore:
    """Pure ``NotificationStore`` for the notification dispatcher."""

    def __init__(self) -> None:
        self.subscriptions: dict[str, FakeSubscription] = {}
        self.node_ranges: dict[str, tuple[int, int]] = {}
        self.notifications: dict[str, dict[str, Any]] = {}
        self.acknowledgements: dict[str, dict[str, Any]] = {}
        self._ids = itertools.count(1)

    # seeding helpers
    def add_subscription(self, sub: FakeSubscription) -> str:
        self.subscriptions[sub.name] = sub
        return sub.name

    def remove_subscription(self, name: str) -> None:
        self.subscriptions.pop(name, None)

    def set_node_range(self, node: str, lft: int, rgt: int) -> None:
        self.node_ranges[node] = (lft, rgt)

    def remove_node(self, node: str) -> None:
        self.node_ranges.pop(node, None)

    def add_acknowledgement(self, notification: str, user: str) -> str:
        name = f"ack-{next(self._ids)}"
        self.acknowledgements[name] = {"notification": notification, "user": user}
        return name

    # NotificationStore protocol
    def live_subscriptions(self, sheet: str):
        # subscriptions store their own scope/target; sheet filtering happens in
        # the matcher (sheet-scope) or via the node-range/column on match.
        return list(self.subscriptions.values())

    def get_node_range(self, node: str) -> Optional[tuple[int, int]]:
        return self.node_ranges.get(node)

    def notification_exists(self, tree_event: str, recipient: str, channel: str) -> bool:
        return any(
            n["tree_event"] == tree_event
            and n["recipient"] == recipient
            and n["channel"] == channel
            for n in self.notifications.values()
        )

    def create_notification(self, data: dict[str, Any]) -> str:
        name = f"notif-{next(self._ids)}"
        self.notifications[name] = {"name": name, **data}
        return name

    def count_ack_required(self, *, tree_event=None, change_request=None) -> int:
        return sum(
            1
            for n in self.notifications.values()
            if n.get("requires_ack")
            and self._scoped(n, tree_event, change_request)
        )

    def count_acknowledged(self, *, tree_event=None, change_request=None) -> int:
        ack_notifs = {a["notification"] for a in self.acknowledgements.values()}
        return sum(
            1
            for name, n in self.notifications.items()
            if n.get("requires_ack")
            and name in ack_notifs
            and self._scoped(n, tree_event, change_request)
        )

    @staticmethod
    def _scoped(n, tree_event, change_request) -> bool:
        if tree_event is not None:
            return n.get("tree_event") == tree_event
        return n.get("change_request") == change_request


# ---------------------------------------------------------------------------
# In-memory WebhookStore
# ---------------------------------------------------------------------------
class InMemoryWebhookStore:
    """Pure ``WebhookStore`` for the webhook dispatcher + retry runner."""

    def __init__(self) -> None:
        self.endpoints: dict[str, FakeEndpoint] = {}
        self.node_ranges: dict[str, tuple[int, int]] = {}
        self.deliveries: dict[str, dict[str, Any]] = {}
        self._claimed: set[str] = set()
        self._ids = itertools.count(1)

    # seeding helpers
    def add_endpoint(self, ep: FakeEndpoint) -> str:
        self.endpoints[ep.name] = ep
        return ep.name

    def remove_endpoint(self, name: str) -> None:
        self.endpoints.pop(name, None)

    def set_node_range(self, node: str, lft: int, rgt: int) -> None:
        self.node_ranges[node] = (lft, rgt)

    # WebhookStore protocol
    def active_endpoints(self, sheet: str):
        return [e for e in self.endpoints.values() if e.active]

    def get_endpoint(self, endpoint: str) -> Optional[FakeEndpoint]:
        return self.endpoints.get(endpoint)

    def get_node_range(self, node: str) -> Optional[tuple[int, int]]:
        return self.node_ranges.get(node)

    def delivery_exists(self, endpoint: str, tree_event: str) -> bool:
        return any(
            d["endpoint"] == endpoint and d["tree_event"] == tree_event
            for d in self.deliveries.values()
        )

    def create_delivery(self, data: dict[str, Any]) -> str:
        name = f"wd-{next(self._ids)}"
        self.deliveries[name] = {"name": name, **data}
        return name

    def update_delivery(self, delivery: str, patch: dict[str, Any]) -> None:
        self.deliveries[delivery].update(patch)
        self._claimed.discard(delivery)  # release claim once the attempt records

    def get_delivery(self, delivery: str) -> dict[str, Any]:
        return self.deliveries[delivery]

    def due_deliveries(self, now) -> list[dict[str, Any]]:
        out = []
        for d in self.deliveries.values():
            if d["status"] != "pending":
                continue
            nra = d.get("next_retry_at")
            if nra is not None and nra <= now:
                out.append(d)
        return out

    def claim_delivery(self, delivery: str) -> bool:
        if delivery in self._claimed:
            return False
        self._claimed.add(delivery)
        return True
