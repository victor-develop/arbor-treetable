"""WEBHOOKS retry / backoff / exhaustion + delivery log — runnable bench-free.

Uses the loopback :class:`LocalHTTPReceiver` for the real POST path, a freezable
``FakeClock``, and the dispatcher's on-demand ``run_retries`` (no real sleeps).
Jitter is OFF so ``next_retry_at`` is exact; a separate unit asserts the jitter
band. Backoff offsets are the core schedule (0s, 30s, 5m, 30m, 2h, 12h).

Maps WEBHOOKS-021..034.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from arbor.core.backoff import RETRY_SCHEDULE_SECONDS
from arbor.core.types import Actor
from arbor.arbor.dispatch.webhook import (
    DELIVERED,
    EVENT_ID_HEADER,
    EXHAUSTED,
    PENDING,
    SIGNATURE_HEADER,
    WebhookDispatcher,
    compute_next_retry_offset,
    is_success,
)

from tests.fixtures.canonical import C
from tests.webhooks.harness import HANG, LocalHTTPReceiver, endpoint, make_world

SECRET = "ext-secret-retry"
# Offsets from the FIRST attempt to attempts 2..6.
RETRY_OFFSETS = list(RETRY_SCHEDULE_SECONDS[1:])  # [30, 300, 1800, 7200, 43200]


@pytest.fixture
def receiver():
    rcv = LocalHTTPReceiver(hang_seconds=2.0)
    try:
        yield rcv
    finally:
        rcv.shutdown()


def _world(receiver, *, timeout=5.0):
    w = make_world(receiver=receiver)
    # Re-bind the dispatcher with a tighter per-request timeout for the HANG test.
    w.dispatcher = WebhookDispatcher(w.store, w.transport, w.clock, timeout=timeout, jitter=False)
    w.store.add_endpoint(endpoint(url=receiver.url, secret=SECRET, event_types=["NODE_VALUE_UPDATED"]))
    return w


def _fire(w, value=42):
    return w.execute(
        "updateCell",
        {"sheet": w.fx.sheet, "node": w.fx.X, "column": w.fx.col_budget, "value": value},
        Actor(C),
    )


# --- success path ----------------------------------------------------------
def test_2xx_delivered_first_attempt(receiver):
    """WEBHOOKS-021: 200 → delivered, attempts=1, next_retry_at cleared."""
    w = _world(receiver)
    receiver.set_default(200)
    _fire(w)
    d = w.deliveries()[0]
    assert d["status"] == DELIVERED
    assert d["attempts"] == 1
    assert d["next_retry_at"] is None
    assert "200" in d["last_response"]


# --- failure scheduling ----------------------------------------------------
def test_non_2xx_schedules_retry_at_30s(receiver):
    """WEBHOOKS-022: 500 → pending, attempts=1, next_retry_at = T0 + 30s."""
    w = _world(receiver)
    receiver.set_default((500, "boom"))
    t0 = w.clock.now()
    _fire(w)
    d = w.deliveries()[0]
    assert d["status"] == PENDING and d["attempts"] == 1
    assert d["next_retry_at"] == t0 + timedelta(seconds=30)
    assert "500" in d["last_response"]


def test_timeout_is_retryable(receiver):
    """WEBHOOKS-023: a hung receiver → client timeout → retryable failure, just
    like a non-2xx (real socket timeout path)."""
    w = _world(receiver, timeout=0.3)
    receiver.set_default(HANG)
    _fire(w)
    d = w.deliveries()[0]
    assert d["status"] == PENDING and d["attempts"] == 1 and d["next_retry_at"] is not None
    assert "timeout" in d["last_response"]


def test_backoff_schedule_across_all_slots_to_exhaustion(receiver):
    """WEBHOOKS-024/026: successive next_retry_at deltas follow 30s,5m,30m,2h,12h;
    attempts climb 1→6; the 6th failure marks exhausted; runner stops."""
    w = _world(receiver)
    receiver.set_default(503)
    _fire(w)  # attempt 1
    did = w.deliveries()[0]["name"]

    for attempt_no, off in enumerate(RETRY_OFFSETS, start=1):
        d = w.store.deliveries[did]
        assert d["attempts"] == attempt_no and d["status"] == PENDING
        # delta from this attempt's completion to the scheduled retry matches slot
        assert d["next_retry_at"] == w.clock.now() + timedelta(seconds=off)
        w.clock.set(d["next_retry_at"])
        w.dispatcher.run_retries()

    d = w.store.deliveries[did]
    assert d["attempts"] == 6 and d["status"] == EXHAUSTED and d["next_retry_at"] is None
    assert w.dispatcher.run_retries() == []  # exhausted row never picked up again


def test_offset_deltas_match_schedule_exactly(receiver):
    """WEBHOOKS-024 (delta form): assert each scheduled next_retry_at sits exactly
    one slot-delay past the clock at the time the attempt completed."""
    w = _world(receiver)
    receiver.set_default(500)
    _fire(w)
    did = w.deliveries()[0]["name"]
    for off in RETRY_OFFSETS:
        completed_at = w.clock.now()
        d = w.store.deliveries[did]
        assert d["next_retry_at"] == completed_at + timedelta(seconds=off)
        w.clock.set(d["next_retry_at"])
        w.dispatcher.run_retries()


def test_recovery_before_exhaustion(receiver):
    """WEBHOOKS-027: fail twice then 200 → delivered, attempts=3, no 4th attempt."""
    w = _world(receiver)
    receiver.queue([500, 500, 200])
    _fire(w)
    did = w.deliveries()[0]["name"]
    for _ in range(2):
        w.clock.set(w.store.deliveries[did]["next_retry_at"])
        w.dispatcher.run_retries()
    d = w.store.deliveries[did]
    assert d["status"] == DELIVERED and d["attempts"] == 3 and d["next_retry_at"] is None
    assert w.dispatcher.run_retries() == []


def test_retry_resends_identical_body_signature_event_id(receiver):
    """WEBHOOKS-028: the retry POST is byte-identical (body + signature +
    Event-Id) to attempt 1 — signed once over the original event body."""
    w = _world(receiver)
    receiver.queue([500, 200])
    _fire(w)
    did = w.deliveries()[0]["name"]
    w.clock.set(w.store.deliveries[did]["next_retry_at"])
    w.dispatcher.run_retries()
    r1, r2 = receiver.requests[0], receiver.requests[1]
    assert r1.body == r2.body
    assert r1.headers[SIGNATURE_HEADER.lower()] == r2.headers[SIGNATURE_HEADER.lower()]
    assert r1.headers[EVENT_ID_HEADER.lower()] == r2.headers[EVENT_ID_HEADER.lower()]


# --- classification boundaries (unit) -------------------------------------
def test_2xx_variants_are_delivered():
    """WEBHOOKS-029: 200/202/204 are success."""
    assert is_success(200) and is_success(202) and is_success(204) and is_success(299)


def test_3xx_4xx_5xx_are_failures():
    """WEBHOOKS-030: 3xx (not auto-followed), 4xx, 5xx all reschedule."""
    assert not is_success(301) and not is_success(302)
    assert not is_success(400) and not is_success(404) and not is_success(500)


def test_redirect_is_not_followed(receiver):
    """WEBHOOKS-030: a 302 is surfaced as a non-2xx failure, NOT silently followed
    to an unverified location (real urllib path with redirect handler disabled)."""
    w = _world(receiver)
    receiver.set_default((302, ""))
    _fire(w)
    d = w.deliveries()[0]
    assert d["status"] == PENDING  # treated as failure, scheduled for retry
    assert "302" in d["last_response"]


def test_jitter_band_is_bounded_and_monotonic():
    """WEBHOOKS-025: with jitter the delay stays within [base, base*1.1], never
    below the slot base, and exhausts past the schedule."""
    for _ in range(100):
        off = compute_next_retry_offset(1, jitter=True)  # next slot = 30s
        assert 30 <= off <= 33
        off4 = compute_next_retry_offset(4, jitter=True)  # next slot = 2h = 7200
        assert 7200 <= off4 <= 7920
    assert compute_next_retry_offset(6, jitter=True) is None  # exhausted


# --- E. delivery log & idempotency ----------------------------------------
def test_delivery_log_tracks_attempt_history(receiver):
    """WEBHOOKS-031: attempts count + last_response progress to the final status,
    filterable by endpoint."""
    w = _world(receiver)
    receiver.queue([(500, "e1"), (500, "e2"), (200, "ok")])
    _fire(w)
    did = w.deliveries()[0]["name"]
    for _ in range(2):
        w.clock.set(w.store.deliveries[did]["next_retry_at"])
        w.dispatcher.run_retries()
    d = w.store.deliveries[did]
    assert d["attempts"] == 3 and d["status"] == DELIVERED and "ok" in d["last_response"]
    # filterable by endpoint
    assert [x["name"] for x in w.deliveries_for("EXT_ENDPOINT")] == [did]


def test_one_delivery_per_endpoint_tree_event_idempotent(receiver):
    """WEBHOOKS-032: re-dispatching the SAME emitted event creates no duplicate
    delivery; the existing row's retry state is reused."""
    w = _world(receiver)
    receiver.set_default(500)
    _fire(w)
    assert len(w.deliveries()) == 1
    # Re-feed the same emitted Tree Event (simulates at-least-once redelivery).
    from tests.webhooks.harness import EventBridge

    w.dispatcher.on_tree_event(EventBridge.of(w.sink.last()))
    assert len(w.deliveries()) == 1  # still one row for (endpoint, tree_event)


def test_concurrent_retry_runners_claim_once(receiver):
    """WEBHOOKS-033: a due delivery is claimed once; the second worker can't claim
    it, so it is not double-POSTed."""
    w = _world(receiver)
    receiver.set_default(500)
    _fire(w)
    did = w.deliveries()[0]["name"]
    w.clock.set(w.store.deliveries[did]["next_retry_at"])
    assert w.store.claim_delivery(did) is True  # worker A
    assert w.store.claim_delivery(did) is False  # worker B finds it claimed


def test_stable_event_id_across_redeliveries(receiver):
    """WEBHOOKS-034: the append-only Tree Event id (X-Arbor-Event-Id / payload
    event_id) is identical on every attempt; the underlying event is never
    mutated."""
    w = _world(receiver)
    receiver.queue([500, 500, 200])
    _fire(w)
    did = w.deliveries()[0]["name"]
    for _ in range(2):
        w.clock.set(w.store.deliveries[did]["next_retry_at"])
        w.dispatcher.run_retries()
    event_ids = {r.event_id for r in receiver.requests}
    assert len(event_ids) == 1  # one stable id across all 3 attempts
    assert next(iter(event_ids)) == w.store.deliveries[did]["tree_event"]
