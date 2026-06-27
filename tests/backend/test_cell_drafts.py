"""Personal CELL DRAFT box — real-adapter (bench) round-trip.

runnable: NEEDS FRAPPE BENCH (``@pytest.mark.bench``; auto-skips bench-free).

The draft box is a per-USER, server-persisted staging area for cell edits BEFORE
they become a Change Request. It is private (a user only ever sees / edits their
OWN drafts) and holds at most ONE draft per (user, sheet, node, column) — saving
the same cell twice upserts. ``submit_cell_drafts`` promotes ALL of a user's
drafts for a sheet into ONE multi-change CR (each item re-resolving to its own
approver via the SAME executor ``suggestChanges`` funnel) and then deletes the
submitted drafts. These are UI-staging endpoints, NOT registry capabilities.

Exercised through the whitelisted ``arbor.api`` funnel only.
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


def _x(fx):
    return fx["nodes"]["X"]


def _drafts_in_db(user: str, sheet: str):
    return frappe.get_all(
        "Arbor Cell Draft",
        filters={"user": user, "sheet": sheet},
        fields=["name", "node", "column", "value", "base_version"],
        order_by="creation asc",
    )


def test_save_cell_draft_upserts_same_cell(fx):
    """Two saves on the SAME cell collapse to ONE draft holding the latest value."""
    h.login_as("E")
    me = h.user("E")
    node, col = _x(fx), fx["columns"]["budget"]

    out1 = api.save_cell_draft(sheet=fx["sheet"], node=node, column=col, value=100)
    out2 = api.save_cell_draft(sheet=fx["sheet"], node=node, column=col, value=250)

    # Same draft name (updated in place, not a second row).
    assert out1["name"] == out2["name"]
    rows = _drafts_in_db(me, fx["sheet"])
    assert len(rows) == 1
    # Latest value wins (value stored as JSON; normalize before comparing).
    stored = rows[0].value
    if isinstance(stored, str):
        stored = frappe.parse_json(stored)
    assert stored == 250


def test_save_cell_draft_persists_base_version(fx):
    h.login_as("E")
    node, col = _x(fx), fx["columns"]["budget"]
    out = api.save_cell_draft(
        sheet=fx["sheet"], node=node, column=col, value=7, base_version=3
    )
    assert frappe.db.get_value("Arbor Cell Draft", out["name"], "base_version") == 3


def test_list_cell_drafts_only_returns_actors_own(fx):
    """A user sees ONLY their own drafts, never another user's."""
    node = _x(fx)
    budget, notes = fx["columns"]["budget"], fx["columns"]["notes"]

    # E stages a draft on budget.
    h.login_as("E")
    api.save_cell_draft(sheet=fx["sheet"], node=node, column=budget, value="E-val")

    # F stages a draft on notes.
    h.login_as("F")
    api.save_cell_draft(sheet=fx["sheet"], node=node, column=notes, value="F-val")
    f_list = api.list_cell_drafts(sheet=fx["sheet"])
    assert len(f_list) == 1
    assert f_list[0]["column"] == notes

    # E only sees E's draft (not F's).
    h.login_as("E")
    e_list = api.list_cell_drafts(sheet=fx["sheet"])
    assert len(e_list) == 1
    assert e_list[0]["column"] == budget
    assert e_list[0]["value"] == "E-val"
    assert {"name", "node", "column", "value", "base_version"} <= set(e_list[0].keys())


def test_discard_one_cell_draft(fx):
    h.login_as("E")
    me = h.user("E")
    node = _x(fx)
    budget, notes = fx["columns"]["budget"], fx["columns"]["notes"]
    api.save_cell_draft(sheet=fx["sheet"], node=node, column=budget, value=1)
    api.save_cell_draft(sheet=fx["sheet"], node=node, column=notes, value=2)

    out = api.discard_cell_draft(sheet=fx["sheet"], node=node, column=budget)
    assert out == {"ok": True}

    rows = _drafts_in_db(me, fx["sheet"])
    assert len(rows) == 1
    assert rows[0].column == notes

    # Discarding an absent cell is a no-op.
    assert api.discard_cell_draft(sheet=fx["sheet"], node=node, column=budget) == {"ok": True}


def test_discard_all_cell_drafts(fx):
    h.login_as("E")
    me = h.user("E")
    node = _x(fx)
    api.save_cell_draft(sheet=fx["sheet"], node=node, column=fx["columns"]["budget"], value=1)
    api.save_cell_draft(sheet=fx["sheet"], node=node, column=fx["columns"]["notes"], value=2)

    out = api.discard_cell_drafts(sheet=fx["sheet"])
    assert out == {"discarded": 2}
    assert _drafts_in_db(me, fx["sheet"]) == []


def test_submit_builds_one_multi_change_cr_and_clears_drafts(fx):
    """Submit promotes ALL drafts into ONE multi-change CR (routed via the executor)
    and DELETES the submitted drafts."""
    h.login_as("E")  # suggest-only proposer
    me = h.user("E")
    node = _x(fx)
    api.save_cell_draft(sheet=fx["sheet"], node=node, column=fx["columns"]["budget"], value=4242)
    api.save_cell_draft(sheet=fx["sheet"], node=node, column=fx["columns"]["notes"], value="batched")

    out = api.submit_cell_drafts(sheet=fx["sheet"])

    # Routed through suggestChanges → ONE CR carrying both changes.
    assert out["kind"] == "suggested"
    cr = out["change_request"]
    assert frappe.db.count("Change Request Change", {"parent": cr}) == 2

    # The submitted drafts are gone.
    assert _drafts_in_db(me, fx["sheet"]) == []
    assert api.list_cell_drafts(sheet=fx["sheet"]) == []

    # The CR actually applies the staged values once each owner approves.
    h.login_as("C")  # owns col:budget
    api.approve_change(change_request=cr)
    h.login_as("B")  # owns col:notes → final approval completes the batch
    out_b = api.approve_change(change_request=cr)
    assert out_b["kind"] == "executed"
    snap = api.get_sheet_snapshot(sheet=fx["sheet"])
    x = [n for n in snap["nodes"] if n["name"] == node][0]
    assert x["values"][fx["columns"]["budget"]] == 4242
    assert x["values"][fx["columns"]["notes"]] == "batched"


def test_submit_empty_is_noop(fx):
    """No drafts → no CR; returns the read no-op envelope."""
    h.login_as("E")
    out = api.submit_cell_drafts(sheet=fx["sheet"])
    assert out == {"kind": "read", "data": {}}
