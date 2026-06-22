"""WEBHOOKS DRY + permission-derived + parity (the one-stream invariant) —
runnable bench-free.

This is the lane's keystone: it drives REAL ``execute_action`` so genuine Tree
Events hit the emitter, then proves webhooks are a PURE CONSUMER of that ONE
append-only stream — co-derived with notifications through the SAME shared matcher,
emitting no event of their own, and observing the SAME event regardless of which
surface/actor caused it. Webhook subscription confers ZERO mutate authority.

Maps WEBHOOKS-013, 035, 036, 037, 038, 039, 040, 041, 042, 047, 048, 050.
"""

from __future__ import annotations

from arbor.core.types import Actor, ActorType
from arbor.arbor.dispatch.matcher import selector_matches
from arbor.arbor.dispatch.notify import NotificationDispatcher
from arbor.arbor.dispatch.testing import (
    FakeClock,
    FakeEndpoint,
    FakeEvent,
    FakeResponse,
    FakeSubscription,
    FakeTransport,
    InMemoryNotificationStore,
)

from tests.fixtures.canonical import A, C, D, E, G
from tests.webhooks.harness import EventBridge, endpoint, make_world

NVU = "NODE_VALUE_UPDATED"


def _world():
    return make_world(transport=FakeTransport(default=FakeResponse(200)))


def _delivered_event(w, delivery):
    return next(e for e in w.sink.events if e.event_id == delivery["tree_event"])


# --- F. one stream feeds both consumers -----------------------------------
def test_webhook_and_notification_off_one_tree_event():
    """WEBHOOKS-035: a single mutation → ONE Tree Event → both a Notification (for
    G) and a Webhook Delivery (for EXT), both referencing that one event id. No
    second event is emitted for the webhook path."""
    w = _world()
    # webhook endpoint (sheet scope) on NODE_VALUE_UPDATED
    w.store.add_endpoint(endpoint(url="http://x", event_types=[NVU], scope="sheet", target=w.fx.sheet))
    # G's in-app subscription on the same event family/scope
    nstore = InMemoryNotificationStore()
    for n in w.repo.nodes.values():
        nstore.set_node_range(n.name, n.lft, n.rgt)
    nstore.add_subscription(
        FakeSubscription("sub-G", G, scope="sheet", target=w.fx.sheet, event_types=[NVU], delivery="in-app")
    )
    ndisp = NotificationDispatcher(nstore, FakeClock())

    before = len(w.sink.events)
    out = w.execute(
        "updateCell", {"sheet": w.fx.sheet, "node": w.fx.X, "column": w.fx.col_budget, "value": 42}, Actor(C)
    )
    new_events = w.sink.events[before:]
    assert out.kind == "executed" and len(new_events) == 1  # exactly one Tree Event
    event = new_events[0]
    # feed the SAME event to the notification dispatcher
    notif_ids = ndisp.on_tree_event(EventBridge.of(event))

    deliveries = w.deliveries_for("EXT_ENDPOINT")
    assert len(deliveries) == 1 and len(notif_ids) == 1
    # both reference that one event id
    assert deliveries[0]["tree_event"] == event.event_id
    assert nstore.notifications[notif_ids[0]]["tree_event"] == event.event_id


def test_dispatcher_emits_no_tree_event_of_its_own():
    """WEBHOOKS-036: dispatch/delivery/retry never grows the Tree Event stream —
    the dispatcher is a pure consumer. Delivery state lives only in Webhook
    Delivery rows."""
    w = make_world(transport=FakeTransport(default=FakeResponse(500)))
    w.store.add_endpoint(endpoint(url="http://x", event_types=[NVU]))
    before = len(w.sink.events)
    w.execute("updateCell", {"sheet": w.fx.sheet, "node": w.fx.X, "column": w.fx.col_budget, "value": 1}, Actor(C))
    after_emit = len(w.sink.events)
    assert after_emit - before == 1  # only the mutation event
    # retries (which fail) must not add events
    w.clock.set(w.store.deliveries[w.deliveries()[0]["name"]]["next_retry_at"])
    w.dispatcher.run_retries()
    assert len(w.sink.events) == after_emit  # unchanged by delivery/retry


