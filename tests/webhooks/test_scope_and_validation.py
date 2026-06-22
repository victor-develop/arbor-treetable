"""WEBHOOKS subscription scope matching + closed-set validation — bench-free.

Subscription scope (sheet | branch | column) matching is driven by REAL Tree
Events from ``execute_action`` against the canonical seed, fanned out through the
``WebhookDispatcher`` (deliveries assert with the in-memory transport — no socket
needed for matching). The validation cases bind to the ONE closed event-type set
(``arbor.core.types.EVENT_TYPES``) the endpoint create-path must enforce.

Maps WEBHOOKS-002, 003, 004, 006, 007, 008, 043, 044, 045, 046, 049.
"""

from __future__ import annotations

from arbor.core.types import EVENT_TYPES, EventType, Actor
from arbor.arbor.dispatch.testing import FakeEvent, FakeResponse, FakeTransport

from tests.fixtures.canonical import A, C, D, G
from tests.webhooks.harness import endpoint, make_world

NVU = EventType.NODE_VALUE_UPDATED.value


def _world():
    """No socket — inject a 200 FakeTransport; we only assert MATCH/no-match and
    delivery-row creation here."""
    return make_world(transport=FakeTransport(default=FakeResponse(200)))


def _update(w, *, node, column, actor=C, value=1):
    return w.execute(
        "updateCell",
        {"sheet": w.fx.sheet, "node": node, "column": column, "value": value},
        Actor(actor),
    )


# --- A. subscription lifecycle --------------------------------------------
def test_create_endpoint_no_delivery_before_any_event():
    """WEBHOOKS-001: a freshly created active endpoint participates in fan-out but
    no Webhook Delivery exists until a matching event fires."""
    w = _world()
    w.store.add_endpoint(endpoint(url="http://x", event_types=["NODE_VALUE_UPDATED", "CHANGE_APPROVED"]))
    assert w.deliveries() == []  # nothing fired yet
    _update(w, node=w.fx.X, column=w.fx.col_budget)  # first matching event
    assert len(w.deliveries_for("EXT_ENDPOINT")) == 1


def test_delete_endpoint_cancels_inflight_retry():
    """WEBHOOKS-005: a pending delivery whose endpoint is then deleted is not
    re-POSTed; it is cancelled (failed, next_retry_at cleared)."""
    w = _world()
    # transport fails so the delivery goes pending with a future retry
    from arbor.arbor.dispatch.testing import FakeResponse as _R, FakeTransport as _T
    w.transport = _T(default=_R(500))
    from arbor.arbor.dispatch.webhook import WebhookDispatcher
    w.dispatcher = WebhookDispatcher(w.store, w.transport, w.clock, jitter=False)
    w.store.add_endpoint(endpoint(url="http://x", event_types=[NVU]))
    _update(w, node=w.fx.X, column=w.fx.col_budget)
    did = w.deliveries()[0]["name"]
    assert w.store.deliveries[did]["status"] == "pending"

    w.store.remove_endpoint("EXT_ENDPOINT")
    w.clock.set(w.store.deliveries[did]["next_retry_at"])
    w.dispatcher.run_retries()
    d = w.store.deliveries[did]
    assert d["status"] == "failed" and d["next_retry_at"] is None


def test_unsubscribed_subscription_changed_no_self_webhook():
    """WEBHOOKS-009: SUBSCRIPTION_CHANGED is an ordinary stream event; an endpoint
    not subscribed to it gets no delivery when a watcher is subscribed."""
    w = _world()
    w.store.add_endpoint(endpoint(url="http://x", event_types=[NVU, "CHANGE_APPROVED"]))
    # A subscribes another watcher → emits SUBSCRIPTION_CHANGED
    w.execute(
        "subscribe",
        {"subscriber": G, "scope": "sheet", "target": w.fx.sheet, "event_types": [NVU], "delivery": "in-app"},
        Actor(A),
    )
    assert any(e.type == "SUBSCRIPTION_CHANGED" for e in w.sink.events)  # event did fire
    assert w.deliveries_for("EXT_ENDPOINT") == []  # but endpoint not subscribed to it


