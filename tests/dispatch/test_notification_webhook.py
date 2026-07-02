"""Notification -> webhook fan-out seam (WS-A3b) — bench-free.

Drives the pure :func:`arbor.arbor.dispatch.notification_webhook.fan_out` with
INJECTED doubles (the in-memory ``InMemoryWebhookStore`` + a real
``WebhookDispatcher`` bound to a ``FakeTransport``, so ``deliver`` is the SAME
reused ``deliver_notification`` seam WS-A3a ships — no reinvented signing/backoff),
mirroring ``test_process_dispatcher``'s injected-doubles style.

Covers: matching by sheet + source; multiple endpoints fan out independently; the
source filter excludes non-subscribed sources; idempotent replay per
(endpoint, event_id); no endpoints -> no-op; a tree_event source is inert (that
lane owns it); a signed delivery row + notification link are recorded through the
reused engine; a global (sheet=None) endpoint matches any sheet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from arbor.core.security import verify_signature

from arbor.arbor.dispatch.notification_webhook import FAN_OUT_SOURCES, fan_out
from arbor.arbor.dispatch.serializer import serialize_notification_bytes
from arbor.arbor.dispatch.testing import (
    FakeEndpoint,
    FakeResponse,
    FakeTransport,
    FakeClock,
    InMemoryWebhookStore,
)
from arbor.arbor.dispatch.webhook import (
    DELIVERED,
    EVENT_ID_HEADER,
    SOURCE_HEADER,
    WebhookDispatcher,
)

SECRET = "notif-secret"
SHEET = "S"


# ---------------------------------------------------------------------------
# Notification double (duck-typed to NotificationView)
# ---------------------------------------------------------------------------
@dataclass
class FakeNotification:
    name: str
    source: str
    sheet: Optional[str] = SHEET
    type: str = "COMMENT_ADDED"
    payload: dict[str, Any] = field(default_factory=dict)
    actor: Optional[str] = None
    actor_type: str = "system"
    timestamp: Optional[str] = None


# ---------------------------------------------------------------------------
# Harness: real WebhookDispatcher over the in-memory store + FakeTransport, so the
# injected ``deliver`` IS the reused ``deliver_notification`` seam (WS-A3a).
# ---------------------------------------------------------------------------
def _notif_endpoint(name, *, sources, sheet=SHEET, active=True, secret=SECRET):
    return FakeEndpoint(
        name=name,
        url=f"https://ext.example/{name}",
        secret=secret,
        event_types=[],  # notification-only endpoint; tree-event filter is empty
        scope="sheet",
        target=sheet or "S",
        active=active,
        notification_sources=list(sources),
        sheet=sheet,
    )


def _harness(responses=None, default=None):
    store = InMemoryWebhookStore()
    transport = FakeTransport(
        responses=responses, default=default or FakeResponse(200, "OK")
    )
    dispatcher = WebhookDispatcher(store, transport, FakeClock(), jitter=False)
    return store, transport, dispatcher


def _fan_out(store, dispatcher, notification):
    return fan_out(
        notification,
        store=store,
        deliver=dispatcher.deliver_notification,
        serialize=serialize_notification_bytes,
    )


# --- matching by sheet + source ---------------------------------------------
def test_matching_endpoint_by_sheet_and_source_enqueues_signed_delivery():
    store, transport, disp = _harness()
    ep = _notif_endpoint("EP_COMMENT", sources=["comment"], sheet=SHEET)
    store.add_endpoint(ep)

    notif = FakeNotification("notif-1", source="comment", sheet=SHEET, type="COMMENT_ADDED")
    created = _fan_out(store, disp, notif)

    assert len(created) == 1
    d = store.deliveries[created[0]]
    assert d["source"] == "comment"
    assert d["notification"] == "notif-1"
    assert d["event_id"] == "notif-1"
    assert d.get("tree_event") is None  # no Tree Event for a notification delivery
    assert d["status"] == DELIVERED  # FakeTransport default 200
    # signed over the SAME byte-stable body via the reused engine
    body = serialize_notification_bytes(
        event_id="notif-1", source="comment", sheet=SHEET, type="COMMENT_ADDED"
    )
    assert verify_signature(SECRET, body, d["signature"])
    req = transport.requests[-1]
    assert req["headers"][SOURCE_HEADER] == "comment"
    assert req["headers"][EVENT_ID_HEADER] == "notif-1"


def test_wrong_sheet_endpoint_does_not_match():
    store, _t, disp = _harness()
    store.add_endpoint(_notif_endpoint("EP_OTHER", sources=["comment"], sheet="OTHER"))
    notif = FakeNotification("notif-x", source="comment", sheet=SHEET)
    assert _fan_out(store, disp, notif) == []
    assert store.deliveries == {}


def test_global_sheet_none_endpoint_matches_any_sheet():
    """A legacy/global endpoint (sheet=None) receives regardless of the
    notification's sheet, so pre-registration-surface endpoints keep working."""
    store, _t, disp = _harness()
    store.add_endpoint(_notif_endpoint("EP_GLOBAL", sources=["process"], sheet=None))
    notif = FakeNotification("notif-g", source="process", sheet="ANY", type="PROCESS_EXPECT_OPENED")
    created = _fan_out(store, disp, notif)
    assert len(created) == 1
    assert store.deliveries[created[0]]["endpoint"] == "EP_GLOBAL"


