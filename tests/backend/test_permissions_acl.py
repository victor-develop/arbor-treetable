"""Backend integration — two-axis ACL, delegation & suggest-routing.

runnable: NEEDS FRAPPE BENCH (``@pytest.mark.bench``; auto-skipped when frappe is
absent). Runs the REAL adapter end to end: every case calls a whitelisted
``arbor.api.*`` method as a persona and asserts the governance keystone — an
authorized action MUTATES + emits the capability's own Tree Event, an
unauthorized action becomes a Change Request routed to the RESOLVED approver +
emits exactly one ``CHANGE_PROPOSED`` (never both, never zero).

Highest-value cases translated from ``tests/permissions-and-delegation.md``:

* Axis 1 ancestor-walk + delegation routing + nearest-grant-wins
  (PERMISSIONS_AND_DELEGATION-001/002/004/006/007/008/009/040).
* moveNode dual-end authority + single-CR co-approver
  (PERMISSIONS_AND_DELEGATION-012/013/014/015/016).
* Axis 2 column authority + axis independence
  (PERMISSIONS_AND_DELEGATION-018/019/021/022/023/025).
* Delegation lifecycle effect on routing
  (PERMISSIONS_AND_DELEGATION-033/035/038).
* Owner-self policy (PERMISSIONS_AND_DELEGATION-056/059).

Bind points: ``arbor.api`` (REST funnel), the canonical seed
``arbor.adapter.seed.seed_canonical_sheet`` (== pure fixture), and the Tree Event
rows written by ``FrappeEventSink``.

Run::

    bench --site <site> run-tests --module tests.backend.test_permissions_acl
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.bench

frappe = pytest.importorskip("frappe")

from arbor import api  # noqa: E402  (after importorskip)

from tests.backend import _helpers as h  # noqa: E402


@pytest.fixture()
def fx():
    """Canonical sheet `S`, default settings, rolled back per test by the bench
    harness's transaction."""
    data = h.seed()
    yield data
    frappe.set_user("Administrator")


def _N(fx, label):
    return fx["nodes"][label]


def _C(fx, field):
    return fx["columns"][field]


# ===========================================================================
# Section 1 — Axis 1: structural authority & ancestor-walk resolution
# ===========================================================================
def test_root_owner_adds_node_under_own_branch_executes(fx):
    """PERMISSIONS_AND_DELEGATION-001: A adds under P1 (no grant on chain) → executed,
    NODE_CREATED, exactly one event."""
    h.login_as("A")
    before = h.event_count(fx["sheet"])
    out = api.add_node(sheet=fx["sheet"], parent=_N(fx, "P1"))
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "NODE_CREATED"
    assert h.event_count(fx["sheet"]) == before + 1  # exactly one event


def test_root_owner_deletes_deep_node_executes(fx):
    """PERMISSIONS_AND_DELEGATION-002: A deletes X (walk X→P1→R, no grant) → executed."""
    h.login_as("A")
    out = api.delete_node(sheet=fx["sheet"], node=_N(fx, "X"))
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "NODE_DELETED"
    assert not frappe.db.exists("Tree Node", _N(fx, "X"))


def test_delegated_owner_adds_inside_own_branch_executes(fx):
    """PERMISSIONS_AND_DELEGATION-004: D adds under Y (inside P2, nearest grant BG_P2)
    → executed."""
    h.login_as("D")
    out = api.add_node(sheet=fx["sheet"], parent=_N(fx, "Y"))
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "NODE_CREATED"


def test_delegation_is_subtree_scoped_D_outside_P2_suggests_to_A(fx):
    """PERMISSIONS_AND_DELEGATION-006: D acting OUTSIDE P2 (addNode under P1) →
    CR routed to A; no node created."""
    h.login_as("D")
    before = frappe.db.count("Tree Node", {"sheet": fx["sheet"]})
    out = api.add_node(sheet=fx["sheet"], parent=_N(fx, "P1"))
    assert out["kind"] == "suggested"
    assert out["event"]["type"] == "CHANGE_PROPOSED"
    cr = h.cr_row(out["change_request"])
    assert cr["resolved_approver"] == h.user("A")
    assert cr["target_kind"] == "node-structure" and cr["operation"] == "add"
    assert frappe.db.count("Tree Node", {"sheet": fx["sheet"]}) == before  # no node


