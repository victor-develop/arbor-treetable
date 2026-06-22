"""Backend integration — Change Request lifecycle (propose → decide → replay).

runnable: NEEDS FRAPPE BENCH (``@pytest.mark.bench``; auto-skipped when frappe is
absent). Drives the REAL CR state machine through the whitelisted REST funnel:
``suggestChange`` / implicit-suggest, ``approveChange`` (replays the deferred
capability AS the resolved approver and emits the REAL mutation event then
``CHANGE_APPROVED``), ``rejectChange``, ``withdrawChange``, the approver/requester
role split, idempotency/terminal-state guards, and moveNode dual-approval.

Cases translated from ``tests/change-request-lifecycle.md`` (and the CR slice of
``tests/permissions-and-delegation.md`` §7):

* Proposal birth + payload fidelity (CHANGE_REQUEST_LIFECYCLE-001/002/004).
* Happy-path replay for value / structure / schema
  (CHANGE_REQUEST_LIFECYCLE-010/011/012/013/014/015).
* Reject / withdraw + role separation (020/021/022/023).
* Denied approval paths (030/031/032/033).
* Owner-self policy approve (041/042).
* Agent-as-actor parity (050/051).
* Idempotency / terminal-state guards (060/061/062/063).
* moveNode dual-approval (070/071/072).
* Event-log ordering + append-only (081/111).
* Decision-time re-resolution (053/054/055; PERMISSIONS_AND_DELEGATION-054) —
  see the ``xfail`` note below.

DECISION-TIME RE-RESOLUTION NOTE. The canonical spec (ARCHITECTURE §5,
PERMISSIONS §1) requires that ``approveChange`` *re-resolve* the approver at
decision time when grants/columns changed since proposal. The shipped core
(``arbor.core.change_request.approve_change``) approves against the CR's STORED
``resolved_approver`` and does not recompute it. The re-resolution cases below are
therefore marked ``xfail(strict=False)``: they are written exactly to the
contract and bind to the real endpoints, so they pass automatically once the core
adds decision-time re-resolution, and flag (not error) until then.

Run::

    bench --site <site> run-tests --module tests.backend.test_change_request_lifecycle
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


def _propose_budget_update_by_E(fx, value=99, node="X"):
    """Helper: E (no authority) updates col:budget → returns the new CR name
    (resolved_approver = C). Reused by several lifecycle cases."""
    h.login_as("E")
    out = api.update_cell(sheet=fx["sheet"], node=_N(fx, node), column=_C(fx, "budget"), value=value)
    assert out["kind"] == "suggested"
    return out["change_request"]


# ===========================================================================
# A. Proposal creation
# ===========================================================================
def test_unauthorized_mutation_auto_creates_cr(fx):
    """CHANGE_REQUEST_LIFECYCLE-001: E's unauthorized updateCell → CR (proposed,
    resolved_approver=C, requester=E), one CHANGE_PROPOSED, no mutation."""
    before = h.cell_value(_N(fx, "X"), _C(fx, "status"))
    before_v = h.cell_version(_N(fx, "X"), _C(fx, "status"))
    h.login_as("E")
    out = api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "status"), value="done")
    assert out["kind"] == "suggested"
    cr = h.cr_row(out["change_request"])
    assert cr["status"] == "proposed"
    assert cr["requester"] == h.user("E") and cr["resolved_approver"] == h.user("C")
    assert cr["target_kind"] == "cell-value" and cr["operation"] == "update"
    assert out["event"]["type"] == "CHANGE_PROPOSED"
    assert out["event"]["actor"] == h.user("E") and out["event"]["actor_type"] == "human"
    assert h.cell_value(_N(fx, "X"), _C(fx, "status")) == before
    assert h.cell_version(_N(fx, "X"), _C(fx, "status")) == before_v


def test_explicit_suggest_resolves_approver_from_payload(fx):
    """CHANGE_REQUEST_LIFECYCLE-002: F's explicit suggestChange addNode under P2 →
    CR routed to D (resolved from the intended parent); no node created."""
    h.login_as("F")
    out = api.suggest_change(
        sheet=fx["sheet"],
        target_kind="node-structure",
        operation="add",
        payload={"sheet": fx["sheet"], "parent": _N(fx, "P2"), "resolved_approver": h.user("D")},
    )
    assert out["kind"] == "suggested"
    cr = h.cr_row(out["change_request"])
    assert cr["requester"] == h.user("F") and cr["operation"] == "add"


def test_cr_payload_is_faithful_replayable_copy(fx):
    """CHANGE_REQUEST_LIFECYCLE-004: CR payload preserves the exact params
    (typed value array preserved) for replay without reconstruction."""
    h.login_as("E")
    out = api.update_cell(sheet=fx["sheet"], node=_N(fx, "Y"), column=_C(fx, "budget"), value=[1, 2, 3])
    payload = h.cr_payload(out["change_request"])
    assert payload["node"] == _N(fx, "Y")
    assert payload["column"] == _C(fx, "budget")
    assert payload["value"] == [1, 2, 3]


# ===========================================================================
# B. Happy-path approval — replay correctness
# ===========================================================================
def test_approve_cell_value_replays_as_approver_and_emits_ordered_events(fx):
    """CHANGE_REQUEST_LIFECYCLE-010/011/081: C approves E's col:budget CR → replay as C
    mutates the cell (version+1); CR.status=approved, decided_by=C, resulting_event
    links the mutation event; CHANGE_APPROVED emitted last; lifecycle order is
    [CHANGE_PROPOSED, CHANGE_APPROVED]."""
    before_v = h.cell_version(_N(fx, "X"), _C(fx, "budget")) or 0
    cr = _propose_budget_update_by_E(fx, value=99)
    h.login_as("C")
    out = api.approve_change(change_request=cr)
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "CHANGE_APPROVED"
    row = h.cr_row(cr)
    assert row["status"] == "approved" and row["decided_by"] == h.user("C")
    assert row["resulting_event"]  # links the real mutation event
    assert h.cell_value(_N(fx, "X"), _C(fx, "budget")) == 99
    assert (h.cell_version(_N(fx, "X"), _C(fx, "budget")) or 0) == before_v + 1
    # The mutation event records the APPROVER as actor (not the requester E).
    mut = frappe.db.get_value("Tree Event", row["resulting_event"], ["type", "actor"], as_dict=True)
    assert mut.type == "NODE_VALUE_UPDATED" and mut.actor == h.user("C")
    # Lifecycle events linked to the CR, in order.
    lifecycle = [e["type"] for e in h.events_of_cr(cr)]
    assert lifecycle == ["CHANGE_PROPOSED", "CHANGE_APPROVED"]


def test_approve_structural_add_replays_addnode(fx):
    """CHANGE_REQUEST_LIFECYCLE-012: D approves F's addNode(parent=P2) CR → a node is
    created under P2; resulting_event is NODE_CREATED."""
    h.login_as("F")
    proposed = api.add_node(sheet=fx["sheet"], parent=_N(fx, "P2"))
    cr = proposed["change_request"]
    before = frappe.db.count("Tree Node", {"sheet": fx["sheet"]})
    h.login_as("D")
    out = api.approve_change(change_request=cr)
    assert out["kind"] == "executed"
    assert frappe.db.count("Tree Node", {"sheet": fx["sheet"]}) == before + 1
    mut = frappe.db.get_value("Tree Event", h.cr_row(cr)["resulting_event"], "type")
    assert mut == "NODE_CREATED"


def test_approve_delete_replays_deletenode(fx):
    """CHANGE_REQUEST_LIFECYCLE-013: A approves E's deleteNode(X) CR → X removed;
    resulting_event NODE_DELETED."""
    h.login_as("E")
    proposed = api.delete_node(sheet=fx["sheet"], node=_N(fx, "X"))
    cr = proposed["change_request"]
    h.login_as("A")
    api.approve_change(change_request=cr)
    assert not frappe.db.exists("Tree Node", _N(fx, "X"))
    assert frappe.db.get_value("Tree Event", h.cr_row(cr)["resulting_event"], "type") == "NODE_DELETED"


def test_approve_column_schema_replays_meta_op(fx):
    """CHANGE_REQUEST_LIFECYCLE-014: C approves E's updateColumn(col:budget) CR →
    schema patched; resulting_event COLUMN_CONFIG_UPDATED."""
    h.login_as("E")
    proposed = api.update_column(sheet=fx["sheet"], column=_C(fx, "budget"), patch={"label": "Budget!"})
    cr = proposed["change_request"]
    assert h.cr_row(cr)["resolved_approver"] == h.user("C")
    h.login_as("C")
    api.approve_change(change_request=cr)
    assert frappe.db.get_value("Tree Column", _C(fx, "budget"), "label") == "Budget!"
    assert frappe.db.get_value("Tree Event", h.cr_row(cr)["resulting_event"], "type") == "COLUMN_CONFIG_UPDATED"


def test_column_editor_may_approve(fx):
    """CHANGE_REQUEST_LIFECYCLE-015 / PERMISSIONS_AND_DELEGATION-051: B (editor on
    col:status, not the resolved owner C) approves E's CR → replay runs as the
    resolved approver, cell updated, decided_by=B."""
    h.login_as("E")
    proposed = api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "status"), value="blocked")
    cr = proposed["change_request"]
    h.login_as("B")
    out = api.approve_change(change_request=cr)
    assert out["kind"] == "executed"
    assert h.cell_value(_N(fx, "X"), _C(fx, "status")) == "blocked"
    assert h.cr_row(cr)["decided_by"] == h.user("B")


# ===========================================================================
# C. Reject & withdraw transitions
# ===========================================================================
def test_reject_sets_status_and_mutates_nothing(fx):
    """CHANGE_REQUEST_LIFECYCLE-020: C rejects E's CR → status=rejected, decided_by=C,
    resulting_event null, CHANGE_REJECTED emitted, cell unchanged."""
    before = h.cell_value(_N(fx, "X"), _C(fx, "status"))
    h.login_as("E")
    cr = api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "status"), value="done")["change_request"]
    h.login_as("C")
    out = api.reject_change(change_request=cr, comment="no")
    assert out["event"]["type"] == "CHANGE_REJECTED"
    row = h.cr_row(cr)
    assert row["status"] == "rejected" and row["decided_by"] == h.user("C")
    assert not row["resulting_event"]
    assert h.cell_value(_N(fx, "X"), _C(fx, "status")) == before


def test_requester_withdraws_emits_change_rejected_withdrawn(fx):
    """CHANGE_REQUEST_LIFECYCLE-021 / PERMISSIONS_AND_DELEGATION-052: E withdraws own CR
    → status=withdrawn, CHANGE_REJECTED with payload.reason='withdrawn', no mutation."""
    cr = _propose_budget_update_by_E(fx)
    h.login_as("E")
    out = api.withdraw_change(change_request=cr)
    assert out["event"]["type"] == "CHANGE_REJECTED"
    assert h.cr_row(cr)["status"] == "withdrawn"
    ev = frappe.db.get_value("Tree Event", out["event"]["event_id"], "payload")
    payload = frappe.parse_json(ev) if isinstance(ev, str) else ev
    assert payload.get("reason") == "withdrawn"


def test_non_requester_cannot_withdraw(fx):
    """CHANGE_REQUEST_LIFECYCLE-022: F (not the requester) withdraws E's CR → denied;
    status stays proposed."""
    cr = _propose_budget_update_by_E(fx)
    h.login_as("F")
    with pytest.raises(frappe.PermissionError):
        api.withdraw_change(change_request=cr)
    assert h.cr_row(cr)["status"] == "proposed"


def test_role_separation_approver_cannot_withdraw_requester_cannot_reject(fx):
    """CHANGE_REQUEST_LIFECYCLE-023: approver→{approve,reject}, requester→{withdraw}.
    C (approver) withdraw → denied; E (requester) reject → denied; CR stays proposed."""
    cr = _propose_budget_update_by_E(fx)
    h.login_as("C")
    with pytest.raises(frappe.PermissionError):
        api.withdraw_change(change_request=cr)
    h.login_as("E")
    with pytest.raises(frappe.PermissionError):
        api.reject_change(change_request=cr)
    assert h.cr_row(cr)["status"] == "proposed"


# ===========================================================================
# D. Approval permission-DENIED paths
# ===========================================================================
def test_non_approver_cannot_approve(fx):
    """CHANGE_REQUEST_LIFECYCLE-030 / PERMISSIONS_AND_DELEGATION-050: E (suggest-only)
    approves a CR routed to C → 403; stays proposed; cell unchanged."""
    before = h.cell_value(_N(fx, "X"), _C(fx, "budget"))
    cr = _propose_budget_update_by_E(fx)
    h.login_as("E")  # E is the requester, not an approver
    with pytest.raises(frappe.PermissionError):
        api.approve_change(change_request=cr)
    assert h.cr_row(cr)["status"] == "proposed"
    assert h.cell_value(_N(fx, "X"), _C(fx, "budget")) == before


def test_structural_approver_of_other_branch_cannot_approve(fx):
    """CHANGE_REQUEST_LIFECYCLE-032: A (root) cannot approve a CR delegated to D
    (P2 add) while grant active; stays proposed."""
    h.login_as("F")
    cr = api.add_node(sheet=fx["sheet"], parent=_N(fx, "P2"))["change_request"]
    assert h.cr_row(cr)["resolved_approver"] == h.user("D")
    h.login_as("A")
    with pytest.raises(frappe.PermissionError):
        api.approve_change(change_request=cr)
    assert h.cr_row(cr)["status"] == "proposed"


def test_column_owner_cannot_approve_structural_cr(fx):
    """CHANGE_REQUEST_LIFECYCLE-033: C (column owner, no Axis-1 authority) cannot
    approve a structural CR (delete Z, approver D)."""
    h.login_as("E")
    cr = api.delete_node(sheet=fx["sheet"], node=_N(fx, "Z"))["change_request"]
    assert h.cr_row(cr)["resolved_approver"] == h.user("D")
    h.login_as("C")
    with pytest.raises(frappe.PermissionError):
        api.approve_change(change_request=cr)


# ===========================================================================
# E. Owner-self policy approve
# ===========================================================================
def test_owner_self_cr_can_be_self_approved():
    """CHANGE_REQUEST_LIFECYCLE-041/042: with the flag on, C's action becomes a
    self-approver CR; C then approves it → mutation lands, full audit trail."""
    fx = h.seed(settings={"owners_must_use_change_requests": True})
    try:
        h.login_as("C")
        proposed = api.update_cell(
            sheet=fx["sheet"], node=fx["nodes"]["Y"], column=fx["columns"]["budget"], value=99
        )
        assert proposed["kind"] == "suggested"
        cr = proposed["change_request"]
        out = api.approve_change(change_request=cr)
        assert out["kind"] == "executed"
        assert h.cell_value(fx["nodes"]["Y"], fx["columns"]["budget"]) == 99
        assert [e["type"] for e in h.events_of_cr(cr)] == ["CHANGE_PROPOSED", "CHANGE_APPROVED"]
    finally:
        frappe.set_user("Administrator")


# ===========================================================================
# F. Agent-as-actor (note: REST surface attributes actor_type=human; agent
# parity is proven against the same execute_action path in the agent lane).
# ===========================================================================
def test_human_approves_a_filed_cr_replays_as_human(fx):
    """CHANGE_REQUEST_LIFECYCLE-050/051 (REST slice): a non-owner files a CR; the
    human approver's approval is the authority — the replay runs as the approver,
    never the requester."""
    cr = _propose_budget_update_by_E(fx, value=7, node="Y")
    h.login_as("C")
    api.approve_change(change_request=cr)
    assert h.cell_value(_N(fx, "Y"), _C(fx, "budget")) == 7
    mut = frappe.db.get_value("Tree Event", h.cr_row(cr)["resulting_event"], "actor")
    assert mut == h.user("C")


# ===========================================================================
# H. Idempotency & terminal-state guards
# ===========================================================================
def test_double_approve_is_rejected(fx):
    """CHANGE_REQUEST_LIFECYCLE-060: second approve → 409; no second mutation/event."""
    before_v = h.cell_version(_N(fx, "X"), _C(fx, "budget")) or 0
    cr = _propose_budget_update_by_E(fx)
    h.login_as("C")
    api.approve_change(change_request=cr)
    v_after_first = h.cell_version(_N(fx, "X"), _C(fx, "budget"))
    lifecycle_after_first = h.events_of_cr(cr)
    with pytest.raises(frappe.ValidationError):
        api.approve_change(change_request=cr)
    assert h.cell_version(_N(fx, "X"), _C(fx, "budget")) == v_after_first == before_v + 1
    assert len(h.events_of_cr(cr)) == len(lifecycle_after_first)  # no duplicate CHANGE_APPROVED


def test_approve_after_reject_is_rejected(fx):
    """CHANGE_REQUEST_LIFECYCLE-061: approve a rejected CR → 409; stays rejected."""
    cr = _propose_budget_update_by_E(fx)
    h.login_as("C")
    api.reject_change(change_request=cr)
    with pytest.raises(frappe.ValidationError):
        api.approve_change(change_request=cr)
    assert h.cr_row(cr)["status"] == "rejected"


def test_reject_after_approve_is_rejected(fx):
    """CHANGE_REQUEST_LIFECYCLE-062: reject an approved CR → 409; stays approved."""
    cr = _propose_budget_update_by_E(fx)
    h.login_as("C")
    api.approve_change(change_request=cr)
    with pytest.raises(frappe.ValidationError):
        api.reject_change(change_request=cr)
    assert h.cr_row(cr)["status"] == "approved"


def test_withdraw_after_decision_is_rejected(fx):
    """CHANGE_REQUEST_LIFECYCLE-063: withdraw a decided CR → 409."""
    cr = _propose_budget_update_by_E(fx)
    h.login_as("C")
    api.approve_change(change_request=cr)
    h.login_as("E")
    with pytest.raises(frappe.ValidationError):
        api.withdraw_change(change_request=cr)


# ===========================================================================
# I. moveNode CRs (two-ended authority; co-approver semantics)
# ===========================================================================
def test_move_cr_routes_to_dest_with_src_co_approver(fx):
    """CHANGE_REQUEST_LIFECYCLE-070: A moves X→P2 → CR target_kind=node-structure,
    operation=move, resolved_approver=D, payload.co_approvers includes A; X not moved."""
    h.login_as("A")
    out = api.move_node(sheet=fx["sheet"], node=_N(fx, "X"), new_parent=_N(fx, "P2"))
    assert out["kind"] == "suggested"
    cr = h.cr_row(out["change_request"])
    assert cr["operation"] == "move" and cr["resolved_approver"] == h.user("D")
    assert h.user("A") in (h.cr_payload(out["change_request"]).get("co_approvers") or [])


def test_move_cr_requires_both_dest_and_src_co_approver(fx):
    """CHANGE_REQUEST_LIFECYCLE-071: the single move CR (ADR-001) transitions to
    approved and replays only once dest D AND co-approver A have approved →
    NODE_MOVED + CHANGE_APPROVED; X re-parented under P2."""
    h.login_as("A")
    out = api.move_node(sheet=fx["sheet"], node=_N(fx, "X"), new_parent=_N(fx, "P2"))
    cr = out["change_request"]
    # Dest approver alone: partial — CR stays proposed, no move yet.
    h.login_as("D")
    partial = api.approve_change(change_request=cr)
    assert partial["kind"] == "suggested"  # pending co-approver A
    assert h.cr_row(cr)["status"] == "proposed"
    p2 = frappe.db.get_value("Tree Node", _N(fx, "P2"), ["lft", "rgt"], as_dict=True)
    x = frappe.db.get_value("Tree Node", _N(fx, "X"), ["lft", "rgt"], as_dict=True)
    assert not (p2.lft < x.lft and x.rgt < p2.rgt)  # not yet inside P2
    # Co-approver A completes it → replay.
    h.login_as("A")
    done = api.approve_change(change_request=cr)
    assert done["kind"] == "executed"
    assert h.cr_row(cr)["status"] == "approved"
    assert frappe.db.get_value("Tree Node", _N(fx, "X"), "parent_tree_node") == _N(fx, "P2")
    assert frappe.db.get_value("Tree Event", h.cr_row(cr)["resulting_event"], "type") == "NODE_MOVED"


def test_move_authorized_both_ends_executes_no_cr(fx):
    """CHANGE_REQUEST_LIFECYCLE-072: D moves Y→Z (both inside P2) → executed, NODE_MOVED,
    no CR."""
    h.login_as("D")
    out = api.move_node(sheet=fx["sheet"], node=_N(fx, "Y"), new_parent=_N(fx, "Z"))
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "NODE_MOVED"
    assert "change_request" not in out or not out.get("change_request")


# ===========================================================================
# J/M. Event-log invariants
# ===========================================================================
def test_proposed_cr_has_only_one_lifecycle_event_until_decided(fx):
    """CHANGE_REQUEST_LIFECYCLE-080: a proposed CR has exactly one linked event
    (CHANGE_PROPOSED); resulting_event null."""
    cr = _propose_budget_update_by_E(fx)
    assert [e["type"] for e in h.events_of_cr(cr)] == ["CHANGE_PROPOSED"]
    assert not h.cr_row(cr)["resulting_event"]


def test_reproposing_after_withdrawal_makes_new_cr(fx):
    """CHANGE_REQUEST_LIFECYCLE-110: re-filing after withdrawal creates a brand-new
    CR; the withdrawn row is untouched."""
    cr1 = _propose_budget_update_by_E(fx)
    h.login_as("E")
    api.withdraw_change(change_request=cr1)
    cr2 = _propose_budget_update_by_E(fx)
    assert cr2 != cr1
    assert h.cr_row(cr1)["status"] == "withdrawn"
    assert h.cr_row(cr2)["status"] == "proposed"


def test_tree_event_log_is_append_only_across_lifecycle(fx):
    """CHANGE_REQUEST_LIFECYCLE-111: the CHANGE_PROPOSED row is unchanged after
    approval; three distinct append-only rows exist (proposed event, mutation
    event, approved event); resulting_event points at the mutation."""
    cr = _propose_budget_update_by_E(fx)
    proposed_ev = h.events_of_cr(cr)[0]
    proposed_creation = proposed_ev["creation"]
    h.login_as("C")
    api.approve_change(change_request=cr)
    # PROPOSED row not back-edited.
    again = frappe.db.get_value("Tree Event", proposed_ev["name"], ["type", "creation"], as_dict=True)
    assert again.type == "CHANGE_PROPOSED" and str(again.creation) == str(proposed_creation)
    row = h.cr_row(cr)
    distinct = {proposed_ev["name"], row["resulting_event"]}
    lifecycle = {e["name"] for e in h.events_of_cr(cr)}
    distinct |= lifecycle
    assert len(distinct) == 3  # proposed, mutation, approved


# ===========================================================================
# G. Decision-time re-resolution (contract; xfail until core re-resolves)
# ===========================================================================
def test_grant_revoked_after_proposal_reroutes_to_fallback_owner(fx):
    """CHANGE_REQUEST_LIFECYCLE-053 / PERMISSIONS_AND_DELEGATION-054: a structural CR
    (addNode under Y) filed while BG_P2 active resolves to D. After A revokes
    BG_P2, the CR must re-resolve to A: D may no longer approve, A may."""
    h.login_as("F")
    cr = api.add_node(sheet=fx["sheet"], parent=_N(fx, "Y"))["change_request"]
    assert h.cr_row(cr)["resolved_approver"] == h.user("D")
    h.login_as("A")
    api.revoke_delegation(branch_grant=fx["grant_P2"])
    # Stale approver D denied.
    h.login_as("D")
    with pytest.raises(frappe.PermissionError):
        api.approve_change(change_request=cr)
    # Re-resolved approver A may approve.
    h.login_as("A")
    out = api.approve_change(change_request=cr)
    assert out["kind"] == "executed"
    assert h.cr_row(cr)["resolved_approver"] == h.user("A")


def test_new_nearer_grant_after_proposal_reroutes_to_new_grantee(fx):
    """CHANGE_REQUEST_LIFECYCLE-054: addNode(parent=Z) CR filed when only BG_P2 (D)
    exists resolves to D. After a nearer BG_Z (D2) is created, the CR must
    re-resolve to D2: D can no longer approve."""
    h.ensure_user("D2")
    h.login_as("F")
    cr = api.add_node(sheet=fx["sheet"], parent=_N(fx, "Z"))["change_request"]
    assert h.cr_row(cr)["resolved_approver"] == h.user("D")
    h.login_as("D")
    api.delegate_branch(sheet=fx["sheet"], branch_root=_N(fx, "Z"), grantee=h.user("D2"))
    with pytest.raises(frappe.PermissionError):
        api.approve_change(change_request=cr)  # D is now stale
    h.login_as("D2")
    out = api.approve_change(change_request=cr)
    assert out["kind"] == "executed"


def test_removed_editor_can_no_longer_approve(fx):
    """CHANGE_REQUEST_LIFECYCLE-055: after grantColumn removes B as editor of
    col:status, B may no longer approve a pending col:status CR; only C may."""
    h.login_as("E")
    cr = api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "status"), value="z")["change_request"]
    # C reassigns col:status editors to empty (removes B).
    h.login_as("C")
    api.grant_column(sheet=fx["sheet"], column=_C(fx, "status"), column_owner=h.user("C"), editors=[])
    h.login_as("B")
    with pytest.raises(frappe.PermissionError):
        api.approve_change(change_request=cr)
