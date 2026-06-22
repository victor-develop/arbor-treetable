"""Notification dispatcher — fan-out, scope matching, ack ledger, accountability.

Bench-free: drives ``NotificationDispatcher`` over the in-memory store + frozen
clock. Maps to NOTIFICATIONS_AND_ACK-* cases (see docstring tags). The canonical
NestedSet ranges (TEST-PLAN §2) are R(1,12) P1(2,5) X(3,4) P2(6,11) Y(7,8) Z(9,10).
"""

from __future__ import annotations

from arbor.arbor.dispatch.notify import Accountability, NotificationDispatcher
from arbor.arbor.dispatch.testing import (
    FakeClock,
    FakeEvent,
    FakeSubscription,
    InMemoryNotificationStore,
)

# Canonical node ranges.
RANGES = {"R": (1, 12), "P1": (2, 5), "X": (3, 4), "P2": (6, 11), "Y": (7, 8), "Z": (9, 10)}


def _store_with_ranges() -> InMemoryNotificationStore:
    store = InMemoryNotificationStore()
    for node, (lft, rgt) in RANGES.items():
        store.set_node_range(node, lft, rgt)
    return store


def _dispatcher(store):
    return NotificationDispatcher(store, FakeClock())


# --- A. scope matching -----------------------------------------------------
def test_sheet_scope_match_creates_one_notification():
    """NOTIFICATIONS_AND_ACK-009: sheet subscriber notified on matching event."""
    store = _store_with_ranges()
    store.add_subscription(
        FakeSubscription("SUB_E", "E", "sheet", "S", ["NODE_VALUE_UPDATED"], "in-app")
    )
    ev = FakeEvent("evt1", "S", "NODE_VALUE_UPDATED", {"node": "X", "column": "col:name"})
    created = _dispatcher(store).on_tree_event(ev)
    assert len(created) == 1
    n = store.notifications[created[0]]
    assert n["recipient"] == "E" and n["channel"] == "in-app"
    assert n["requires_ack"] is False and n["delivered_at"] is not None


def test_branch_scope_descendant_matches():
    """NOTIFICATIONS_AND_ACK-010: descendant Z of P2 matches branch sub."""
    store = _store_with_ranges()
    store.add_subscription(
        FakeSubscription("SUB_G", "G", "branch", "P2", ["NODE_DELETED"], "in-app", requires_ack=True)
    )
    ev = FakeEvent("evtZ", "S", "NODE_DELETED", {"node": "Z"})
    created = _dispatcher(store).on_tree_event(ev)
    assert len(created) == 1
    assert store.notifications[created[0]]["requires_ack"] is True


def test_branch_scope_includes_root_inclusive():
    """NOTIFICATIONS_AND_ACK-010b: branch scope is inclusive of the root P2."""
    store = _store_with_ranges()
    store.add_subscription(
        FakeSubscription("SUB_G", "G", "branch", "P2", ["NODE_MOVED"], "in-app")
    )
    ev = FakeEvent("evtP2", "S", "NODE_MOVED", {"node": "P2"})
    assert len(_dispatcher(store).on_tree_event(ev)) == 1


def test_branch_scope_outside_does_not_match():
    """NOTIFICATIONS_AND_ACK-011: X (under P1) is outside P2's range."""
    store = _store_with_ranges()
    store.add_subscription(
        FakeSubscription("SUB_G", "G", "branch", "P2", ["NODE_DELETED"], "in-app")
    )
    ev = FakeEvent("evtX", "S", "NODE_DELETED", {"node": "X"})
    assert _dispatcher(store).on_tree_event(ev) == []


def test_column_scope_direct_equality():
    """NOTIFICATIONS_AND_ACK-012: column scope matches payload.column exactly."""
    store = _store_with_ranges()
    store.add_subscription(
        FakeSubscription("SUB_C", "C", "column", "col:budget", ["NODE_VALUE_UPDATED"], "email")
    )
    match = FakeEvent("e1", "S", "NODE_VALUE_UPDATED", {"node": "Y", "column": "col:budget"})
    nomatch = FakeEvent("e2", "S", "NODE_VALUE_UPDATED", {"node": "Y", "column": "col:name"})
    d = _dispatcher(store)
    assert len(d.on_tree_event(match)) == 1
    assert d.on_tree_event(nomatch) == []


def test_event_types_filter_excludes_unsubscribed_type():
    """NOTIFICATIONS_AND_ACK-013: event-type filter excludes non-subscribed types."""
    store = _store_with_ranges()
    store.add_subscription(
        FakeSubscription("SUB_E", "E", "sheet", "S", ["CHANGE_APPROVED"], "in-app")
    )
    ev = FakeEvent("e", "S", "CHANGE_PROPOSED", {})
    assert _dispatcher(store).on_tree_event(ev) == []