def test_non_owner_under_P2_routes_to_delegated_owner_not_root(fx):
    """PERMISSIONS_AND_DELEGATION-007: F adds under P2 → CR to D (delegation wins
    over root A)."""
    h.login_as("F")
    out = api.add_node(sheet=fx["sheet"], parent=_N(fx, "P2"))
    assert out["kind"] == "suggested"
    assert h.cr_row(out["change_request"])["resolved_approver"] == h.user("D")


def test_non_owner_delete_under_A_branch_routes_to_A(fx):
    """PERMISSIONS_AND_DELEGATION-008: E deletes X (walk X→P1→R, no grant) → CR to A;
    X still present."""
    h.login_as("E")
    out = api.delete_node(sheet=fx["sheet"], node=_N(fx, "X"))
    assert out["kind"] == "suggested"
    cr = h.cr_row(out["change_request"])
    assert cr["resolved_approver"] == h.user("A") and cr["operation"] == "delete"
    assert frappe.db.exists("Tree Node", _N(fx, "X"))


def test_nearest_grant_wins_nested_delegation(fx):
    """PERMISSIONS_AND_DELEGATION-009/040: with BG_Z (grantee D2) nested under BG_P2,
    a structural change on a child of Z routes to D2, not D, not A."""
    h.ensure_user("D2")
    # D sub-delegates Z to D2 (PERMISSIONS-038 path).
    h.login_as("D")
    api.delegate_branch(sheet=fx["sheet"], branch_root=_N(fx, "Z"), grantee=h.user("D2"))
    # F proposes a structural add under Z → nearest active grant is BG_Z → D2.
    h.login_as("F")
    out = api.add_node(sheet=fx["sheet"], parent=_N(fx, "Z"))
    assert out["kind"] == "suggested"
    assert h.cr_row(out["change_request"])["resolved_approver"] == h.user("D2")
    # Sibling under P2 (Y) still routes to the outer grantee D (boundary precision).
    out_y = api.add_node(sheet=fx["sheet"], parent=_N(fx, "Y"))
    assert h.cr_row(out_y["change_request"])["resolved_approver"] == h.user("D")


# ===========================================================================
# Section 2 — moveNode: dual-end (src + dest) authority + co-approver
# ===========================================================================
def test_move_within_own_region_executes_both_ends(fx):
    """PERMISSIONS_AND_DELEGATION-012: A moves X (src P1→A, dest R→A) → executed."""
    h.login_as("A")
    out = api.move_node(sheet=fx["sheet"], node=_N(fx, "X"), new_parent=_N(fx, "R"))
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "NODE_MOVED"


def test_move_into_other_branch_routes_to_dest_with_src_co_approver(fx):
    """PERMISSIONS_AND_DELEGATION-013: A moves X into P2 (src A, dest D) → single CR
    routed to dest D with payload.co_approvers including src A; X not moved."""
    h.login_as("A")
    before_parent = frappe.db.get_value("Tree Node", _N(fx, "X"), "parent_tree_node")
    out = api.move_node(sheet=fx["sheet"], node=_N(fx, "X"), new_parent=_N(fx, "P2"))
    assert out["kind"] == "suggested"
    cr = h.cr_row(out["change_request"])
    assert cr["resolved_approver"] == h.user("D")  # dest is the routing target
    assert cr["target_kind"] == "node-structure" and cr["operation"] == "move"
    assert h.user("A") in (h.cr_payload(out["change_request"]).get("co_approvers") or [])
    # X not moved.
    assert frappe.db.get_value("Tree Node", _N(fx, "X"), "parent_tree_node") == before_parent


def test_move_out_of_own_branch_routes_to_dest_owner(fx):
    """PERMISSIONS_AND_DELEGATION-014: D moves Y out of P2 into P1 (src D, dest A) →
    CR to A with co_approver D."""
    h.login_as("D")
    out = api.move_node(sheet=fx["sheet"], node=_N(fx, "Y"), new_parent=_N(fx, "P1"))
    assert out["kind"] == "suggested"
    cr = h.cr_row(out["change_request"])
    assert cr["resolved_approver"] == h.user("A")
    assert h.user("D") in (h.cr_payload(out["change_request"]).get("co_approvers") or [])


