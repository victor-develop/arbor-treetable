"""Backend integration — notification fan-out, ack ledger & accountability.

runnable: NEEDS FRAPPE BENCH (``@pytest.mark.bench``; auto-skipped when frappe is
absent). Exercises the REAL notification dispatcher
(``arbor.dispatch.frappe_dispatch.on_tree_event_insert`` + ``accountability``)
reacting to Tree Events produced by the REAL ``arbor.api`` funnel. The dispatcher
is driven explicitly via ``_helpers.dispatch_pending_events`` so these tests do
NOT depend on whether the integrator has wired the
``doc_events["Tree Event"]["after_insert"]`` hook into ``hooks.py`` yet (the
manifest this lane returns declares that hook). Dispatch dedups per
``(tree_event, recipient, channel)``, so the explicit drive is idempotent even
when a real hook is also active.

Cases translated from ``tests/notifications-and-ack.md`` (highest value):

* Subscription lifecycle (NOTIFICATIONS_AND_ACK-001/006).
* Dispatcher scope matching — sheet / branch (NestedSet, inclusive root) / column,
  event_types filter, multi-channel, CR linkage, no-event ops
  (009/010/011/012/013/014b/016/017).
* Lifecycle notifications — proposed / approved×2 / rejected (018/019/020).
* Persona G requires_ack + acknowledge + idempotency + ACL
  (023/024/025/026/027).
* Accountability "N notified / M acked" — single, ack-only denominator,
  multi-recipient, CR-scoped, zero-state (029/030/031/032/033).
* Temporal semantics + e2e ledger close (037/038/043).

Run::

    bench --site <site> run-tests --module tests.backend.test_notifications_ack
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.bench

frappe = pytest.importorskip("frappe")

from arbor import api  # noqa: E402

from tests.backend import _helpers as h  # noqa: E402


@pytest.fixture()
def fx():
    data = h.seed()
    yield data
    frappe.set_user("Administrator")


def _N(fx, label):
    return fx["nodes"][label]


def _C(fx, field):
    return fx["columns"][field]


def _sub_G(fx, event_types=None):
    """Create G's canonical sensitive subscription (branch P2, requires_ack)."""
    event_types = event_types or ["CHANGE_PROPOSED", "CHANGE_APPROVED", "NODE_DELETED"]
    h.login_as("G")
    out = api.subscribe(
        scope="branch",
        target=_N(fx, "P2"),
        event_types=event_types,
        delivery="in-app",
        requires_ack=True,
    )
    return out["data"]["subscription"]


# ===========================================================================
# A. Subscription lifecycle
# ===========================================================================
def test_self_subscribe_creates_row_and_emits_subscription_changed(fx):
    """NOTIFICATIONS_AND_ACK-001: E self-subscribes sheet-scope → one Subscription
    (subscriber=E, requires_ack default false) + one SUBSCRIPTION_CHANGED; no
    Notification from the subscribe call itself."""
    h.login_as("E")
    out = api.subscribe(scope="sheet", target=fx["sheet"], event_types=["CHANGE_APPROVED"], delivery="in-app")
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "SUBSCRIPTION_CHANGED"
    sub = out["data"]["subscription"]
    row = frappe.db.get_value("Subscription", sub, ["subscriber", "subscriber_kind", "scope", "requires_ack"], as_dict=True)
    assert row.subscriber == h.user("E") and row.scope == "sheet"
    assert row.requires_ack in (0, "0", False)
    # the SUBSCRIPTION_CHANGED event yields no notifications (no subscriber to it)
    h.dispatch_pending_events(fx["sheet"])
    assert h.notifications_for(out["event"]["event_id"]) == []


def test_unsubscribe_removes_row_and_stops_future_fanout(fx):
    """NOTIFICATIONS_AND_ACK-006/038: E unsubscribes → row gone, SUBSCRIPTION_CHANGED;
    a later matching event produces no Notification for E."""
    h.login_as("E")
    sub = api.subscribe(
        scope="sheet", target=fx["sheet"], event_types=["NODE_VALUE_UPDATED"], delivery="in-app"
    )["data"]["subscription"]
    api.unsubscribe(subscription=sub)
    assert not frappe.db.exists("Subscription", sub)
    # B authorized-edits col:name → NODE_VALUE_UPDATED, but E is unsubscribed.
    h.login_as("B")
    ev = api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "name"), value="v")["event"]["event_id"]
    h.dispatch_pending_events(fx["sheet"])
    assert h.notifications_for_recipient(ev, "E") == []