def test_shared_matcher_governs_both_consumers():
    """WEBHOOKS-037: the SAME selector_matches predicate, given identical
    scope=branch/target=P2/event_types, agrees for a node inside P2 and rejects one
    outside — proving one NestedSet-range matcher serves webhook + notification."""
    ranges = {"P2": (6, 11), "Z": (9, 10), "X": (3, 4)}
    node_range = ranges.get
    sub = FakeSubscription("s", G, scope="branch", target="P2", event_types=["NODE_DELETED"], delivery="in-app")
    ep = FakeEndpoint("e", "http://x", "sec", ["NODE_DELETED"], scope="branch", target="P2")
    inside = FakeEvent("e1", "S", "NODE_DELETED", {"node": "Z"})
    outside = FakeEvent("e2", "S", "NODE_DELETED", {"node": "X"})
    # both selectors agree
    assert selector_matches(sub, inside, node_range) is True
    assert selector_matches(ep, inside, node_range) is True
    assert selector_matches(sub, outside, node_range) is False
    assert selector_matches(ep, outside, node_range) is False


def test_change_approved_lifecycle_two_deliveries_share_stream():
    """WEBHOOKS-013: approveChange replays the handler → NODE_VALUE_UPDATED then
    CHANGE_APPROVED, both observed by webhooks in emission order; CHANGE_APPROVED
    links the CR."""
    w = _world()
    w.store.add_endpoint(endpoint(url="http://x", event_types=[NVU, "CHANGE_APPROVED"]))
    out = w.execute(
        "updateCell", {"sheet": w.fx.sheet, "node": w.fx.X, "column": w.fx.col_budget, "value": 77}, Actor(E)
    )  # propose
    cr = out.change_request
    w.execute("approveChange", {"change_request": cr}, Actor(C))
    types = [_delivered_event(w, d).type for d in w.deliveries_for("EXT_ENDPOINT")]
    assert types == [NVU, "CHANGE_APPROVED"]
    approved = next(d for d in w.deliveries_for("EXT_ENDPOINT") if _delivered_event(w, d).type == "CHANGE_APPROVED")
    assert _delivered_event(w, approved).change_request == cr


# --- G. permission-derived / boundary -------------------------------------
def test_webhook_subscription_confers_no_mutate_authority():
    """WEBHOOKS-039: EXT owning an endpoint has ZERO edit authority — its
    updateCell on a column it doesn't own routes to a CR + CHANGE_PROPOSED. If
    subscribed to CHANGE_PROPOSED, EXT receives the delivery for the CR it caused.
    Subscription and authority are orthogonal."""
    w = _world()
    w.store.add_endpoint(endpoint(url="http://x", event_types=["CHANGE_PROPOSED"]))
    out = w.execute(
        "updateCell", {"sheet": w.fx.sheet, "node": w.fx.X, "column": w.fx.col_budget, "value": 5},
        Actor("EXT"),
    )
    assert out.kind == "suggested"  # NOT authorized despite owning the endpoint
    deliveries = w.deliveries_for("EXT_ENDPOINT")
    assert len(deliveries) == 1
    assert _delivered_event(w, deliveries[0]).type == "CHANGE_PROPOSED"
    assert _delivered_event(w, deliveries[0]).change_request == out.change_request


def test_delegation_changed_delivers_when_subscribed():
    """WEBHOOKS-040: delegateBranch emits DELEGATION_CHANGED, delivered when
    subscribed; payload identifies branch_root + grantee."""
    w = _world()
    w.store.add_endpoint(endpoint(url="http://x", event_types=["DELEGATION_CHANGED"]))
    w.execute("delegateBranch", {"sheet": w.fx.sheet, "branch_root": w.fx.P1, "grantee": D}, Actor(A))
    deliveries = w.deliveries_for("EXT_ENDPOINT")
    assert len(deliveries) == 1
    ev = _delivered_event(w, deliveries[0])
    assert ev.type == "DELEGATION_CHANGED"
    assert ev.payload["branch_root"] == w.fx.P1 and ev.payload["grantee"] == D