# --- multiple endpoints ------------------------------------------------------
def test_multiple_endpoints_fan_out_independently():
    store, transport, disp = _harness()
    store.add_endpoint(_notif_endpoint("EP1", sources=["sla"], secret="s1"))
    store.add_endpoint(_notif_endpoint("EP2", sources=["sla", "process"], secret="s2"))
    # an endpoint NOT subscribed to sla must be excluded from this fan-out.
    store.add_endpoint(_notif_endpoint("EP3", sources=["comment"], secret="s3"))

    notif = FakeNotification("notif-2", source="sla", sheet=SHEET, type="SLA_BREACHED")
    created = _fan_out(store, disp, notif)

    assert len(created) == 2
    endpoints = {store.deliveries[c]["endpoint"] for c in created}
    assert endpoints == {"EP1", "EP2"}
    # per-endpoint HMAC over the shared body
    body = serialize_notification_bytes(
        event_id="notif-2", source="sla", sheet=SHEET, type="SLA_BREACHED"
    )
    sigs = {store.deliveries[c]["endpoint"]: store.deliveries[c]["signature"] for c in created}
    assert verify_signature("s1", body, sigs["EP1"])
    assert verify_signature("s2", body, sigs["EP2"])
    assert not verify_signature("s2", body, sigs["EP1"])  # cross-secret must NOT verify
    assert len(transport.requests) == 2


# --- source filter -----------------------------------------------------------
def test_source_filter_excludes_non_subscribed_sources():
    """An endpoint subscribed only to 'comment' receives nothing for an 'sla'
    notification, and vice-versa — the notification_sources filter gates delivery."""
    store, _t, disp = _harness()
    store.add_endpoint(_notif_endpoint("EP_COMMENT", sources=["comment"]))

    # sla notification: the comment-only endpoint must not match.
    assert _fan_out(store, disp, FakeNotification("n-sla", source="sla")) == []
    # comment notification: it matches.
    created = _fan_out(store, disp, FakeNotification("n-comment", source="comment"))
    assert len(created) == 1


def test_change_request_source_fans_out():
    store, transport, disp = _harness()
    store.add_endpoint(_notif_endpoint("EP_CR", sources=["change_request"]))
    notif = FakeNotification(
        "n-cr", source="change_request", sheet=SHEET, type="CHANGE_REQUEST_PENDING",
        payload={"change_request": "CR9"},
    )
    created = _fan_out(store, disp, notif)
    assert len(created) == 1
    req = transport.requests[-1]
    assert req["headers"][SOURCE_HEADER] == "change_request"
    # CR link rides inside payload; the top-level change_request slot stays None.
    import json

    body = json.loads(req["body"].decode("utf-8"))
    assert body["payload"]["change_request"] == "CR9"
    assert body["change_request"] is None


def test_tree_event_source_is_inert():
    """A tree_event-sourced notification is NOT fanned out here — the tree-event
    lane owns those deliveries; routing it here would double-deliver."""
    store, transport, disp = _harness()
    # even a fully-matching endpoint must not receive a tree_event source
    store.add_endpoint(_notif_endpoint("EP", sources=["comment"]))
    notif = FakeNotification("n-tree", source="tree_event", sheet=SHEET)
    assert _fan_out(store, disp, notif) == []
    assert transport.requests == []
    assert set(FAN_OUT_SOURCES) == {"comment", "process", "sla", "change_request"}


# --- idempotent replay -------------------------------------------------------
def test_idempotent_replay_per_endpoint_event_id():
    """Replaying the SAME notification (at-least-once doc_event) does not
    double-POST: deliver is idempotent per (endpoint, event_id)."""
    store, transport, disp = _harness()
    store.add_endpoint(_notif_endpoint("EP", sources=["process"]))
    notif = FakeNotification("n-dup", source="process", sheet=SHEET, type="PROCESS_EXPECT_OPENED")

    first = _fan_out(store, disp, notif)
    second = _fan_out(store, disp, notif)

    assert len(first) == 1
    assert second == []  # replay: idempotent no-op, no new delivery id
    assert len([d for d in store.deliveries.values() if d["endpoint"] == "EP"]) == 1
    assert len(transport.requests) == 1  # exactly one POST across both calls


# --- no endpoints ------------------------------------------------------------
def test_no_endpoints_is_noop():
    store, transport, disp = _harness()
    # no endpoints registered at all
    assert _fan_out(store, disp, FakeNotification("n-none", source="comment")) == []
    assert store.deliveries == {}
    assert transport.requests == []


def test_no_subscribed_endpoints_is_noop():
    store, transport, disp = _harness()
    # endpoints exist but none subscribe to this source
    store.add_endpoint(_notif_endpoint("EP", sources=["comment"]))
    assert _fan_out(store, disp, FakeNotification("n-x", source="sla")) == []
    assert transport.requests == []


# --- inactive endpoint excluded ---------------------------------------------
def test_inactive_endpoint_excluded():
    store, transport, disp = _harness()
    store.add_endpoint(_notif_endpoint("EP_OFF", sources=["comment"], active=False))
    assert _fan_out(store, disp, FakeNotification("n-off", source="comment")) == []
    assert transport.requests == []
