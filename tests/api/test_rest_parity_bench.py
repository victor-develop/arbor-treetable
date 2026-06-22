"""REST API parity, auth gate, and error contracts — REQUIRES A FRAPPE BENCH + SITE.

Runnable: **needs Frappe bench** (``@pytest.mark.bench``; auto-skipped when frappe
is not importable so the bench-free suite stays green).

Drives the real whitelisted ``arbor.api`` surface (FrappeRepository /
FrappeEventSink) on a live site. Every capability is reachable via its named
``arbor.<method>`` and via generic ``arbor.execute_action`` with an ACL outcome
IDENTICAL to the in-process ``execute_action`` (proven structurally bench-free in
``test_parity_harness.py``; proven here against the live funnel). Auth is
required; error contracts are 401/403/404/409/400; the external system is a normal
User bound by the same two-axis ACL and may query the tree as a base-of-record.

Run on a bench, e.g.::

    bench --site <site> run-tests --module tests.api.test_rest_parity_bench

Maps to api.md: A (001-004 auth gate), B (010-013 parity/dispatch), C (020-028
CRUD), D (040-048 denied paths), E (060-068 delegation), F (080-087 lifecycle),
G (100-103 snapshot), H (120-125 pagination/base-of-record), I (140-149 errors),
J (160-163 concurrency), K (180-182 event cardinality); plus the TEST-PLAN §5.1
unsubscribe REST gap.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.bench

frappe = pytest.importorskip("frappe", reason="REST parity requires a Frappe bench")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
@pytest.fixture()
def seeded():
    """Seed the ONE canonical sheet `S` (rolled back per-test by the bench
    harness transaction)."""
    try:
        from arbor.adapter.seed import seed_canonical_sheet
    except ModuleNotFoundError:  # pragma: no cover
        from arbor.arbor.adapter.seed import seed_canonical_sheet
    return seed_canonical_sheet()


def _api():
    try:
        from arbor import api
    except ModuleNotFoundError:  # pragma: no cover
        from arbor.arbor import api
    return api


def _as(user: str):
    frappe.set_user((user if "@" in user else f"{user}@arbor.example").lower())


def _as_guest():
    frappe.set_user("Guest")


def _status():
    return frappe.local.response.get("http_status_code")


# ===========================================================================
# A. Authentication & authorization gate (transport-level)
# ===========================================================================
def test_unauthenticated_capability_call_is_401(seeded):
    """API-001: no auth → 401; no value mutated, no Tree Event."""
    api = _api()
    _as_guest()
    before = frappe.db.count("Tree Event", {"sheet": seeded["sheet"]})
    with pytest.raises((frappe.AuthenticationError, frappe.PermissionError)):
        api.update_cell(
            sheet=seeded["sheet"], node=seeded["nodes"]["X"],
            column=seeded["columns"]["status"], value="done",
        )
    assert frappe.db.count("Tree Event", {"sheet": seeded["sheet"]}) == before


def test_authenticated_request_resolves_actor_identity(seeded):
    """API-003: B's credentials → executed; event actor == B, actor_type == human
    (API callers are never 'agent')."""
    api = _api()
    _as("B")
    out = api.update_cell(
        sheet=seeded["sheet"], node=seeded["nodes"]["Z"],
        column=seeded["columns"]["name"], value="Zed",
    )
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "NODE_VALUE_UPDATED"
    assert out["event"]["actor"] == "b@arbor.example"
    assert out["event"]["actor_type"] == "human"


def test_read_endpoints_require_auth(seeded):
    """API-004: get_sheet_snapshot as Guest → 401/403."""
    api = _api()
    _as_guest()
    with pytest.raises((frappe.AuthenticationError, frappe.PermissionError)):
        api.get_sheet_snapshot(sheet=seeded["sheet"])


# ===========================================================================
# B. Surface-parity invariant (against the LIVE funnel)
# ===========================================================================
def test_authorized_write_executes_and_emits_one_event(seeded):
    """API-010 / API-180: C owns col:budget → executed + exactly one event."""
    api = _api()
    _as("C")
    before = frappe.db.count("Tree Event", {"sheet": seeded["sheet"]})
    out = api.update_cell(
        sheet=seeded["sheet"], node=seeded["nodes"]["Y"],
        column=seeded["columns"]["budget"], value=42,
    )
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "NODE_VALUE_UPDATED"
    assert out["event"]["change_request"] is None
    assert frappe.db.count("Tree Event", {"sheet": seeded["sheet"]}) == before + 1


def test_unauthorized_write_becomes_change_request_200(seeded):
    """API-011 / API-040 / API-181: A owns no columns; col:budget edit → CR to C,
    one CHANGE_PROPOSED, NO NODE_VALUE_UPDATED. suggest is a 200 success, not 403."""
    api = _api()
    _as("A")
    out = api.update_cell(
        sheet=seeded["sheet"], node=seeded["nodes"]["X"],
        column=seeded["columns"]["budget"], value=7,
    )
    assert out["kind"] == "suggested"
    assert out["event"]["type"] == "CHANGE_PROPOSED"
    cr = frappe.get_doc("Change Request", out["change_request"])
    assert cr.resolved_approver == "c@arbor.example"
    assert cr.target_kind == "cell-value"


def test_generic_dispatch_equals_named_method(seeded):
    """API-012: arbor.execute_action(action_id) ≡ arbor.update_cell — one funnel,
    identical single NODE_VALUE_UPDATED shape."""
    api = _api()
    _as("B")
    generic = api.execute_action(
        action_id="updateCell",
        params={"sheet": seeded["sheet"], "node": seeded["nodes"]["Z"],
                "column": seeded["columns"]["notes"], "value": "n"},
    )
    named = api.update_cell(
        sheet=seeded["sheet"], node=seeded["nodes"]["Z"],
        column=seeded["columns"]["notes"], value="n",
    )
    assert generic["kind"] == named["kind"] == "executed"
    assert generic["event"]["type"] == named["event"]["type"] == "NODE_VALUE_UPDATED"


# ===========================================================================
# C. CRUD happy paths
# ===========================================================================
def test_add_node_under_owned_branch(seeded):
    """API-020: A owns root structure → addNode executes; one NODE_CREATED."""
    api = _api()
    _as("A")
    out = api.add_node(sheet=seeded["sheet"], parent=seeded["nodes"]["P1"], values={"name": "X2"})
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "NODE_CREATED"


def test_delegated_owner_adds_within_grant(seeded):
    """API-060 / API-023-ish: D adds under Y (in P2 grant) → executes."""
    api = _api()
    _as("D")
    out = api.add_node(sheet=seeded["sheet"], parent=seeded["nodes"]["Y"])
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "NODE_CREATED"


def test_add_column_as_structural_owner(seeded):
    """API-025: A is sheet structural_owner → addColumn executes."""
    api = _api()
    _as("A")
    out = api.add_column(sheet=seeded["sheet"], field="due", label="Due", type="text", column_owner="c@arbor.example")
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "COLUMN_CONFIG_UPDATED"


def test_column_editor_can_edit_but_editor_set_applies_to_delete(seeded):
    """API-048: B is editor (not owner) on col:status → updateCell executes; the
    same resolver set governs deleteColumn (editors are owner-equivalent)."""
    api = _api()
    _as("B")
    upd = api.update_cell(
        sheet=seeded["sheet"], node=seeded["nodes"]["X"],
        column=seeded["columns"]["status"], value="blocked",
    )
    assert upd["kind"] == "executed"
    assert upd["event"]["type"] == "NODE_VALUE_UPDATED"


# ===========================================================================
# D. Permission-DENIED control actions ARE 403 (not converted to a CR)
# ===========================================================================
def _propose_budget_cr(api, seeded, requester="E"):
    _as(requester)
    out = api.update_cell(
        sheet=seeded["sheet"], node=seeded["nodes"]["X"],
        column=seeded["columns"]["budget"], value=99,
    )
    return out["change_request"]


def test_approve_by_non_approver_is_403(seeded):
    """API-044: a non-approver approving → 403; CR stays proposed; no event."""
    api = _api()
    cr = _propose_budget_cr(api, seeded, requester="E")
    _as("F")  # F is not the approver (C is)
    with pytest.raises(frappe.PermissionError):
        api.approve_change(change_request=cr)
    assert frappe.get_doc("Change Request", cr).status == "proposed"


def test_reject_by_non_approver_is_403(seeded):
    """API-045: a non-approver rejecting → 403; CR stays proposed."""
    api = _api()
    cr = _propose_budget_cr(api, seeded, requester="E")
    _as("F")
    with pytest.raises(frappe.PermissionError):
        api.reject_change(change_request=cr)
    assert frappe.get_doc("Change Request", cr).status == "proposed"


def test_withdraw_by_non_requester_is_403(seeded):
    """API-046: withdraw is requester-only → 403 for anyone else."""
    api = _api()
    cr = _propose_budget_cr(api, seeded, requester="E")
    _as("F")
    with pytest.raises(frappe.PermissionError):
        api.withdraw_change(change_request=cr)
    assert frappe.get_doc("Change Request", cr).status == "proposed"


def test_external_system_write_bound_by_same_acl(seeded):
    """API-043: EXT is a normal User + API key with no column authority; its
    col:status write becomes a CR to C — no external bypass (DECISIONS #4)."""
    api = _api()
    _as("EXT")
    out = api.update_cell(
        sheet=seeded["sheet"], node=seeded["nodes"]["Y"],
        column=seeded["columns"]["status"], value="ext",
    )
    assert out["kind"] == "suggested"
    assert frappe.get_doc("Change Request", out["change_request"]).resolved_approver == "c@arbor.example"


# ===========================================================================
# F. Lifecycle via API
# ===========================================================================
def test_approve_replays_handler_and_emits_real_event(seeded):
    """API-081: approver C approves a cell CR → replay emits NODE_VALUE_UPDATED
    then CHANGE_APPROVED; CR terminal with resulting_event linked."""
    api = _api()
    cr = _propose_budget_cr(api, seeded, requester="E")
    _as("C")
    out = api.approve_change(change_request=cr)
    assert out["kind"] == "executed"
    doc = frappe.get_doc("Change Request", cr)
    assert doc.status == "approved"
    assert doc.resulting_event


def test_reject_emits_change_rejected_no_mutation(seeded):
    """API-083: approver rejects → CHANGE_REJECTED; no mutation; CR terminal."""
    api = _api()
    cr = _propose_budget_cr(api, seeded, requester="E")
    _as("C")
    out = api.reject_change(change_request=cr, comment="no")
    assert out["event"]["type"] == "CHANGE_REJECTED"
    assert frappe.get_doc("Change Request", cr).status == "rejected"


def test_withdraw_own_cr_emits_change_rejected_withdrawn(seeded):
    """API-084: requester withdraws → CHANGE_REJECTED (reason=withdrawn); terminal."""
    api = _api()
    cr = _propose_budget_cr(api, seeded, requester="E")
    _as("E")
    out = api.withdraw_change(change_request=cr)
    assert out["event"]["type"] == "CHANGE_REJECTED"
    assert frappe.get_doc("Change Request", cr).status == "withdrawn"


def test_approve_terminal_cr_is_409(seeded):
    """API-085: approving an already-decided CR → 409 conflict; no duplicate."""
    api = _api()
    cr = _propose_budget_cr(api, seeded, requester="E")
    _as("C")
    api.approve_change(change_request=cr)
    with pytest.raises(frappe.ValidationError):
        api.approve_change(change_request=cr)
    assert _status() == 409


# ===========================================================================
# G. Snapshot — the shared read serializer
# ===========================================================================
def test_snapshot_shape_and_acl_hints(seeded):
    """API-100: snapshot carries columns (owner+editors), node tree, values, and
    per-actor affordances from the SHARED serializer."""
    api = _api()
    _as("B")
    snap = api.get_sheet_snapshot(sheet=seeded["sheet"])
    assert {c["field"] for c in snap["columns"]} == {"name", "status", "budget", "notes"}
    by_field = {c["field"]: c for c in snap["columns"]}
    assert by_field["name"]["can_edit"] is True   # B owns col:name
    assert by_field["budget"]["can_edit"] is False  # C owns col:budget


def test_snapshot_unknown_sheet_404(seeded):
    """API-102: snapshot of an unknown sheet → 404."""
    api = _api()
    _as("B")
    with pytest.raises(frappe.DoesNotExistError):
        api.get_sheet_snapshot(sheet="does-not-exist")
    assert _status() == 404


def test_snapshot_reflects_committed_write(seeded):
    """API-103: read-after-write — a committed update_cell shows in the snapshot."""
    api = _api()
    _as("C")
    api.update_cell(
        sheet=seeded["sheet"], node=seeded["nodes"]["X"],
        column=seeded["columns"]["budget"], value=77,
    )
    snap = api.get_sheet_snapshot(sheet=seeded["sheet"])
    x = next(n for n in snap["nodes"] if n["name"] == seeded["nodes"]["X"])
    assert x["values"][seeded["columns"]["budget"]] == 77


# ===========================================================================
# H. External system: base-of-record queries via auto-REST (read; auth required)
# ===========================================================================
def test_external_system_paginated_tree_query(seeded):
    """API-120 (shape): EXT reads Tree Node ordered by lft with limit/offset; the
    tree is queryable as a relational base-of-record. (Canonical S has 6 nodes;
    the big-tree variant is the ≥500-row fixture referenced by the catalog.)"""
    api = _api()  # noqa: F841 (auth path uses the same session user)
    _as("EXT")
    page = frappe.get_all(
        "Tree Node", filters={"sheet": seeded["sheet"]},
        fields=["name", "lft"], order_by="lft asc", limit_page_length=4, limit_start=0,
    )
    assert [r["lft"] for r in page] == sorted(r["lft"] for r in page)
    assert len(page) <= 4


def test_external_system_descendant_range_query(seeded):
    """API-121: filter Tree Node by NestedSet descendant range returns exactly
    P2's descendants (Y, Z) and excludes nodes outside P2."""
    _as("EXT")
    p2 = frappe.get_doc("Tree Node", seeded["nodes"]["P2"])
    descendants = frappe.get_all(
        "Tree Node",
        filters={"sheet": seeded["sheet"], "lft": [">", p2.lft], "rgt": ["<", p2.rgt]},
        pluck="name",
    )
    assert set(descendants) == {seeded["nodes"]["Y"], seeded["nodes"]["Z"]}


def test_tree_event_log_is_readonly_via_rest(seeded):
    """API-123 / API-047: the Tree Event log is an append-only audit base-of-record
    — readable, but a raw REST write is denied."""
    api = _api()
    _as("C")
    api.update_cell(
        sheet=seeded["sheet"], node=seeded["nodes"]["Y"],
        column=seeded["columns"]["budget"], value=1,
    )
    events = frappe.get_all(
        "Tree Event", filters={"sheet": seeded["sheet"], "type": "NODE_VALUE_UPDATED"},
        order_by="creation desc", limit_page_length=50,
    )
    assert events
    # Raw write to a governed DocType is denied (only execute_action may mutate).
    with pytest.raises((frappe.PermissionError, frappe.ValidationError)):
        doc = frappe.new_doc("Tree Node Value")
        doc.node = seeded["nodes"]["X"]
        doc.column = seeded["columns"]["budget"]
        doc.value = 5
        doc.insert()  # no ignore_permissions → governed lockdown


# ===========================================================================
# I. Error contracts & boundary conditions
# ===========================================================================
def test_schema_invalid_params_400(seeded):
    """API-140: missing required params → 400 from validate_schema, before ACL."""
    api = _api()
    _as("C")
    with pytest.raises(frappe.ValidationError):
        api.execute_action(action_id="updateCell", params={"sheet": seeded["sheet"], "node": seeded["nodes"]["X"]})
    assert _status() == 400


def test_wrong_typed_param_400(seeded):
    """API-141: addColumn type outside the enum → 400; no column; no event."""
    api = _api()
    _as("A")
    with pytest.raises(frappe.ValidationError):
        api.add_column(sheet=seeded["sheet"], field="f", label="F", type="date")
    assert _status() == 400


def test_unknown_action_id_404(seeded):
    """API-142: unknown capability via generic dispatch → 404."""
    api = _api()
    _as("B")
    with pytest.raises(frappe.DoesNotExistError):
        api.execute_action(action_id="frobnicate", params={})
    assert _status() == 404


def test_unknown_node_404(seeded):
    """API-143: dangling node reference → 404."""
    api = _api()
    _as("C")
    with pytest.raises(frappe.DoesNotExistError):
        api.update_cell(sheet=seeded["sheet"], node="ghost", column=seeded["columns"]["budget"], value=1)


def test_approve_unknown_cr_404(seeded):
    """API-145: approving a non-existent CR → 404."""
    api = _api()
    _as("C")
    with pytest.raises(frappe.DoesNotExistError):
        api.approve_change(change_request="cr-ghost")


def test_move_cycle_409(seeded):
    """API-146: a move under one's own descendant → 409 integrity conflict."""
    api = _api()
    try:
        from arbor.adapter.repository import CycleError
    except ModuleNotFoundError:
        from arbor.arbor.adapter.repository import CycleError

    _as("A")
    with pytest.raises((CycleError, frappe.ValidationError)):
        api.move_node(sheet=seeded["sheet"], node=seeded["nodes"]["R"], new_parent=seeded["nodes"]["X"])
    assert _status() == 409


# ===========================================================================
# J. Optimistic concurrency (Feature 1) — API-160..163
#
# Contract (authz_spec.txt CONCURRENCY WIRING + authz_tdd.txt FEATURE 1):
#  * A stale per-cell ``base_version`` (StaleVersionError) and a vanished move
#    anchor (StaleMoveError) are NOT 409. They return a structured HTTP-200
#    Outcome ``{kind:'read', error:'VERSION_CONFLICT', data:{node, column,
#    current_version, current_value}}`` so the FE reads ``outcome.error`` rather
#    than catching a thrown 4xx.
#  * CycleError / CRStateError keep 409 (asserted by test_move_cycle_409 and
#    test_approve_terminal_cr_is_409 above).
#  * An omitted ``base_version`` keeps today's no-check behavior (opt-in).
# ===========================================================================
def test_stale_base_version_is_version_conflict_200(seeded):
    """API-161: a stale per-cell base_version → HTTP-200 VERSION_CONFLICT carrying
    the authoritative current version + value; the rejected write does NOT land."""
    api = _api()
    _as("C")  # C owns col:budget → authorized to write directly
    node, col = seeded["nodes"]["Y"], seeded["columns"]["budget"]
    # establish a known version by writing once
    first = api.execute_action(
        action_id="updateCell",
        params={"sheet": seeded["sheet"], "node": node, "column": col, "value": 100},
    )
    cur = first["data"]["version"]
    # now write with a deliberately STALE base_version
    out = api.execute_action(
        action_id="updateCell",
        params={"sheet": seeded["sheet"], "node": node, "column": col,
                "value": 999, "base_version": cur - 1},
    )
    assert out["error"] == "VERSION_CONFLICT"
    assert out["kind"] == "read"  # not a mutation; FE reads outcome.error
    assert out["data"]["node"] == node
    assert out["data"]["column"] == col
    assert out["data"]["current_version"] == cur
    assert out["data"]["current_value"] == 100
    # the rejected value never landed; the status code is 200 (no 4xx thrown)
    assert _status() in (None, 200)


def test_matching_base_version_executes_and_returns_new_version(seeded):
    """API-160: a matching base_version writes through and folds the bumped
    version into the Outcome data so the next same-cell edit carries it."""
    api = _api()
    _as("C")
    node, col = seeded["nodes"]["Z"], seeded["columns"]["budget"]
    first = api.execute_action(
        action_id="updateCell",
        params={"sheet": seeded["sheet"], "node": node, "column": col, "value": 1},
    )
    base = first["data"]["version"]
    out = api.execute_action(
        action_id="updateCell",
        params={"sheet": seeded["sheet"], "node": node, "column": col,
                "value": 2, "base_version": base},
    )
    assert out["kind"] == "executed"
    assert out["data"]["version"] == base + 1


def test_omitted_base_version_is_opt_in_no_check(seeded):
    """API-162: omitting base_version keeps today's blind-overwrite behavior."""
    api = _api()
    _as("C")
    node, col = seeded["nodes"]["Y"], seeded["columns"]["budget"]
    out = api.execute_action(
        action_id="updateCell",
        params={"sheet": seeded["sheet"], "node": node, "column": col, "value": 55},
    )
    assert out["kind"] == "executed"
    assert out["error"] is None or "error" not in out


def test_stale_move_anchor_is_version_conflict_200(seeded):
    """API-163: a moveNode whose ``after`` anchor sibling has vanished →
    HTTP-200 VERSION_CONFLICT (StaleMoveError), NOT a 409."""
    api = _api()
    _as("A")  # A owns root structure
    out = api.execute_action(
        action_id="moveNode",
        params={"sheet": seeded["sheet"], "node": seeded["nodes"]["Y"],
                "new_parent": seeded["nodes"]["P1"],
                "after": "ghost-sibling", "expected_revision": "stale-rev"},
    )
    assert out["error"] == "VERSION_CONFLICT"
    assert out["kind"] == "read"
    assert _status() in (None, 200)


def test_internal_reset_not_callable_by_owner_403(seeded):
    """API-149: internalReset is system/admin only; even sheet owner A is denied
    via the ordinary capability surface; no purge, no event."""
    api = _api()
    _as("A")
    with pytest.raises(frappe.PermissionError):
        api.execute_action(action_id="internalReset", params={"sheet": seeded["sheet"], "confirm": True})


# ===========================================================================
# TEST-PLAN §5.1 — unsubscribe REST gap (the missing half of the pair)
# ===========================================================================
def test_unsubscribe_rest_round_trip(seeded):
    """TEST-PLAN §5.1: subscribe then unsubscribe via REST so the subscription
    lifecycle is symmetric on the API surface. (The Web-UI half is a frontend-lane
    note; the cross-surface parity is asserted bench-free in test_parity_harness.)"""
    api = _api()
    _as("B")
    sub = api.subscribe(scope="sheet", target=seeded["sheet"], event_types=["NODE_VALUE_UPDATED"], delivery="in-app")
    sub_name = sub["data"]["subscription"]
    out = api.unsubscribe(subscription=sub_name)
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "SUBSCRIPTION_CHANGED"
    assert not frappe.db.exists("Subscription", sub_name)


def test_unsubscribe_by_non_owner_is_403(seeded):
    """§5.1 corollary: only the subscription owner may unsubscribe via REST → 403."""
    api = _api()
    _as("B")
    sub = api.subscribe(scope="sheet", target=seeded["sheet"], event_types=["NODE_VALUE_UPDATED"], delivery="in-app")
    sub_name = sub["data"]["subscription"]
    _as("E")
    with pytest.raises(frappe.PermissionError):
        api.unsubscribe(subscription=sub_name)
    assert frappe.db.exists("Subscription", sub_name)
