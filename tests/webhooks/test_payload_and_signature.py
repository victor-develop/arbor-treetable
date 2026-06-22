"""WEBHOOKS payload schema + HMAC signature — runnable bench-free.

Drives the REAL ``arbor.core.executor.execute_action`` against the canonical seed
so genuine Tree Events hit the emitter, fans them out through the
``WebhookDispatcher``, and POSTs over the loopback :class:`LocalHTTPReceiver`. The
receiver (not the dispatcher) recomputes HMAC over the exact received bytes — the
contract a real consumer relies on.

Maps WEBHOOKS-010, 011, 012, 013, 014, 015, 016, 017, 018, 019, 020.
"""

from __future__ import annotations

import pytest

from arbor.core.security import verify_signature
from arbor.core.types import Actor, ActorType
from arbor.arbor.dispatch.serializer import serialize_event_bytes

from tests.fixtures.canonical import C, E
from tests.webhooks.harness import EventBridge, LocalHTTPReceiver, endpoint, make_world

SECRET = "ext-secret-001"
PAYLOAD_FIELDS = {
    "type",
    "sheet",
    "payload",
    "actor",
    "actor_type",
    "change_request",
    "timestamp",
    "event_id",
}


@pytest.fixture
def receiver():
    rcv = LocalHTTPReceiver()
    try:
        yield rcv
    finally:
        rcv.shutdown()


def _world(receiver, event_types=None, secret=SECRET, **ep):
    w = make_world(receiver=receiver)
    receiver.set_default(200)
    w.store.add_endpoint(
        endpoint(url=receiver.url, secret=secret, event_types=event_types, **ep)
    )
    return w


def _update_budget(w, value=42, node=None, actor=C):
    return w.execute(
        "updateCell",
        {"sheet": w.fx.sheet, "node": node or w.fx.X, "column": w.fx.col_budget, "value": value},
        Actor(actor),
    )


# --- B. payload schema -----------------------------------------------------
def test_delivered_body_is_full_tree_event_field_set(receiver):
    """WEBHOOKS-010: body is the serialized Tree Event with the full field set."""
    w = _world(receiver, event_types=["NODE_VALUE_UPDATED"])
    _update_budget(w, 42)
    body = receiver.requests[0].json()
    assert set(body) == PAYLOAD_FIELDS
    assert body["type"] == "NODE_VALUE_UPDATED"
    assert body["sheet"] == w.fx.sheet
    assert body["change_request"] is None  # direct authorized write, no CR
    assert body["event_id"] == receiver.requests[0].event_id


def test_node_value_updated_payload_carries_node_column_old_new_version(receiver):
    """WEBHOOKS-011: payload carries {node, column, old_value, new_value, version},
    verbatim from the event's own payload (no surface re-derives it)."""
    w = _world(receiver, event_types=["NODE_VALUE_UPDATED"])
    _update_budget(w, value=42)  # canonical seed has X.budget = 1000
    p = receiver.requests[0].json()["payload"]
    assert p["node"] == w.fx.X
    assert p["column"] == w.fx.col_budget
    assert p["old_value"] == 1000
    assert p["new_value"] == 42
    assert p["version"] == 2  # seeded at 1, bumped once


def test_change_proposed_payload_references_cr(receiver):
    """WEBHOOKS-012: E's unauthorized updateCell → CR + CHANGE_PROPOSED; the
    delivery's top-level + payload both reference the CR; actor=E, human."""
    w = _world(receiver, event_types=["CHANGE_PROPOSED"])
    out = _update_budget(w, value=99, actor=E)  # E is suggest-only on col:budget
    assert out.kind == "suggested"
    body = receiver.requests[0].json()
    assert body["type"] == "CHANGE_PROPOSED"
    assert body["change_request"] == out.change_request
    assert body["payload"]["change_request"] == out.change_request
    assert body["payload"]["action"] == "updateCell"
    assert body["actor"] == E and body["actor_type"] == ActorType.HUMAN.value


def test_change_approved_lifecycle_two_deliveries(receiver):
    """WEBHOOKS-013: approveChange replays the handler, emitting NODE_VALUE_UPDATED
    then CHANGE_APPROVED — webhooks observe BOTH in emission order; CHANGE_APPROVED
    carries the CR link. (The replayed mutation event's own change_request is None
    per the core executor's _run_and_emit; the CR link rides CHANGE_APPROVED.)"""
    w = _world(receiver, event_types=["NODE_VALUE_UPDATED", "CHANGE_APPROVED"])
    out = _update_budget(w, value=77, actor=E)  # propose
    cr = out.change_request
    receiver.requests.clear()  # ignore the (unsubscribed) CHANGE_PROPOSED phase
    w.execute("approveChange", {"change_request": cr}, Actor(C))  # C owns col:budget

    types = [r.json()["type"] for r in receiver.requests]
    assert types == ["NODE_VALUE_UPDATED", "CHANGE_APPROVED"]  # emission order
    approved = receiver.requests[1].json()
    assert approved["change_request"] == cr
    assert approved["actor"] == C  # replayed AS the approver


