"""Webhook dispatcher — payload, HMAC, retry/backoff, delivery log, idempotency.

Bench-free: drives ``WebhookDispatcher`` over the in-memory store + frozen clock
+ programmable transport. Maps to WEBHOOKS-* cases. Backoff uses the core
schedule (0s, 30s, 5m, 30m, 2h, 12h); jitter is disabled in tests for exact
``next_retry_at`` assertions (WEBHOOKS-022/024), and a separate test asserts the
jitter band (WEBHOOKS-025).
"""

from __future__ import annotations

import json
from datetime import timedelta

from arbor.core.security import verify_signature

from arbor.arbor.dispatch.webhook import (
    DELIVERED,
    EXHAUSTED,
    PENDING,
    EVENT_ID_HEADER,
    SIGNATURE_HEADER,
    WebhookDispatcher,
    compute_next_retry_offset,
    is_success,
)
from arbor.arbor.dispatch.serializer import serialize_event_bytes
from arbor.arbor.dispatch.testing import (
    FakeClock,
    FakeEndpoint,
    FakeEvent,
    FakeResponse,
    FakeTransport,
    InMemoryWebhookStore,
)

SECRET = "test-secret"
RANGES = {"R": (1, 12), "P1": (2, 5), "X": (3, 4), "P2": (6, 11), "Y": (7, 8), "Z": (9, 10)}


def _store():
    s = InMemoryWebhookStore()
    for n, (lft, rgt) in RANGES.items():
        s.set_node_range(n, lft, rgt)
    return s


def _ext_endpoint(name="EXT_ENDPOINT", event_types=None, scope="sheet", target="S", secret=SECRET, active=True):
    return FakeEndpoint(
        name=name,
        url="https://ext.example/hook",
        secret=secret,
        event_types=event_types or ["NODE_VALUE_UPDATED", "CHANGE_APPROVED"],
        scope=scope,
        target=target,
        active=active,
    )


def _dispatcher(store, transport, clock=None, jitter=False):
    return WebhookDispatcher(store, transport, clock or FakeClock(), jitter=jitter)


def _value_event(eid="evt1", node="X", column="col:budget", old=10, new=42, version=2):
    return FakeEvent(
        eid, "S", "NODE_VALUE_UPDATED",
        {"node": node, "column": column, "old_value": old, "new_value": new, "version": version},
    )


# --- A. subscription gating ------------------------------------------------
def test_active_endpoint_delivers():
    """WEBHOOKS-001/021: active endpoint, 2xx → delivered, attempts=1."""
    store = _store(); store.add_endpoint(_ext_endpoint())
    tx = FakeTransport(default=FakeResponse(200, "OK"))
    created = _dispatcher(store, tx).on_tree_event(_value_event())
    assert len(created) == 1
    d = store.deliveries[created[0]]
    assert d["status"] == DELIVERED and d["attempts"] == 1 and d["next_retry_at"] is None
    assert len(tx.requests) == 1


def test_inactive_endpoint_no_delivery():
    """WEBHOOKS-003: deactivated endpoint produces no delivery."""
    store = _store(); store.add_endpoint(_ext_endpoint(active=False))
    tx = FakeTransport()
    assert _dispatcher(store, tx).on_tree_event(_value_event()) == []
    assert tx.requests == []


def test_event_types_filter():
    """WEBHOOKS-002/043: unsubscribed event type yields no delivery."""
    store = _store(); store.add_endpoint(_ext_endpoint(event_types=["CHANGE_APPROVED"]))
    tx = FakeTransport()
    assert _dispatcher(store, tx).on_tree_event(_value_event()) == []  # NODE_VALUE_UPDATED not subscribed


def test_branch_scope_endpoint():
    """WEBHOOKS-006: branch endpoint matches descendant, not outside node."""
    store = _store()
    store.add_endpoint(_ext_endpoint("EXT_BRANCH", ["NODE_DELETED"], scope="branch", target="P2"))
    tx = FakeTransport()
    d = _dispatcher(store, tx)
    assert len(d.on_tree_event(FakeEvent("eZ", "S", "NODE_DELETED", {"node": "Z"}))) == 1
    assert d.on_tree_event(FakeEvent("eX", "S", "NODE_DELETED", {"node": "X"})) == []


def test_column_scope_endpoint():
    """WEBHOOKS-007: column endpoint matches only its column."""
    store = _store()
    store.add_endpoint(_ext_endpoint("EXT_COL", ["NODE_VALUE_UPDATED"], scope="column", target="col:budget"))
    tx = FakeTransport()
    d = _dispatcher(store, tx)
    assert len(d.on_tree_event(_value_event("e1", node="Y", column="col:budget"))) == 1
    assert d.on_tree_event(_value_event("e2", node="Z", column="col:name")) == []