def test_unsubscribe_by_non_owner_denied(fx):
    """NOTIFICATIONS_AND_ACK-007: F cannot unsubscribe E's subscription (ACL deny; not a CR)."""
    h.login_as("E")
    sub = api.subscribe(scope="sheet", target=fx["sheet"], event_types=["CHANGE_APPROVED"], delivery="in-app")["data"]["subscription"]
    h.login_as("F")
    with pytest.raises(frappe.PermissionError):
        api.unsubscribe(subscription=sub)
    assert frappe.db.exists("Subscription", sub)


# ===========================================================================
# B. Dispatcher fan-out & scope matching
# ===========================================================================
def test_sheet_scope_subscriber_notified_on_matching_event(fx):
    """NOTIFICATIONS_AND_ACK-009: sheet-scope E gets exactly one in-app Notification
    for a matching NODE_VALUE_UPDATED, requires_ack=false."""
    h.login_as("E")
    api.subscribe(scope="sheet", target=fx["sheet"], event_types=["NODE_VALUE_UPDATED"], delivery="in-app")
    h.login_as("B")
    ev = api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "name"), value="v")["event"]["event_id"]
    h.dispatch_pending_events(fx["sheet"])
    notifs = h.notifications_for_recipient(ev, "E")
    assert len(notifs) == 1
    assert notifs[0]["channel"] == "in-app" and notifs[0]["requires_ack"] in (0, "0", False)


def test_branch_scope_matches_descendant_not_outsider(fx):
    """NOTIFICATIONS_AND_ACK-010/011: G (branch P2) is matched for a delete of Z
    (descendant of P2) but NOT for a delete of X (under P1)."""
    _sub_G(fx, event_types=["NODE_DELETED"])
    # delete Z (in P2) by its owner D → authorized NODE_DELETED.
    h.login_as("D")
    ev_z = api.delete_node(sheet=fx["sheet"], node=_N(fx, "Z"))["event"]["event_id"]
    # delete X (under P1) by A → authorized NODE_DELETED, outside P2.
    h.login_as("A")
    ev_x = api.delete_node(sheet=fx["sheet"], node=_N(fx, "X"))["event"]["event_id"]
    h.dispatch_pending_events(fx["sheet"])
    assert len(h.notifications_for_recipient(ev_z, "G")) == 1
    assert h.notifications_for_recipient(ev_x, "G") == []


def test_column_scope_matches_only_that_column(fx):
    """NOTIFICATIONS_AND_ACK-012: column-scope (col:budget) matches a col:budget value
    event but not a col:name value event."""
    h.login_as("C")
    api.subscribe(scope="column", target=_C(fx, "budget"), event_types=["NODE_VALUE_UPDATED"], delivery="email")
    # C edits col:budget on Y (authorized) → matches.
    ev_budget = api.update_cell(sheet=fx["sheet"], node=_N(fx, "Y"), column=_C(fx, "budget"), value=5001)["event"]["event_id"]
    # B edits col:name on Y (authorized) → does NOT match col:budget scope.
    h.login_as("B")
    ev_name = api.update_cell(sheet=fx["sheet"], node=_N(fx, "Y"), column=_C(fx, "name"), value="Y2")["event"]["event_id"]
    h.dispatch_pending_events(fx["sheet"])
    assert len(h.notifications_for_recipient(ev_budget, "C")) == 1
    assert h.notifications_for_recipient(ev_name, "C") == []


def test_event_types_filter_excludes_unsubscribed_type(fx):
    """NOTIFICATIONS_AND_ACK-013: E subscribed only to CHANGE_APPROVED gets nothing for
    a CHANGE_PROPOSED."""
    h.login_as("E")
    api.subscribe(scope="sheet", target=fx["sheet"], event_types=["CHANGE_APPROVED"], delivery="in-app")
    # F's suggestion under P2 emits CHANGE_PROPOSED.
    h.login_as("F")
    ev = api.add_node(sheet=fx["sheet"], parent=_N(fx, "P2"))["event"]["event_id"]
    h.dispatch_pending_events(fx["sheet"])
    assert h.notifications_for_recipient(ev, "E") == []


