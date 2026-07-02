"""Notification -> webhook fan-out seam (Area 3, WS-A3b).

Comment / process / sla / change_request notifications never become Tree Events
(only structural writes do), so the tree-event webhook lane (``on_tree_event``)
never sees them. This seam bridges that gap: given a JUST-CREATED Notification, it
finds the ACTIVE Webhook Endpoints that (a) subscribe to the notification's
``source`` and (b) are scoped to the notification's sheet, and ENQUEUES one Webhook
Delivery per endpoint through the SAME signed / backoff / retry engine the
tree-event lane uses (:mod:`arbor.arbor.dispatch.webhook`). It reinvents nothing —
signing, idempotency, and retry all live in the reused ``deliver_notification`` /
``serialize_notification_bytes`` seams from WS-A3a.

Purity + testability: :func:`fan_out` takes its side-effect collaborators
(``store`` for the endpoint set, ``serialize`` for the byte-stable body, ``deliver``
for the enqueue+first-attempt) as INJECTED deps — mirroring
``ProcessDispatcher(repo, notify, clock)`` — so it runs deterministically over the
in-memory ``InMemoryWebhookStore`` + a ``WebhookDispatcher`` bound to a
``FakeTransport`` in the unit tests, with no frappe import here.

Idempotency: per ``(endpoint, event_id)`` where ``event_id`` is the Notification's
own name. The reused ``deliver_notification`` enforces this key (a replay of the
same Notification is a no-op returning ``None``), so this seam does no de-dupe of
its own beyond passing the stable id through.

Nothing here imports frappe.

Wiring note for WS-A3c (do NOT wire it here):

    The Frappe binding declares this seam on the Notification birth. In
    ``arbor/hooks.py``::

        doc_events = {
            "Notification": {"after_insert": "arbor.arbor.dispatch."
                             "frappe_dispatch.on_notification_insert"},
        }

    where ``on_notification_insert(doc, method=None)`` builds a
    :class:`NotificationView` from the inserted Notification Document (resolving its
    ``sheet`` from the source row — the Notification DocType has no ``sheet`` column
    of its own), then calls :func:`fan_out` with a Frappe-backed
    ``WebhookStore`` + a ``WebhookDispatcher.deliver_notification`` bound deliver.
    Tree-event-sourced Notifications are already covered by the tree-event webhook
    lane, so A3c's adapter should skip ``source == 'tree_event'`` to avoid a
    double delivery.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Protocol, runtime_checkable

from .ports import WebhookEndpointView, WebhookStore
from .serializer import serialize_notification_bytes

#: The non-tree-event notification sources that fan out to webhooks. A Notification
#: with ``source == 'tree_event'`` is delivered by the tree-event lane instead and
#: must NOT be routed here (it would double-deliver).
FAN_OUT_SOURCES = ("comment", "process", "sla", "change_request")


@runtime_checkable
class NotificationView(Protocol):
    """A just-created Notification, adapted for fan-out (WS-A3b).

    The persisted Notification DocType has no ``sheet`` column, so the adapter
    (WS-A3c) resolves the owning sheet from the source row and supplies it here.
    ``type`` is a human-facing DISPLAY string (e.g. ``COMMENT_ADDED``,
    ``PROCESS_EXPECT_OPENED``) deliberately NOT constrained to the closed
    EVENT_TYPES set. ``payload`` carries source-specific context (and, for a CR
    notification, the CR link — the top-level ``change_request`` slot is reserved
    for tree-event replays)."""

    name: str  # the Notification id == X-Arbor-Event-Id idempotency key
    source: str  # comment | process | sla | change_request
    sheet: Optional[str]
    type: str
    payload: Optional[dict[str, Any]]
    actor: Optional[str]
    actor_type: str
    timestamp: Optional[str]


#: Signature of the reused enqueue seam (``WebhookDispatcher.deliver_notification``).
DeliverNotification = Callable[..., Optional[str]]

#: Signature of the reused body serializer (``serialize_notification_bytes``).
SerializeNotification = Callable[..., bytes]


def _endpoint_scoped_to_sheet(endpoint: WebhookEndpointView, sheet: Optional[str]) -> bool:
    """A notification-source endpoint matches a notification when the endpoint's
    registration ``sheet`` equals the notification's sheet. A legacy/global endpoint
    (``sheet is None``) matches ANY sheet, so pre-registration-surface endpoints keep
    receiving. If the notification itself has no sheet, only a global endpoint
    matches."""
    ep_sheet = getattr(endpoint, "sheet", None)
    if ep_sheet is None:
        return True
    return ep_sheet == sheet


def fan_out(
    notification: NotificationView,
    *,
    store: WebhookStore,
    deliver: DeliverNotification,
    serialize: SerializeNotification = serialize_notification_bytes,
) -> list[str]:
    """Enqueue one Webhook Delivery per matching endpoint for ``notification``.

    Selection: ACTIVE endpoints whose ``notification_sources`` contains
    ``notification.source`` (``store.notification_endpoints``) AND whose registration
    sheet matches the notification's sheet (:func:`_endpoint_scoped_to_sheet`). For
    each, a byte-stable notification body is built via ``serialize`` and handed to
    ``deliver`` (the reused ``WebhookDispatcher.deliver_notification``), which signs,
    persists ONE delivery, and drives the first attempt.

    Idempotent per ``(endpoint, event_id)`` — the shared key ``deliver`` enforces,
    with ``event_id == notification.name`` — so a replayed Notification (at-least-once
    doc_event, worker retry) never double-POSTs.

    Inert for a ``tree_event`` source (that lane owns those deliveries) and for a
    notification with no matching endpoints (a no-op returning ``[]``). Returns the
    ids of the deliveries CREATED this call (skipping ``None`` from idempotent
    replays)."""
    source = notification.source
    if source not in FAN_OUT_SOURCES:
        # tree_event (or any unknown) is not a fan-out source; the tree-event lane
        # already delivers those. No-op so a mis-wired call cannot double-deliver.
        return []

    endpoints = store.notification_endpoints(source)
    if not endpoints:
        return []

    sheet = notification.sheet
    body = serialize(
        event_id=notification.name,
        source=source,
        sheet=sheet,
        type=notification.type,
        payload=getattr(notification, "payload", None),
        actor=getattr(notification, "actor", None),
        actor_type=getattr(notification, "actor_type", "system"),
        timestamp=getattr(notification, "timestamp", None),
    )

    created: list[str] = []
    for endpoint in endpoints:
        if not _endpoint_scoped_to_sheet(endpoint, sheet):
            continue
        delivery_id = deliver(
            endpoint,
            notification_id=notification.name,
            body=body,
            source=source,
        )
        if delivery_id is not None:
            created.append(delivery_id)

    return created


__all__ = [
    "FAN_OUT_SOURCES",
    "NotificationView",
    "fan_out",
]