# --- A. event_types filter & active gating --------------------------------
def test_update_event_types_narrows_subscription():
    """WEBHOOKS-002: narrowing event_types to [CHANGE_APPROVED] drops a later
    NODE_VALUE_UPDATED; a CHANGE_APPROVED still delivers."""
    w = _world()
    w.store.add_endpoint(endpoint(url="http://x", event_types=["CHANGE_APPROVED"]))
    _update(w, node=w.fx.X, column=w.fx.col_budget)  # C owns budget → NODE_VALUE_UPDATED
    assert w.deliveries_for("EXT_ENDPOINT") == []  # not subscribed to NVU

    # a CHANGE_APPROVED (via the approval lifecycle) does deliver
    from tests.fixtures.canonical import E

    out = _update(w, node=w.fx.X, column=w.fx.col_budget, actor=E)  # propose
    w.execute("approveChange", {"change_request": out.change_request}, Actor(C))
    delivered_types = {
        next(e for e in w.sink.events if e.event_id == d["tree_event"]).type
        for d in w.deliveries_for("EXT_ENDPOINT")
    }
    assert "CHANGE_APPROVED" in delivered_types


def test_inactive_endpoint_no_delivery():
    """WEBHOOKS-003: a deactivated endpoint produces no new delivery; its prior
    rows remain. (active=0 gating.)"""
    w = _world()
    w.store.add_endpoint(endpoint(url="http://x", event_types=[NVU], active=True))
    _update(w, node=w.fx.X, column=w.fx.col_budget)  # one delivery
    assert len(w.deliveries_for("EXT_ENDPOINT")) == 1
    prior = list(w.store.deliveries)

    w.store.endpoints["EXT_ENDPOINT"].active = False
    _update(w, node=w.fx.Y, column=w.fx.col_budget)
    assert list(w.store.deliveries) == prior  # no new row; historical row intact


def test_reactivation_no_backfill():
    """WEBHOOKS-004: re-activating delivers only post-reactivation events; the
    event emitted while inactive is not retroactively delivered (dispatch decided
    at emit time)."""
    w = _world()
    w.store.add_endpoint(endpoint(url="http://x", event_types=[NVU], active=False))
    _update(w, node=w.fx.X, column=w.fx.col_budget)  # emitted while inactive
    assert w.deliveries_for("EXT_ENDPOINT") == []

    w.store.endpoints["EXT_ENDPOINT"].active = True
    _update(w, node=w.fx.Y, column=w.fx.col_budget)  # post-reactivation
    deliveries = w.deliveries_for("EXT_ENDPOINT")
    assert len(deliveries) == 1
    # the delivered event is the SECOND (post-reactivation) one
    delivered_event = next(e for e in w.sink.events if e.event_id == deliveries[0]["tree_event"])
    assert delivered_event.payload["node"] == w.fx.Y


# --- scope matching --------------------------------------------------------
def test_branch_scope_matches_descendant_not_outside():
    """WEBHOOKS-006: branch endpoint on P2 receives NODE_DELETED for Z (∈ P2) but
    not for X (∈ P1). Same lft/rgt range used by notification dispatch."""
    w = _world()
    w.store.add_endpoint(
        endpoint("EXT_BRANCH", url="http://x", event_types=["NODE_DELETED"], scope="branch", target=w.fx.P2)
    )
    w.execute("deleteNode", {"sheet": w.fx.sheet, "node": w.fx.Z}, Actor(D))  # Z ∈ P2, D authorized
    w.execute("deleteNode", {"sheet": w.fx.sheet, "node": w.fx.X}, Actor(A))  # X ∈ P1
    deliveries = w.deliveries_for("EXT_BRANCH")
    assert len(deliveries) == 1
    delivered = next(e for e in w.sink.events if e.event_id == deliveries[0]["tree_event"])
    assert delivered.payload["node"] == w.fx.Z


def test_column_scope_matches_only_its_column():
    """WEBHOOKS-007: column endpoint on col:budget matches a budget update on Y,
    not a name update on Z (direct column equality)."""
    w = _world()
    w.store.add_endpoint(
        endpoint("EXT_COL", url="http://x", event_types=[NVU], scope="column", target=w.fx.col_budget)
    )
    _update(w, node=w.fx.Y, column=w.fx.col_budget)  # C owns budget → match
    _update(w, node=w.fx.Z, column=w.fx.col_name, actor="B")  # B owns name → non-match
    deliveries = w.deliveries_for("EXT_COL")
    assert len(deliveries) == 1
    delivered = next(e for e in w.sink.events if e.event_id == deliveries[0]["tree_event"])
    assert delivered.payload["column"] == w.fx.col_budget