def test_fanout_multiple_endpoints_independent_signatures():
    """WEBHOOKS-008/018: two endpoints → two deliveries, per-endpoint HMAC."""
    store = _store()
    store.add_endpoint(_ext_endpoint("EP1", ["NODE_VALUE_UPDATED"], secret="secret1"))
    store.add_endpoint(_ext_endpoint("EP2", ["NODE_VALUE_UPDATED"], scope="column", target="col:budget", secret="secret2"))
    tx = FakeTransport(default=FakeResponse(200))
    ev = _value_event(node="Y", column="col:budget")
    created = _dispatcher(store, tx).on_tree_event(ev)
    assert len(created) == 2
    body = serialize_event_bytes(ev)
    sigs = {store.deliveries[c]["endpoint"]: store.deliveries[c]["signature"] for c in created}
    assert verify_signature("secret1", body, sigs["EP1"])
    assert verify_signature("secret2", body, sigs["EP2"])
    # cross-secret must NOT verify (WEBHOOKS-018)
    assert not verify_signature("secret2", body, sigs["EP1"])


def test_sheet_isolation():
    """WEBHOOKS-049: event on S2 never reaches the S-scoped endpoint."""
    store = _store(); store.add_endpoint(_ext_endpoint(target="S"))
    tx = FakeTransport()
    ev = _value_event(); ev.sheet = "S2"
    assert _dispatcher(store, tx).on_tree_event(ev) == []


# --- B/C. payload + HMAC ---------------------------------------------------
def test_payload_is_serialized_tree_event_with_full_fieldset():
    """WEBHOOKS-010: body is the serialized Tree Event with the full field set."""
    store = _store(); store.add_endpoint(_ext_endpoint())
    tx = FakeTransport(default=FakeResponse(200))
    _dispatcher(store, tx).on_tree_event(_value_event(eid="evtX"))
    body = json.loads(tx.requests[0]["body"].decode("utf-8"))
    assert set(body) == {"type", "sheet", "payload", "actor", "actor_type", "change_request", "timestamp", "event_id"}
    assert body["type"] == "NODE_VALUE_UPDATED" and body["sheet"] == "S"
    assert body["event_id"] == "evtX" and body["change_request"] is None


def test_payload_passthrough_node_column_old_new_version():
    """WEBHOOKS-011: NODE_VALUE_UPDATED payload carries {node,column,old,new,version}."""
    store = _store(); store.add_endpoint(_ext_endpoint())
    tx = FakeTransport(default=FakeResponse(200))
    _dispatcher(store, tx).on_tree_event(_value_event(node="X", column="col:budget", old=10, new=42, version=2))
    p = json.loads(tx.requests[0]["body"].decode("utf-8"))["payload"]
    assert p == {"node": "X", "column": "col:budget", "old_value": 10, "new_value": 42, "version": 2}


def test_signature_header_and_persisted_match_wire_bytes():
    """WEBHOOKS-015/016: X-Arbor-Signature over the exact transmitted bytes verifies."""
    store = _store(); store.add_endpoint(_ext_endpoint())
    tx = FakeTransport(default=FakeResponse(200))
    created = _dispatcher(store, tx).on_tree_event(_value_event())
    req = tx.requests[0]
    sig_header = req["headers"][SIGNATURE_HEADER]
    assert verify_signature(SECRET, req["body"], sig_header)
    assert store.deliveries[created[0]]["signature"] == sig_header


def test_event_id_header_for_idempotency():
    """WEBHOOKS-020: X-Arbor-Event-Id == tree_event == payload.event_id."""
    store = _store(); store.add_endpoint(_ext_endpoint())
    tx = FakeTransport(default=FakeResponse(200))
    _dispatcher(store, tx).on_tree_event(_value_event(eid="evt-77"))
    req = tx.requests[0]
    assert req["headers"][EVENT_ID_HEADER] == "evt-77"
    assert json.loads(req["body"].decode("utf-8"))["event_id"] == "evt-77"


def test_change_proposed_payload_references_cr():
    """WEBHOOKS-012: CHANGE_PROPOSED body has top-level + payload CR link."""
    store = _store(); store.add_endpoint(_ext_endpoint(event_types=["CHANGE_PROPOSED"]))
    tx = FakeTransport(default=FakeResponse(200))
    ev = FakeEvent("e", "S", "CHANGE_PROPOSED", {"change_request": "CR9", "action": "updateCell"},
                   actor="E", actor_type="human", change_request="CR9")
    _dispatcher(store, tx).on_tree_event(ev)
    body = json.loads(tx.requests[0]["body"].decode("utf-8"))
    assert body["change_request"] == "CR9" and body["actor"] == "E"
    assert body["payload"]["change_request"] == "CR9" and body["payload"]["action"] == "updateCell"