def test_branch_scope_follows_subtree_not_delegate_identity():
    """WEBHOOKS-041: D's addNode under Y (∈ P2) → NODE_CREATED matches the
    P2-scoped endpoint by SUBTREE membership of the new node, independent of who
    acted (D). A sibling add under P1 would not match."""
    w = _world()
    w.store.add_endpoint(
        endpoint("EXT_BRANCH", url="http://x", event_types=["NODE_CREATED"], scope="branch", target=w.fx.P2)
    )
    w.execute("addNode", {"sheet": w.fx.sheet, "parent": w.fx.Y}, Actor(D))  # inside P2
    w.execute("addNode", {"sheet": w.fx.sheet, "parent": w.fx.P1}, Actor(A))  # outside P2 (under P1)
    deliveries = w.deliveries_for("EXT_BRANCH")
    assert len(deliveries) == 1
    assert _delivered_event(w, deliveries[0]).payload["parent"] == w.fx.Y


def test_move_into_branch_delivers_on_post_move_position():
    """WEBHOOKS-042: a moveNode whose destination is inside P2 (X → Y) is approved
    and emits NODE_MOVED; the P2-scoped endpoint matches because the node now
    resides within P2's range at emit time. (Dual-end CR: approver D + co-approver
    A both approve.)"""
    w = _world()
    w.store.add_endpoint(
        endpoint("EXT_BRANCH", url="http://x", event_types=["NODE_MOVED"], scope="branch", target=w.fx.P2)
    )
    out = w.execute("moveNode", {"sheet": w.fx.sheet, "node": w.fx.X, "new_parent": w.fx.Y}, Actor(A))
    cr = out.change_request
    crd = w.repo.get_change_request(cr)
    # collect ALL required approvals (resolved_approver D + co-approver A)
    w.execute("approveChange", {"change_request": cr}, Actor(crd["resolved_approver"]))
    for co in crd["payload"].get("co_approvers") or []:
        w.execute("approveChange", {"change_request": cr}, Actor(co))
    deliveries = w.deliveries_for("EXT_BRANCH")
    assert len(deliveries) == 1
    ev = _delivered_event(w, deliveries[0])
    assert ev.type == "NODE_MOVED" and ev.payload["node"] == w.fx.X


def test_reject_and_withdraw_both_surface_as_change_rejected():
    """WEBHOOKS-047: rejectChange and withdrawChange BOTH emit CHANGE_REJECTED; the
    payload/CR status distinguishes reject vs withdraw (reason='withdrawn')."""
    # (a) reject
    w = _world()
    w.store.add_endpoint(endpoint(url="http://x", event_types=["CHANGE_REJECTED"]))
    out = w.execute(
        "updateCell", {"sheet": w.fx.sheet, "node": w.fx.X, "column": w.fx.col_budget, "value": 1}, Actor(E)
    )
    w.execute("rejectChange", {"change_request": out.change_request}, Actor(C))
    rejected = w.deliveries_for("EXT_ENDPOINT")
    assert len(rejected) == 1
    rej_ev = _delivered_event(w, rejected[0])
    assert rej_ev.type == "CHANGE_REJECTED" and "reason" not in rej_ev.payload

    # (b) withdraw — separate world
    w2 = _world()
    w2.store.add_endpoint(endpoint(url="http://x", event_types=["CHANGE_REJECTED"]))
    out2 = w2.execute(
        "updateCell", {"sheet": w2.fx.sheet, "node": w2.fx.X, "column": w2.fx.col_budget, "value": 2}, Actor(E)
    )
    w2.execute("withdrawChange", {"change_request": out2.change_request}, Actor(E))  # requester
    withdrawn = w2.deliveries_for("EXT_ENDPOINT")
    assert len(withdrawn) == 1
    wd_ev = _delivered_event(w2, withdrawn[0])
    assert wd_ev.type == "CHANGE_REJECTED" and wd_ev.payload["reason"] == "withdrawn"