def test_overlapping_subscriptions_collapse_one_per_channel():
    """NOTIFICATIONS_AND_ACK-014: two matching subs, same channel → one Notification."""
    store = _store_with_ranges()
    store.add_subscription(
        FakeSubscription("C1", "C", "sheet", "S", ["NODE_VALUE_UPDATED"], "in-app")
    )
    store.add_subscription(
        FakeSubscription("C2", "C", "column", "col:budget", ["NODE_VALUE_UPDATED"], "in-app")
    )
    ev = FakeEvent("e", "S", "NODE_VALUE_UPDATED", {"node": "Y", "column": "col:budget"})
    assert len(_dispatcher(store).on_tree_event(ev)) == 1


def test_two_channels_two_notifications():
    """NOTIFICATIONS_AND_ACK-014b: same recipient, distinct channels → distinct rows."""
    store = _store_with_ranges()
    store.add_subscription(
        FakeSubscription("Ci", "C", "sheet", "S", ["NODE_VALUE_UPDATED"], "in-app")
    )
    store.add_subscription(
        FakeSubscription("Ce", "C", "sheet", "S", ["NODE_VALUE_UPDATED"], "email")
    )
    ev = FakeEvent("e", "S", "NODE_VALUE_UPDATED", {"node": "Y", "column": "col:budget"})
    created = _dispatcher(store).on_tree_event(ev)
    channels = {store.notifications[c]["channel"] for c in created}
    assert channels == {"in-app", "email"}


def test_delivered_at_and_channel_from_clock():
    """NOTIFICATIONS_AND_ACK-015: delivered_at = clock.now(), channel from sub."""
    store = _store_with_ranges()
    clock = FakeClock()
    store.add_subscription(
        FakeSubscription("SUB_E", "E", "sheet", "S", ["NODE_VALUE_UPDATED"], "email")
    )
    ev = FakeEvent("e", "S", "NODE_VALUE_UPDATED", {"node": "Y", "column": "col:budget"})
    created = NotificationDispatcher(store, clock).on_tree_event(ev)
    n = store.notifications[created[0]]
    assert n["channel"] == "email" and n["delivered_at"] == clock.now()


def test_change_request_link_copied():
    """NOTIFICATIONS_AND_ACK-016: Notification copies the CR link from the event."""
    store = _store_with_ranges()
    store.add_subscription(
        FakeSubscription("SUB_G", "G", "branch", "P2", ["CHANGE_PROPOSED"], "in-app")
    )
    ev = FakeEvent(
        "e", "S", "CHANGE_PROPOSED", {"node": "Z", "change_request": "CR1"}, change_request="CR1"
    )
    created = _dispatcher(store).on_tree_event(ev)
    assert store.notifications[created[0]]["change_request"] == "CR1"


# --- temporal / idempotency ------------------------------------------------
def test_idempotent_redispatch_same_event():
    """NOTIFICATIONS_AND_ACK-014 cross-invocation: re-run yields no duplicate."""
    store = _store_with_ranges()
    store.add_subscription(
        FakeSubscription("SUB_E", "E", "sheet", "S", ["NODE_VALUE_UPDATED"], "in-app")
    )
    ev = FakeEvent("e", "S", "NODE_VALUE_UPDATED", {"node": "X", "column": "col:name"})
    d = _dispatcher(store)
    d.on_tree_event(ev)
    d.on_tree_event(ev)  # redelivery
    assert len(store.notifications) == 1


def test_late_subscription_not_backfilled():
    """NOTIFICATIONS_AND_ACK-037: subscription created after event isn't backfilled.

    The dispatcher runs once per event; a subscription added after the run never
    sees that past event."""
    store = _store_with_ranges()
    ev = FakeEvent("e", "S", "NODE_DELETED", {"node": "Z"})
    d = _dispatcher(store)
    assert d.on_tree_event(ev) == []  # no subs yet
    store.add_subscription(FakeSubscription("SUB_G", "G", "branch", "P2", ["NODE_DELETED"], "in-app"))
    # the past event is not re-dispatched; only NEW events match.
    assert len(store.notifications) == 0


def test_unsubscribed_before_event_no_notification():
    """NOTIFICATIONS_AND_ACK-038: removed subscription yields no fan-out."""
    store = _store_with_ranges()
    store.add_subscription(FakeSubscription("SUB_E", "E", "sheet", "S", ["NODE_VALUE_UPDATED"], "in-app"))
    store.remove_subscription("SUB_E")
    ev = FakeEvent("e", "S", "NODE_VALUE_UPDATED", {"node": "X", "column": "col:name"})
    assert _dispatcher(store).on_tree_event(ev) == []