def test_move_within_delegated_subtree_executes(fx):
    """PERMISSIONS_AND_DELEGATION-015: D moves Z under Y (both ends inside P2 → D) →
    executed."""
    h.login_as("D")
    out = api.move_node(sheet=fx["sheet"], node=_N(fx, "Z"), new_parent=_N(fx, "Y"))
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "NODE_MOVED"


def test_column_owner_has_no_structural_authority_for_move(fx):
    """PERMISSIONS_AND_DELEGATION-016: C (column owner only) moves Y→P1 → CR to dest A,
    co_approvers=[D]; column authority confers no structural authority."""
    h.login_as("C")
    out = api.move_node(sheet=fx["sheet"], node=_N(fx, "Y"), new_parent=_N(fx, "P1"))
    assert out["kind"] == "suggested"
    cr = h.cr_row(out["change_request"])
    assert cr["resolved_approver"] == h.user("A")
    assert h.user("D") in (h.cr_payload(out["change_request"]).get("co_approvers") or [])


# ===========================================================================
# Section 3 — Axis 2: column authority (owner + editors) & axis independence
# ===========================================================================
def test_column_owner_edits_label_inside_other_branch_executes(fx):
    """PERMISSIONS_AND_DELEGATION-018: B (owner col:name) edits col:name on Z (in D's
    branch) → executed; Axis 2 ignores structure."""
    h.login_as("B")
    out = api.update_cell(sheet=fx["sheet"], node=_N(fx, "Z"), column=_C(fx, "name"), value="new")
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "NODE_VALUE_UPDATED"
    assert h.cell_value(_N(fx, "Z"), _C(fx, "name")) == "new"


def test_editor_edits_column_executes(fx):
    """PERMISSIONS_AND_DELEGATION-019: B (editor on col:status) edits col:status → executed
    (editors are approvers)."""
    h.login_as("B")
    out = api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "status"), value="done")
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "NODE_VALUE_UPDATED"


def test_column_owner_edits_foreign_column_suggests_to_owner(fx):
    """PERMISSIONS_AND_DELEGATION-021: B edits col:budget (C's) → CR to C; cell unchanged."""
    h.login_as("B")
    before = h.cell_value(_N(fx, "X"), _C(fx, "budget"))
    out = api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "budget"), value=50)
    assert out["kind"] == "suggested"
    cr = h.cr_row(out["change_request"])
    assert cr["resolved_approver"] == h.user("C")
    assert cr["target_kind"] == "cell-value" and cr["operation"] == "update"
    assert h.cell_value(_N(fx, "X"), _C(fx, "budget")) == before


def test_pending_suggestion_is_visible_per_cell_to_other_readers(fx):
    """A suggested edit shows up as a per-cell ``pending`` mark in the snapshot
    for ANOTHER reader — not just the suggester's session (so it survives refresh
    and Dev-B-style viewers see it). B suggests col:budget on X (→ CR to C); A, a
    different user, reads the snapshot and sees X.pending[col:budget] carrying the
    CR id, requester=B and the proposed value, while the committed value stays."""
    h.login_as("B")
    out = api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "budget"), value=50)
    assert out["kind"] == "suggested"
    cr = out["change_request"]

    h.login_as("A")  # a different user entirely
    snap = api.get_sheet_snapshot(sheet=fx["sheet"])
    x = next(n for n in snap["nodes"] if n["name"] == _N(fx, "X"))
    marks = x["pending"].get(_C(fx, "budget"))
    assert marks, "another reader should see the pending suggestion on the cell"
    assert marks[0]["change_request"] == cr
    assert marks[0]["requester"] == h.user("B")
    assert marks[0]["value"] == 50
    # committed value unchanged (suggestion not applied); other cells carry none.
    assert x["values"][_C(fx, "budget")] == 1000  # canonical seed value for X
    y = next(n for n in snap["nodes"] if n["name"] == _N(fx, "Y"))
    assert _C(fx, "budget") not in y["pending"]


def test_structural_owner_has_no_column_authority(fx):
    """PERMISSIONS_AND_DELEGATION-022: A (sheet owner, no columns) edits col:budget →
    CR to C; structural authority != column authority."""
    h.login_as("A")
    out = api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "budget"), value=7)
    assert out["kind"] == "suggested"
    assert h.cr_row(out["change_request"])["resolved_approver"] == h.user("C")