def test_actor_type_agent_surfaces_in_payload(receiver):
    """WEBHOOKS-014: an agent acting as its own User with column authority writes
    directly; the delivery shows actor_type=agent, distinguishable with no separate
    code path."""
    w = make_world(receiver=receiver)
    receiver.set_default(200)
    w.store.add_endpoint(endpoint(url=receiver.url, secret=SECRET, event_types=["NODE_VALUE_UPDATED"]))
    # Grant the AGENT user editor authority on col:status, then it writes directly.
    w.repo.set_column_authority(w.fx.sheet, w.fx.col_status, editors=["AGENT"])
    w.execute(
        "updateCell",
        {"sheet": w.fx.sheet, "node": w.fx.X, "column": w.fx.col_status, "value": "doing"},
        Actor("AGENT", ActorType.AGENT),
    )
    body = receiver.requests[0].json()
    assert body["actor"] == "AGENT"
    assert body["actor_type"] == ActorType.AGENT.value


# --- C. HMAC signature -----------------------------------------------------
def test_signature_header_present_and_verifies_over_wire_bytes(receiver):
    """WEBHOOKS-015/016: X-Arbor-Signature = sha256=HMAC(secret, raw_body); the
    receiver recomputes over the exact received bytes and it matches; the same
    value is persisted on the Webhook Delivery."""
    w = _world(receiver, event_types=["NODE_VALUE_UPDATED"])
    _update_budget(w, 42)
    req = receiver.requests[0]
    assert req.signature is not None and req.signature.startswith("sha256=")
    assert req.verify(SECRET)  # receiver-side HMAC over received bytes
    delivery = w.deliveries()[0]
    assert delivery["signature"] == req.signature


def test_tampered_body_fails_verification(receiver):
    """WEBHOOKS-017: altering a single byte of the captured body breaks the HMAC."""
    w = _world(receiver, event_types=["NODE_VALUE_UPDATED"])
    _update_budget(w, 42)
    req = receiver.requests[0]
    tampered = bytearray(req.body)
    tampered[0] ^= 0x01
    assert verify_signature(SECRET, req.body, req.signature) is True
    assert verify_signature(SECRET, bytes(tampered), req.signature) is False


def test_per_endpoint_secret_isolation(receiver):
    """WEBHOOKS-018: two endpoints, same event, different secrets — each delivery
    verifies only under its own secret."""
    w = make_world(receiver=receiver)
    receiver.set_default(200)
    w.store.add_endpoint(endpoint("EP1", url=receiver.url, secret="secret-1", event_types=["NODE_VALUE_UPDATED"]))
    w.store.add_endpoint(
        endpoint("EP2", url=receiver.url, secret="secret-2", scope="column", target=w.fx.col_budget, event_types=["NODE_VALUE_UPDATED"])
    )
    _update_budget(w, 42)
    d1 = w.deliveries_for("EP1")[0]
    d2 = w.deliveries_for("EP2")[0]
    # bodies identical, signatures differ by secret
    assert verify_signature("secret-1", d1["body"], d1["signature"])
    assert verify_signature("secret-2", d2["body"], d2["signature"])
    assert not verify_signature("secret-2", d1["body"], d1["signature"])  # cross-secret fails


def test_secret_rotation_signs_new_deliveries_with_new_key(receiver):
    """WEBHOOKS-019: rotating the endpoint secret signs subsequent deliveries with
    the new key; the prior delivery's stored signature is unchanged."""
    w = _world(receiver, event_types=["NODE_VALUE_UPDATED"], secret="old-secret")
    _update_budget(w, 42)  # event 1, signed old
    old_delivery = w.deliveries()[0]
    assert verify_signature("old-secret", old_delivery["body"], old_delivery["signature"])

    # rotate the endpoint secret on the live store row, fire a new event
    w.store.endpoints["EXT_ENDPOINT"].secret = "new-secret"
    _update_budget(w, 7, node=w.fx.Y)  # event 2, signed new

    deliveries = w.deliveries()
    new_delivery = [d for d in deliveries if d["name"] != old_delivery["name"]][0]
    assert verify_signature("new-secret", new_delivery["body"], new_delivery["signature"])
    assert not verify_signature("old-secret", new_delivery["body"], new_delivery["signature"])
    # historical row preserved
    assert old_delivery["signature"] == w.store.deliveries[old_delivery["name"]]["signature"]
    assert verify_signature("old-secret", old_delivery["body"], old_delivery["signature"])


def test_event_id_header_equals_tree_event_and_payload_event_id(receiver):
    """WEBHOOKS-020: X-Arbor-Event-Id == delivery.tree_event == payload.event_id,
    so a consumer can dedupe on it."""
    w = _world(receiver, event_types=["NODE_VALUE_UPDATED"])
    _update_budget(w, 42)
    req = receiver.requests[0]
    delivery = w.deliveries()[0]
    assert req.event_id == delivery["tree_event"]
    assert req.json()["event_id"] == delivery["tree_event"]


def test_signed_bytes_equal_transmitted_bytes(receiver):
    """WEBHOOKS-015: the bytes signed are byte-identical to the bytes transmitted
    (no re-serialization drift). The serializer's output equals the wire body."""
    w = _world(receiver, event_types=["NODE_VALUE_UPDATED"])
    out = _update_budget(w, 42)
    emitted = w.sink.last()
    expected_body = serialize_event_bytes(EventBridge.of(emitted))
    assert receiver.requests[0].body == expected_body
    assert w.deliveries()[0]["body"] == expected_body