def test_two_channels_one_notification_each(fx):
    """NOTIFICATIONS_AND_ACK-014b: C with in-app + email sheet-scope subscriptions gets
    exactly two Notification rows (one per channel) for one matching event."""
    h.login_as("C")
    api.subscribe(scope="sheet", target=fx["sheet"], event_types=["NODE_VALUE_UPDATED"], delivery="in-app")
    api.subscribe(scope="sheet", target=fx["sheet"], event_types=["NODE_VALUE_UPDATED"], delivery="email")
    h.login_as("B")
    ev = api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "name"), value="vv")["event"]["event_id"]
    h.dispatch_pending_events(fx["sheet"])
    channels = sorted(n["channel"] for n in h.notifications_for_recipient(ev, "C"))
    assert channels == ["email", "in-app"]


def test_notification_copies_change_request_link(fx):
    """NOTIFICATIONS_AND_ACK-016: a CHANGE_PROPOSED-derived Notification carries the
    CR link."""
    _sub_G(fx, event_types=["CHANGE_PROPOSED"])
    h.login_as("F")
    out = api.add_node(sheet=fx["sheet"], parent=_N(fx, "Y"))  # in P2; suggested → CR to D
    ev = out["event"]["event_id"]
    cr = out["change_request"]
    h.dispatch_pending_events(fx["sheet"])
    notifs = h.notifications_for_recipient(ev, "G")
    assert len(notifs) == 1 and notifs[0]["change_request"] == cr


def test_acknowledge_emits_no_tree_event(fx):
    """NOTIFICATIONS_AND_ACK-017: acknowledge produces no Tree Event (so no fan-out)."""
    _sub_G(fx, event_types=["NODE_DELETED"])
    h.login_as("D")
    api.delete_node(sheet=fx["sheet"], node=_N(fx, "Z"))
    h.dispatch_pending_events(fx["sheet"])
    before = h.event_count(fx["sheet"])
    # G acks their notification.
    n = frappe.get_all("Notification", filters={"recipient": h.user("G")}, pluck="name")[0]
    h.login_as("G")
    api.acknowledge(notification=n)
    assert h.event_count(fx["sheet"]) == before  # no new Tree Event


# ===========================================================================
# C. Lifecycle notifications
# ===========================================================================
def test_change_proposed_notifies_resolved_approver(fx):
    """NOTIFICATIONS_AND_ACK-018: C subscribed to CHANGE_PROPOSED is notified when E's
    unauthorized col:budget edit routes a CR to C, with the CR linked."""
    h.login_as("C")
    api.subscribe(scope="sheet", target=fx["sheet"], event_types=["CHANGE_PROPOSED"], delivery="in-app")
    h.login_as("E")
    out = api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "budget"), value=1)
    h.dispatch_pending_events(fx["sheet"])
    notifs = h.notifications_for_recipient(out["event"]["event_id"], "C")
    assert len(notifs) == 1 and notifs[0]["change_request"] == out["change_request"]


def test_approval_fans_out_to_both_mutation_and_approved_events(fx):
    """NOTIFICATIONS_AND_ACK-019: G (branch P2, NODE_DELETED + CHANGE_APPROVED) gets TWO
    ack-required Notifications when D approves the delete of Z (one per emitted event)."""
    _sub_G(fx, event_types=["CHANGE_APPROVED", "NODE_DELETED"])
    h.login_as("E")
    cr = api.delete_node(sheet=fx["sheet"], node=_N(fx, "Z"))["change_request"]
    h.login_as("D")
    api.approve_change(change_request=cr)
    h.dispatch_pending_events(fx["sheet"])
    g_notifs = frappe.get_all("Notification", filters={"recipient": h.user("G")}, fields=["tree_event", "requires_ack"])
    types = {frappe.db.get_value("Tree Event", n["tree_event"], "type") for n in g_notifs}
    assert {"NODE_DELETED", "CHANGE_APPROVED"} <= types
    assert all(n["requires_ack"] in (1, "1", True) for n in g_notifs)


def test_rejection_notifies_without_mutation_event(fx):
    """NOTIFICATIONS_AND_ACK-020: subscriber to CHANGE_REJECTED is notified on reject;
    no NODE_VALUE_UPDATED exists for the CR."""
    h.login_as("E")
    api.subscribe(scope="sheet", target=fx["sheet"], event_types=["CHANGE_REJECTED"], delivery="in-app")
    cr = api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "budget"), value=2)["change_request"]
    h.login_as("C")
    rej = api.reject_change(change_request=cr)
    h.dispatch_pending_events(fx["sheet"])
    assert len(h.notifications_for_recipient(rej["event"]["event_id"], "E")) == 1
    # No mutation event linked to this CR.
    assert not h.cr_row(cr)["resulting_event"]