def test_delegated_owner_has_no_column_authority_in_own_branch(fx):
    """PERMISSIONS_AND_DELEGATION-023: D edits col:status on Y (own branch) → CR to C;
    structural delegation gives no value-edit authority."""
    h.login_as("D")
    out = api.update_cell(sheet=fx["sheet"], node=_N(fx, "Y"), column=_C(fx, "status"), value="x")
    assert out["kind"] == "suggested"
    assert h.cr_row(out["change_request"])["resolved_approver"] == h.user("C")


def test_label_edit_routes_to_label_column_owner_not_branch_owner(fx):
    """PERMISSIONS_AND_DELEGATION-025: D (structurally owns Y) edits Y's label
    (col:name) → CR to B (label column owner), not D."""
    h.login_as("D")
    out = api.update_cell(sheet=fx["sheet"], node=_N(fx, "Y"), column=_C(fx, "name"), value="renamed")
    assert out["kind"] == "suggested"
    assert h.cr_row(out["change_request"])["resolved_approver"] == h.user("B")


# ===========================================================================
# Section 5 — Delegation lifecycle & its effect on routing
# ===========================================================================
def test_delegate_then_revoke_shifts_structural_routing(fx):
    """PERMISSIONS_AND_DELEGATION-035: A revokes BG_P2 → DELEGATION_CHANGED; P2 authority
    falls back to A. After revoke, F's add under P2 routes to A, not D."""
    h.login_as("A")
    out = api.revoke_delegation(branch_grant=fx["grant_P2"])
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "DELEGATION_CHANGED"
    assert frappe.db.get_value("Branch Grant", fx["grant_P2"], "active") in (0, "0", False)
    # routing now collapses to root A
    h.login_as("F")
    out2 = api.add_node(sheet=fx["sheet"], parent=_N(fx, "P2"))
    assert h.cr_row(out2["change_request"])["resolved_approver"] == h.user("A")


def test_subdelegation_within_own_subtree_executes(fx):
    """PERMISSIONS_AND_DELEGATION-038: D sub-delegates Z→D2 (resolve_structural_approver(Z)=D)
    → executed; an active grant on Z is created."""
    h.ensure_user("D2")
    h.login_as("D")
    out = api.delegate_branch(sheet=fx["sheet"], branch_root=_N(fx, "Z"), grantee=h.user("D2"))
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "DELEGATION_CHANGED"
    assert frappe.db.exists(
        "Branch Grant",
        {"sheet": fx["sheet"], "branch_root": _N(fx, "Z"), "grantee": h.user("D2"), "active": 1},
    )


# ===========================================================================
# Section 8 — Owner-self policy (owners_must_use_change_requests)
# ===========================================================================
def test_owner_self_policy_forces_cr_for_authorized_owner():
    """PERMISSIONS_AND_DELEGATION-056: with owners_must_use_change_requests=true, C's
    authorized updateCell still yields a self-approver CR; cell unchanged."""
    fx = h.seed(settings={"owners_must_use_change_requests": True})
    try:
        h.login_as("C")
        before = h.cell_value(fx["nodes"]["X"], fx["columns"]["budget"])
        out = api.update_cell(
            sheet=fx["sheet"], node=fx["nodes"]["X"], column=fx["columns"]["budget"], value=4
        )
        assert out["kind"] == "suggested"
        assert out["event"]["type"] == "CHANGE_PROPOSED"
        cr = h.cr_row(out["change_request"])
        assert cr["resolved_approver"] == h.user("C") and cr["requester"] == h.user("C")
        assert h.cell_value(fx["nodes"]["X"], fx["columns"]["budget"]) == before
    finally:
        frappe.set_user("Administrator")


def test_default_policy_owner_action_mutates_directly(fx):
    """PERMISSIONS_AND_DELEGATION-059 (control for -056): flag false → C mutates directly,
    no CR."""
    h.login_as("C")
    out = api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "budget"), value=4)
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "NODE_VALUE_UPDATED"
    assert h.cell_value(_N(fx, "X"), _C(fx, "budget")) == 4