def test_dangling_branch_target_graceful():
    """NOTIFICATIONS_AND_ACK-041: deleted branch-root target matches nothing, no crash."""
    store = _store_with_ranges()
    store.add_subscription(FakeSubscription("SUB", "G", "branch", "GONE", ["NODE_DELETED"], "in-app"))
    ev = FakeEvent("e", "S", "NODE_DELETED", {"node": "Z"})
    assert _dispatcher(store).on_tree_event(ev) == []


def test_node_moved_into_branch_matched_by_current_range():
    """NOTIFICATIONS_AND_ACK-040: branch membership uses current NestedSet position."""
    store = _store_with_ranges()
    store.add_subscription(FakeSubscription("SUB_G", "G", "branch", "P2", ["NODE_VALUE_UPDATED"], "in-app"))
    # X originally under P1 (3,4) — outside P2. After move into P2 it gets a range inside (6,11).
    store.set_node_range("X", 7, 8)  # now inside P2
    store.set_node_range("Y", 9, 10)
    ev = FakeEvent("e", "S", "NODE_VALUE_UPDATED", {"node": "X", "column": "col:name"})
    assert len(_dispatcher(store).on_tree_event(ev)) == 1


# --- E. accountability -----------------------------------------------------
def _seed_ack_event(store, recipient="G", requires_ack=True, tree_event="evtA", cr=None):
    store.create_notification(
        {
            "tree_event": tree_event,
            "change_request": cr,
            "recipient": recipient,
            "channel": "in-app",
            "requires_ack": requires_ack,
            "delivered_at": "t",
        }
    )


def test_accountability_single_event():
    """NOTIFICATIONS_AND_ACK-029: notified/acked for one ack-required event."""
    store = _store_with_ranges()
    _seed_ack_event(store, "G", True, "evtA")
    d = _dispatcher(store)
    assert d.accountability(tree_event="evtA") == Accountability(1, 0)
    # G acks
    notif = next(iter(store.notifications))
    store.add_acknowledgement(notif, "G")
    assert d.accountability(tree_event="evtA") == Accountability(1, 1)


def test_accountability_excludes_non_ack_subscribers():
    """NOTIFICATIONS_AND_ACK-030: only requires_ack notifications count."""
    store = _store_with_ranges()
    _seed_ack_event(store, "G", True, "evtA")
    _seed_ack_event(store, "E", False, "evtA")
    assert _dispatcher(store).accountability(tree_event="evtA") == Accountability(1, 0)


def test_accountability_multi_recipient():
    """NOTIFICATIONS_AND_ACK-031: aggregates across G and G2."""
    store = _store_with_ranges()
    _seed_ack_event(store, "G", True, "evtA")
    _seed_ack_event(store, "G2", True, "evtA")
    d = _dispatcher(store)
    assert d.accountability(tree_event="evtA") == Accountability(2, 0)
    notif = next(iter(store.notifications))
    store.add_acknowledgement(notif, "G")
    assert d.accountability(tree_event="evtA") == Accountability(2, 1)


def test_accountability_cr_scoped():
    """NOTIFICATIONS_AND_ACK-032: report scoped to a Change Request."""
    store = _store_with_ranges()
    _seed_ack_event(store, "G", True, "evtP", cr="CR1")
    _seed_ack_event(store, "G", True, "evtA", cr="CR1")
    assert _dispatcher(store).accountability(change_request="CR1") == Accountability(2, 0)


def test_accountability_zero_state():
    """NOTIFICATIONS_AND_ACK-033: no ack-required subscribers → 0 notified / 0 acked."""
    store = _store_with_ranges()
    _seed_ack_event(store, "E", False, "evtA")
    assert _dispatcher(store).accountability(tree_event="evtA") == Accountability(0, 0)


def test_agent_actor_event_dispatched_identically():
    """NOTIFICATIONS_AND_ACK-035: dispatcher does not branch on actor_type."""
    store = _store_with_ranges()
    store.add_subscription(FakeSubscription("SUB_E", "E", "sheet", "S", ["NODE_VALUE_UPDATED"], "in-app"))
    ev = FakeEvent("e", "S", "NODE_VALUE_UPDATED", {"node": "X", "column": "col:notes"}, actor="AGENT", actor_type="agent")
    assert len(_dispatcher(store).on_tree_event(ev)) == 1


def test_external_webhook_channel_notification():
    """NOTIFICATIONS_AND_ACK-034: delivery=webhook produces channel=webhook row."""
    store = _store_with_ranges()
    store.add_subscription(
        FakeSubscription("EXT", "EXT", "sheet", "S", ["CHANGE_APPROVED"], "webhook", subscriber_kind="external")
    )
    ev = FakeEvent("e", "S", "CHANGE_APPROVED", {}, change_request="CR1")
    created = _dispatcher(store).on_tree_event(ev)
    assert store.notifications[created[0]]["channel"] == "webhook"
