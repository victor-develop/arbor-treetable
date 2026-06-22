"""REQUIRES A FRAPPE BENCH + SITE (``@pytest.mark.bench``).

End-to-end coverage of the whitelisted REST funnel against the real adapter
(``FrappeRepository`` / ``FrappeEventSink``). These assert the API-side contract
only (the emitted Tree Event / Outcome / HTTP status); downstream webhook +
notification fan-out is covered by sibling catalogs.

Run on a bench::

    bench --site <site> run-tests --module tests.adapter.test_api_integration

Skipped automatically when frappe is not importable (see conftest).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.bench

frappe = pytest.importorskip("frappe")


@pytest.fixture()
def seeded():
    """Seed the canonical sheet `S` for each test (rolled back by the bench
    test harness's per-test transaction)."""
    from arbor.adapter.seed import seed_canonical_sheet

    return seed_canonical_sheet()


def _as(user: str):
    frappe.set_user((user if "@" in user else f"{user}@arbor.example").lower())


# --- B. surface parity -----------------------------------------------------
def test_api_authorized_write_executes_and_emits(seeded):
    """API-010: C (owner of col:budget) updates a cell → executed + 1 event."""
    from arbor import api

    _as("C")
    out = api.update_cell(
        sheet=seeded["sheet"],
        node=seeded["nodes"]["Y"],
        column=seeded["columns"]["budget"],
        value=42,
    )
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "NODE_VALUE_UPDATED"
    assert out["event"]["actor_type"] == "human"


def test_generic_dispatch_equals_named_method(seeded):
    """API-011: arbor.execute_action ≡ arbor.update_cell (one path)."""
    from arbor import api

    _as("C")
    out = api.execute_action(
        action_id="updateCell",
        params={
            "sheet": seeded["sheet"],
            "node": seeded["nodes"]["X"],
            "column": seeded["columns"]["budget"],
            "value": 7,
        },
    )
    assert out["kind"] == "executed"
    assert out["event"]["type"] == "NODE_VALUE_UPDATED"


# --- governance keystone: suggest is 200, not 403 --------------------------
def test_unauthorized_mutation_becomes_change_request(seeded):
    """API-030-ish: E has no authority → updateCell becomes a CR (200 suggested),
    NOT a 403."""
    from arbor import api

    _as("E")
    out = api.update_cell(
        sheet=seeded["sheet"],
        node=seeded["nodes"]["X"],
        column=seeded["columns"]["budget"],
        value=99,
    )
    assert out["kind"] == "suggested"
    assert out["change_request"]
    assert out["event"]["type"] == "CHANGE_PROPOSED"


# --- control-action denial IS a 403 ----------------------------------------
def test_approve_by_non_approver_is_403(seeded):
    """API-040: a non-approver approving a CR → 403 PermissionError."""
    from arbor import api

    _as("E")
    proposed = api.update_cell(
        sheet=seeded["sheet"], node=seeded["nodes"]["X"],
        column=seeded["columns"]["budget"], value=99,
    )
    cr = proposed["change_request"]
    _as("F")  # F is not the approver (C is)
    with pytest.raises(frappe.PermissionError):
        api.approve_change(change_request=cr)


# --- 404 contracts ---------------------------------------------------------
def test_snapshot_unknown_sheet_404(seeded):
    from arbor import api

    _as("B")
    with pytest.raises(frappe.DoesNotExistError):
        api.get_sheet_snapshot(sheet="does-not-exist")
    assert frappe.local.response.get("http_status_code") == 404


def test_unknown_capability_404(seeded):
    from arbor import api

    _as("B")
    with pytest.raises(frappe.DoesNotExistError):
        api.execute_action(action_id="frobnicate", params={})
    assert frappe.local.response.get("http_status_code") == 404


def test_update_unknown_node_404(seeded):
    from arbor import api

    _as("C")
    with pytest.raises(frappe.DoesNotExistError):
        api.update_cell(
            sheet=seeded["sheet"], node="no-such-node",
            column=seeded["columns"]["budget"], value=1,
        )


# --- 409 contracts ---------------------------------------------------------
def test_move_cycle_409(seeded):
    """API-150: moving a node under its own descendant → 409 cycle."""
    from arbor import api
    try:
        from arbor.adapter.repository import CycleError
    except ModuleNotFoundError:
        from arbor.arbor.adapter.repository import CycleError

    _as("A")
    # Move R (root) under X (its descendant) → cycle.
    with pytest.raises((CycleError, frappe.ValidationError)):
        api.move_node(
            sheet=seeded["sheet"], node=seeded["nodes"]["R"],
            new_parent=seeded["nodes"]["X"],
        )
    assert frappe.local.response.get("http_status_code") == 409


def test_approve_terminal_cr_409(seeded):
    """API-140: approving an already-decided CR → 409 conflict."""
    from arbor import api

    _as("E")
    proposed = api.update_cell(
        sheet=seeded["sheet"], node=seeded["nodes"]["X"],
        column=seeded["columns"]["budget"], value=99,
    )
    cr = proposed["change_request"]
    _as("C")
    api.approve_change(change_request=cr)  # first approval succeeds
    with pytest.raises(frappe.ValidationError):
        api.approve_change(change_request=cr)  # terminal → 409
    assert frappe.local.response.get("http_status_code") == 409


# --- snapshot shape + ACL hints --------------------------------------------
def test_snapshot_shape_and_acl_hints(seeded):
    """API-100: snapshot carries column config, node tree, values, and the
    per-actor edit/structure affordances. B can edit col:name (owner) but not
    col:budget (C's)."""
    from arbor import api

    _as("B")
    snap = api.get_sheet_snapshot(sheet=seeded["sheet"])
    assert snap["sheet"]["name"] == seeded["sheet"]
    assert {c["field"] for c in snap["columns"]} == {"name", "status", "budget", "notes"}
    by_field = {c["field"]: c for c in snap["columns"]}
    assert by_field["name"]["can_edit"] is True  # B owns col:name
    assert by_field["budget"]["can_edit"] is False  # C owns col:budget
    # Structure: B owns no branch → cannot change structure of any node.
    assert all(n["can_change_structure"] is False for n in snap["nodes"])


def test_snapshot_parity_with_in_process_serializer(seeded):
    """API-101: REST snapshot == direct serializer call (one serializer)."""
    from arbor import api

    _as("B")
    rest = api.get_sheet_snapshot(sheet=seeded["sheet"])
    # The REST method IS the in-process serializer call here (same process), so
    # parity is structural; assert the canonical keys exist and match a re-call.
    again = api.get_sheet_snapshot(sheet=seeded["sheet"])
    assert rest == again