def test_owner_self_policy_produces_same_webhook_sequence():
    """WEBHOOKS-048: with owners_must_use_change_requests, even owner C's updateCell
    yields CHANGE_PROPOSED, then on self-approval NODE_VALUE_UPDATED + CHANGE_APPROVED
    — identical webhook behavior to a non-owner CR; no special-case."""
    w = make_world(
        settings={"owners_must_use_change_requests": True},
        transport=FakeTransport(default=FakeResponse(200)),
    )
    w.store.add_endpoint(
        endpoint(url="http://x", event_types=["CHANGE_PROPOSED", "CHANGE_APPROVED", NVU])
    )
    out = w.execute(
        "updateCell", {"sheet": w.fx.sheet, "node": w.fx.X, "column": w.fx.col_budget, "value": 8}, Actor(C)
    )
    assert out.kind == "suggested"  # owner-self forced to CR
    w.execute("approveChange", {"change_request": out.change_request}, Actor(C))  # self-approve
    types = [_delivered_event(w, d).type for d in w.deliveries_for("EXT_ENDPOINT")]
    assert types == ["CHANGE_PROPOSED", NVU, "CHANGE_APPROVED"]


def test_subscription_delivery_webhook_channel_bridges_to_dispatcher():
    """WEBHOOKS-038: a Subscription with delivery='webhook' yields a Notification
    with channel='webhook' — the bridge between the notification ledger and the
    webhook delivery mechanism, both off the one stream."""
    w = _world()
    nstore = InMemoryNotificationStore()
    for n in w.repo.nodes.values():
        nstore.set_node_range(n.name, n.lft, n.rgt)
    nstore.add_subscription(
        FakeSubscription("sub-wh", "EXT", scope="sheet", target=w.fx.sheet, event_types=[NVU], delivery="webhook")
    )
    ndisp = NotificationDispatcher(nstore, FakeClock())
    out = w.execute(
        "updateCell", {"sheet": w.fx.sheet, "node": w.fx.X, "column": w.fx.col_budget, "value": 3}, Actor(C)
    )
    notif_ids = ndisp.on_tree_event(EventBridge.of(w.sink.last()))
    assert len(notif_ids) == 1
    assert nstore.notifications[notif_ids[0]]["channel"] == "webhook"


def test_surface_parity_identical_payload_shape_across_surfaces():
    """WEBHOOKS-050: the SAME authorized updateCell driven three ways (web/REST/
    agent) yields structurally identical webhook payloads except for actor/
    actor_type and event_id/timestamp. The webhook surface can't tell the origin
    beyond actor_type. (All three funnel through the ONE execute_action; we vary
    only the Actor/actor_type to model the surface.)"""
    surfaces = [
        ("B", ActorType.HUMAN),    # web executeAction, B is editor on col:status
        ("B", ActorType.HUMAN),    # REST POST — same actor, same path
        ("AGENT", ActorType.AGENT),  # agent tool — its own user, granted editor
    ]
    payloads = []
    for user, atype in surfaces:
        w = _world()
        w.store.add_endpoint(endpoint(url="http://x", event_types=[NVU]))
        if user == "AGENT":
            w.repo.set_column_authority(w.fx.sheet, w.fx.col_status, editors=["B", "AGENT"])
        w.execute(
            "updateCell",
            {"sheet": w.fx.sheet, "node": w.fx.X, "column": w.fx.col_status, "value": "doing"},
            Actor(user, atype),
        )
        d = w.deliveries_for("EXT_ENDPOINT")[0]
        ev = _delivered_event(w, d)
        payloads.append((ev.type, ev.sheet, ev.payload, ev.actor, ev.actor_type))

    # type/sheet/payload-shape identical across all three
    assert {p[0] for p in payloads} == {NVU}
    assert {p[1] for p in payloads} == {"S"}
    for _, _, payload, _, _ in payloads:
        assert set(payload) == {"node", "column", "old_value", "new_value", "version"}
        assert payload["node"] == "X" and payload["column"] == "col:status"
    # the ONLY differences are actor / actor_type
    assert payloads[2][3] == "AGENT" and payloads[2][4] == ActorType.AGENT.value
    assert payloads[0][3] == "B" and payloads[0][4] == ActorType.HUMAN.value