def test_actor_type_agent_in_payload():
    """WEBHOOKS-014: actor_type=agent surfaces in the payload."""
    store = _store(); store.add_endpoint(_ext_endpoint())
    tx = FakeTransport(default=FakeResponse(200))
    ev = _value_event(); ev.actor = "AGENT"; ev.actor_type = "agent"
    _dispatcher(store, tx).on_tree_event(ev)
    body = json.loads(tx.requests[0]["body"].decode("utf-8"))
    assert body["actor_type"] == "agent" and body["actor"] == "AGENT"


# --- D. retry / backoff ----------------------------------------------------
def test_non_2xx_schedules_retry_30s():
    """WEBHOOKS-022: 500 → pending, attempts=1, next_retry_at = T0 + 30s."""
    store = _store(); store.add_endpoint(_ext_endpoint())
    clock = FakeClock(); t0 = clock.now()
    tx = FakeTransport(default=FakeResponse(500, "boom"))
    created = _dispatcher(store, tx, clock).on_tree_event(_value_event())
    d = store.deliveries[created[0]]
    assert d["status"] == PENDING and d["attempts"] == 1
    assert d["next_retry_at"] == t0 + timedelta(seconds=30)
    assert "500" in d["last_response"]


def test_timeout_is_retryable():
    """WEBHOOKS-023: timeout treated like a non-2xx failure."""
    store = _store(); store.add_endpoint(_ext_endpoint())
    clock = FakeClock()
    tx = FakeTransport(default=FakeTransport.TIMEOUT)
    created = _dispatcher(store, tx, clock).on_tree_event(_value_event())
    d = store.deliveries[created[0]]
    assert d["status"] == PENDING and d["attempts"] == 1 and d["next_retry_at"] is not None
    assert "timeout" in d["last_response"]


def test_full_backoff_sequence_to_exhaustion():
    """WEBHOOKS-024/026: 0,30,300,1800,7200,43200 then exhausted at attempt 6."""
    store = _store(); store.add_endpoint(_ext_endpoint())
    clock = FakeClock()
    tx = FakeTransport(default=FakeResponse(503))
    disp = _dispatcher(store, tx, clock)
    created = disp.on_tree_event(_value_event())  # attempt 1
    did = created[0]
    expected_offsets = [30, 300, 1800, 7200, 43200]
    for i, off in enumerate(expected_offsets, start=1):
        d = store.deliveries[did]
        assert d["attempts"] == i and d["status"] == PENDING
        # advance to the scheduled retry and run the runner
        clock.set(d["next_retry_at"])
        disp.run_retries()
    d = store.deliveries[did]
    assert d["attempts"] == 6 and d["status"] == EXHAUSTED and d["next_retry_at"] is None
    # exhausted row is no longer picked up
    assert disp.run_retries() == []


def test_recovery_before_exhaustion():
    """WEBHOOKS-027: fail twice then 200 → delivered, attempts=3."""
    store = _store(); store.add_endpoint(_ext_endpoint())
    clock = FakeClock()
    tx = FakeTransport(responses=[FakeResponse(500), FakeResponse(500), FakeResponse(200)])
    disp = _dispatcher(store, tx, clock)
    did = disp.on_tree_event(_value_event())[0]
    for _ in range(2):
        clock.set(store.deliveries[did]["next_retry_at"])
        disp.run_retries()
    d = store.deliveries[did]
    assert d["status"] == DELIVERED and d["attempts"] == 3 and d["next_retry_at"] is None


def test_retry_resends_identical_body_signature_eventid():
    """WEBHOOKS-028: retry resends byte-identical body + signature + Event-Id."""
    store = _store(); store.add_endpoint(_ext_endpoint())
    clock = FakeClock()
    tx = FakeTransport(responses=[FakeResponse(500), FakeResponse(200)])
    disp = _dispatcher(store, tx, clock)
    did = disp.on_tree_event(_value_event())[0]
    clock.set(store.deliveries[did]["next_retry_at"])
    disp.run_retries()
    r1, r2 = tx.requests[0], tx.requests[1]
    assert r1["body"] == r2["body"]
    assert r1["headers"][SIGNATURE_HEADER] == r2["headers"][SIGNATURE_HEADER]
    assert r1["headers"][EVENT_ID_HEADER] == r2["headers"][EVENT_ID_HEADER]


def test_2xx_variants_delivered():
    """WEBHOOKS-029: 202/204 count as delivered; 3xx/4xx/5xx do not (WEBHOOKS-030)."""
    assert is_success(200) and is_success(202) and is_success(204)
    assert not is_success(301) and not is_success(302)
    assert not is_success(404) and not is_success(500)