# ===========================================================================
# D. Persona G — requires_ack + acknowledge
# ===========================================================================
def test_requires_ack_copied_to_notification(fx):
    """NOTIFICATIONS_AND_ACK-024: G's branch sub (requires_ack=true) yields a
    Notification with requires_ack=1; a non-ack subscriber's is requires_ack=0."""
    _sub_G(fx, event_types=["NODE_DELETED"])
    h.login_as("E")  # non-ack sheet subscriber
    api.subscribe(scope="sheet", target=fx["sheet"], event_types=["NODE_DELETED"], delivery="in-app")
    h.login_as("D")
    ev = api.delete_node(sheet=fx["sheet"], node=_N(fx, "Z"))["event"]["event_id"]
    h.dispatch_pending_events(fx["sheet"])
    g = h.notifications_for_recipient(ev, "G")[0]
    e = h.notifications_for_recipient(ev, "E")[0]
    assert g["requires_ack"] in (1, "1", True)
    assert e["requires_ack"] in (0, "0", False)


def test_acknowledge_creates_one_row_and_is_idempotent(fx):
    """NOTIFICATIONS_AND_ACK-025/026: G acks → one Acknowledgement row; a second ack
    does not duplicate it (uniqueness (notification, user))."""
    _sub_G(fx, event_types=["NODE_DELETED"])
    h.login_as("D")
    ev = api.delete_node(sheet=fx["sheet"], node=_N(fx, "Z"))["event"]["event_id"]
    h.dispatch_pending_events(fx["sheet"])
    n = h.notifications_for_recipient(ev, "G")[0]["name"]
    h.login_as("G")
    api.acknowledge(notification=n)
    assert len(h.acks_for(n)) == 1
    # Idempotent second ack.
    try:
        api.acknowledge(notification=n)
    except frappe.exceptions.ValidationError:
        pass  # unique-constraint rejection is an acceptable idempotent outcome
    assert len(h.acks_for(n)) == 1


def test_acknowledge_by_non_recipient_denied(fx):
    """NOTIFICATIONS_AND_ACK-027: F cannot acknowledge G's notification (ACL deny)."""
    _sub_G(fx, event_types=["NODE_DELETED"])
    h.login_as("D")
    ev = api.delete_node(sheet=fx["sheet"], node=_N(fx, "Z"))["event"]["event_id"]
    h.dispatch_pending_events(fx["sheet"])
    n = h.notifications_for_recipient(ev, "G")[0]["name"]
    h.login_as("F")
    with pytest.raises(frappe.PermissionError):
        api.acknowledge(notification=n)
    assert h.acks_for(n) == []


# ===========================================================================
# E. Accountability report ("N notified / M acked")
# ===========================================================================
def test_accountability_single_event_before_and_after_ack(fx):
    """NOTIFICATIONS_AND_ACK-029: one ack-required event → {1,0}; after G acks → {1,1}."""
    _sub_G(fx, event_types=["NODE_DELETED"])
    h.login_as("D")
    ev = api.delete_node(sheet=fx["sheet"], node=_N(fx, "Z"))["event"]["event_id"]
    h.dispatch_pending_events(fx["sheet"])
    assert h.accountability(tree_event=ev) == {"notified": 1, "acked": 0}
    n = h.notifications_for_recipient(ev, "G")[0]["name"]
    h.login_as("G")
    api.acknowledge(notification=n)
    assert h.accountability(tree_event=ev) == {"notified": 1, "acked": 1}


def test_accountability_counts_only_ack_required_in_denominator(fx):
    """NOTIFICATIONS_AND_ACK-030: an event matched by G (ack) and E (non-ack) →
    notified counts only G (1, not 2)."""
    _sub_G(fx, event_types=["NODE_DELETED"])
    h.login_as("E")
    api.subscribe(scope="sheet", target=fx["sheet"], event_types=["NODE_DELETED"], delivery="in-app")
    h.login_as("D")
    ev = api.delete_node(sheet=fx["sheet"], node=_N(fx, "Z"))["event"]["event_id"]
    h.dispatch_pending_events(fx["sheet"])
    assert h.accountability(tree_event=ev) == {"notified": 1, "acked": 0}