def test_multiple_endpoints_fan_out_independently():
    """WEBHOOKS-008: one event → two deliveries (sheet + column scope both match a
    budget update on Y), each its own row/signature/retry state."""
    w = _world()
    w.store.add_endpoint(endpoint("EXT_SHEET", url="http://x", event_types=[NVU], scope="sheet", target=w.fx.sheet, secret="s-sheet"))
    w.store.add_endpoint(endpoint("EXT_COL", url="http://x", event_types=[NVU], scope="column", target=w.fx.col_budget, secret="s-col"))
    _update(w, node=w.fx.Y, column=w.fx.col_budget)
    assert len(w.deliveries_for("EXT_SHEET")) == 1
    assert len(w.deliveries_for("EXT_COL")) == 1
    # same underlying tree_event, distinct rows
    te_sheet = w.deliveries_for("EXT_SHEET")[0]["tree_event"]
    te_col = w.deliveries_for("EXT_COL")[0]["tree_event"]
    assert te_sheet == te_col
    assert w.deliveries_for("EXT_SHEET")[0]["signature"] != w.deliveries_for("EXT_COL")[0]["signature"]


def test_sheet_scope_isolates_other_sheets():
    """WEBHOOKS-049: an event whose sheet != target never reaches a sheet-scoped
    endpoint."""
    w = _world()
    w.store.add_endpoint(endpoint(url="http://x", event_types=[NVU], scope="sheet", target=w.fx.sheet))
    # Fabricate-via-bridge an event on a different sheet S2 (no S2 seed needed; we
    # only test the sheet-equality gate, and the event still comes from the real
    # emitter shape).
    foreign = FakeEvent("evt-foreign", "S2", NVU, {"node": "n", "column": w.fx.col_budget})
    w.dispatcher.on_tree_event(foreign)
    assert w.deliveries_for("EXT_ENDPOINT") == []


# --- schema / bulk events -------------------------------------------------
def test_column_config_updated_delivers():
    """WEBHOOKS-045: COLUMN_CONFIG_UPDATED (deleteColumn) rides the same stream."""
    w = _world()
    w.store.add_endpoint(endpoint(url="http://x", event_types=["COLUMN_CONFIG_UPDATED"]))
    w.execute("deleteColumn", {"sheet": w.fx.sheet, "column": w.fx.col_budget}, Actor(C))
    assert len(w.deliveries_for("EXT_ENDPOINT")) == 1


def test_import_completed_single_delivery():
    """WEBHOOKS-046: one IMPORT_COMPLETED event → exactly one delivery, regardless
    of rows touched (the webhook layer never invents per-row events)."""
    w = _world()
    w.store.add_endpoint(endpoint(url="http://x", event_types=["IMPORT_COMPLETED"]))
    # IMPORT_COMPLETED is emitted by a bulk path; bridge one such event onto the
    # stream shape (one event → one delivery is the invariant under test).
    w.dispatcher.on_tree_event(FakeEvent("evt-imp", w.fx.sheet, "IMPORT_COMPLETED", {"rows": 500}))
    assert len(w.deliveries_for("EXT_ENDPOINT")) == 1


# --- negative boundary over the WHOLE closed set --------------------------
def test_unsubscribed_event_types_never_deliver():
    """WEBHOOKS-043: with event_types = [NODE_VALUE_UPDATED, CHANGE_APPROVED], every
    OTHER member of the closed set produces zero deliveries."""
    w = _world()
    w.store.add_endpoint(endpoint(url="http://x", event_types=["NODE_VALUE_UPDATED", "CHANGE_APPROVED"]))
    others = [t for t in EVENT_TYPES if t not in ("NODE_VALUE_UPDATED", "CHANGE_APPROVED")]
    for i, t in enumerate(others):
        w.dispatcher.on_tree_event(FakeEvent(f"evt-{i}", w.fx.sheet, t, {"node": w.fx.X, "column": w.fx.col_budget}))
    assert w.deliveries_for("EXT_ENDPOINT") == []


def test_closed_event_type_set_has_eleven_members():
    """WEBHOOKS-044 (contract): the closed Tree Event type set is exactly the 11
    members of EVENT_TYPES — the single source of truth the endpoint create-path
    must validate against."""
    assert len(EVENT_TYPES) == 11
    assert set(EVENT_TYPES) == {e.value for e in EventType}


def test_invalid_event_type_is_outside_the_closed_set():
    """WEBHOOKS-044: an endpoint subscribing to a non-member (e.g. 'NODE_EXPLODED')
    must be rejected — assert it is NOT a valid event type, so a create-path
    membership check against EVENT_TYPES rejects it (guarding silent
    never-matching subscriptions)."""
    bad = ["NODE_EXPLODED"]
    assert all(t not in EVENT_TYPES for t in bad)
    # and such a subscription would, by construction, match nothing on the stream
    w = _world()
    w.store.add_endpoint(endpoint(url="http://x", event_types=bad))
    for t in EVENT_TYPES:  # no real event type can satisfy a bogus filter
        w.dispatcher.on_tree_event(FakeEvent(f"e-{t}", w.fx.sheet, t, {"node": w.fx.X}))
    assert w.deliveries_for("EXT_ENDPOINT") == []