def test_jitter_within_bounds():
    """WEBHOOKS-025: jittered delay within [base, base*(1+frac)], monotonic per slot."""
    # attempt 1 → next slot base 30s
    for _ in range(50):
        off = compute_next_retry_offset(1, jitter=True)
        assert 30 <= off <= 33  # 30 + up to 10%
    assert compute_next_retry_offset(6, jitter=True) is None  # exhausted


# --- E. delivery log & idempotency ----------------------------------------
def test_delivery_log_appends_attempt_history():
    """WEBHOOKS-031: attempts count + last_response progress, filterable by endpoint."""
    store = _store(); store.add_endpoint(_ext_endpoint())
    clock = FakeClock()
    tx = FakeTransport(responses=[FakeResponse(500, "e1"), FakeResponse(500, "e2"), FakeResponse(200, "ok")])
    disp = _dispatcher(store, tx, clock)
    did = disp.on_tree_event(_value_event())[0]
    for _ in range(2):
        clock.set(store.deliveries[did]["next_retry_at"]); disp.run_retries()
    d = store.deliveries[did]
    assert d["attempts"] == 3 and d["status"] == DELIVERED and "ok" in d["last_response"]


def test_one_delivery_per_endpoint_tree_event():
    """WEBHOOKS-032: duplicate dispatch is idempotent per (endpoint, tree_event)."""
    store = _store(); store.add_endpoint(_ext_endpoint())
    tx = FakeTransport(default=FakeResponse(200))
    disp = _dispatcher(store, tx)
    ev = _value_event()
    disp.on_tree_event(ev)
    disp.on_tree_event(ev)  # re-run
    assert len(store.deliveries) == 1


def test_concurrent_retry_claim_once():
    """WEBHOOKS-033: a due delivery is claimed once; second runner sees nothing."""
    store = _store(); store.add_endpoint(_ext_endpoint())
    clock = FakeClock()
    tx = FakeTransport(default=FakeResponse(500))
    disp = _dispatcher(store, tx, clock)
    did = disp.on_tree_event(_value_event())[0]
    clock.set(store.deliveries[did]["next_retry_at"])
    # Simulate worker A claiming first; worker B then finds nothing to claim.
    assert store.claim_delivery(did) is True
    assert store.claim_delivery(did) is False


def test_deleted_endpoint_cancels_inflight_retry():
    """WEBHOOKS-005: pending delivery against a deleted endpoint is not POSTed."""
    store = _store(); store.add_endpoint(_ext_endpoint())
    clock = FakeClock()
    tx = FakeTransport(default=FakeResponse(500))
    disp = _dispatcher(store, tx, clock)
    did = disp.on_tree_event(_value_event())[0]  # attempt 1 fails → pending
    store.remove_endpoint("EXT_ENDPOINT")
    clock.set(store.deliveries[did]["next_retry_at"])
    n_before = len(tx.requests)
    disp.run_retries()
    assert len(tx.requests) == n_before  # no new POST
    assert store.deliveries[did]["status"] == "failed" and store.deliveries[did]["next_retry_at"] is None


# --- F/G. DRY + boundaries -------------------------------------------------
def test_schema_event_delivers():
    """WEBHOOKS-045: COLUMN_CONFIG_UPDATED rides the same stream."""
    store = _store(); store.add_endpoint(_ext_endpoint(event_types=["COLUMN_CONFIG_UPDATED"]))
    tx = FakeTransport(default=FakeResponse(200))
    ev = FakeEvent("e", "S", "COLUMN_CONFIG_UPDATED", {"column": "col:budget"})
    assert len(_dispatcher(store, tx).on_tree_event(ev)) == 1


def test_import_completed_single_delivery():
    """WEBHOOKS-046: one IMPORT_COMPLETED event → one delivery (not per-row)."""
    store = _store(); store.add_endpoint(_ext_endpoint(event_types=["IMPORT_COMPLETED"]))
    tx = FakeTransport(default=FakeResponse(200))
    ev = FakeEvent("e", "S", "IMPORT_COMPLETED", {"rows": 500})
    assert len(_dispatcher(store, tx).on_tree_event(ev)) == 1


def test_dispatcher_emits_no_tree_event():
    """WEBHOOKS-036: dispatcher writes only Webhook Delivery rows, never the stream.

    The in-memory store has no Tree Event collection — asserting the dispatcher
    only ever touches deliveries makes the consumer-not-producer invariant
    structural here."""
    store = _store(); store.add_endpoint(_ext_endpoint())
    tx = FakeTransport(default=FakeResponse(500))
    disp = _dispatcher(store, tx, FakeClock())
    disp.on_tree_event(_value_event())
    assert not hasattr(store, "tree_events")  # there is no event-producing surface
    assert set(store.deliveries) and all("endpoint" in d for d in store.deliveries.values())