def test_accountability_multi_recipient(fx):
    """NOTIFICATIONS_AND_ACK-031: G and G2 both branch-subscribe ack-required; one event
    → {2,0}; after only G acks → {2,1}."""
    _sub_G(fx, event_types=["NODE_DELETED"])
    h.ensure_user("G2")
    h.login_as("G2")
    api.subscribe(scope="branch", target=_N(fx, "P2"), event_types=["NODE_DELETED"], delivery="in-app", requires_ack=True)
    h.login_as("D")
    ev = api.delete_node(sheet=fx["sheet"], node=_N(fx, "Z"))["event"]["event_id"]
    h.dispatch_pending_events(fx["sheet"])
    assert h.accountability(tree_event=ev) == {"notified": 2, "acked": 0}
    n_g = h.notifications_for_recipient(ev, "G")[0]["name"]
    h.login_as("G")
    api.acknowledge(notification=n_g)
    assert h.accountability(tree_event=ev) == {"notified": 2, "acked": 1}


def test_accountability_scoped_to_change_request(fx):
    """NOTIFICATIONS_AND_ACK-032: report keyed by CR aggregates ack-required
    Notifications across the CR's proposed + approved (+ mutation) events."""
    _sub_G(fx, event_types=["CHANGE_PROPOSED", "CHANGE_APPROVED", "NODE_DELETED"])
    h.login_as("E")
    cr = api.delete_node(sheet=fx["sheet"], node=_N(fx, "Z"))["change_request"]
    h.dispatch_pending_events(fx["sheet"])  # proposed notification
    assert h.accountability(change_request=cr) == {"notified": 1, "acked": 0}
    h.login_as("D")
    api.approve_change(change_request=cr)
    h.dispatch_pending_events(fx["sheet"])  # + NODE_DELETED + CHANGE_APPROVED notifications
    report = h.accountability(change_request=cr)
    assert report["notified"] >= 1 and report["acked"] == 0


def test_accountability_zero_state(fx):
    """NOTIFICATIONS_AND_ACK-033: an event with no ack-required subscribers → {0,0}."""
    h.login_as("E")  # only a non-ack subscriber
    api.subscribe(scope="sheet", target=fx["sheet"], event_types=["NODE_VALUE_UPDATED"], delivery="in-app")
    h.login_as("B")
    ev = api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "name"), value="z")["event"]["event_id"]
    h.dispatch_pending_events(fx["sheet"])
    assert h.accountability(tree_event=ev) == {"notified": 0, "acked": 0}


# ===========================================================================
# G. Temporal semantics + e2e ledger close
# ===========================================================================
def test_subscription_after_event_does_not_backfill(fx):
    """NOTIFICATIONS_AND_ACK-037: a branch subscription created AFTER an event is not
    retroactively notified."""
    h.login_as("D")
    ev = api.delete_node(sheet=fx["sheet"], node=_N(fx, "Z"))["event"]["event_id"]
    h.dispatch_pending_events(fx["sheet"])  # no G subscription yet
    _sub_G(fx, event_types=["NODE_DELETED"])  # subscribe AFTER the event
    h.dispatch_pending_events(fx["sheet"])  # re-run dispatcher
    assert h.notifications_for_recipient(ev, "G") == []


def test_e2e_sensitive_change_ledger_closes(fx):
    """NOTIFICATIONS_AND_ACK-043: F proposes deleteNode(Z) in P2 → CR to D + proposed
    notification to G; D approves → NODE_DELETED + CHANGE_APPROVED notify G; G acks
    all → CR-scoped report reads fully acknowledged (acked == notified)."""
    _sub_G(fx, event_types=["CHANGE_PROPOSED", "CHANGE_APPROVED", "NODE_DELETED"])
    h.login_as("F")
    cr = api.delete_node(sheet=fx["sheet"], node=_N(fx, "Z"))["change_request"]
    h.dispatch_pending_events(fx["sheet"])
    assert h.accountability(change_request=cr) == {"notified": 1, "acked": 0}
    h.login_as("D")
    api.approve_change(change_request=cr)
    h.dispatch_pending_events(fx["sheet"])
    # G acks every ack-required notification linked to the CR.
    h.login_as("G")
    for n in h.notifications_for_cr(cr):
        if n["requires_ack"] in (1, "1", True):
            api.acknowledge(notification=n["name"])
    report = h.accountability(change_request=cr)
    assert report["notified"] >= 1 and report["acked"] == report["notified"]
