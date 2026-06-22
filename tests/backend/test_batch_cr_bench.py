"""Multi-change Change Request — real-adapter (bench) round-trip.

runnable: NEEDS FRAPPE BENCH (``@pytest.mark.bench``). Confirms the batch CR
persists its ``changes`` child rows, spans two column owners, and applies
atomically only once every item is approved — through the whitelisted
``arbor.suggest_changes`` / ``arbor.approve_change`` funnel.
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


def test_batch_two_owners_applies_atomically(fx):
    h.login_as("E")  # suggest-only proposer
    out = api.suggest_changes(
        sheet=fx["sheet"],
        changes=[
            {"action": "updateCell", "params": {"node": _x(fx), "column": fx["columns"]["budget"], "value": 4242}},
            {"action": "updateCell", "params": {"node": _x(fx), "column": fx["columns"]["notes"], "value": "batched"}},
        ],
    )
    assert out["kind"] == "suggested"
    cr = out["change_request"]
    assert frappe.db.count("Change Request Change", {"parent": cr}) == 2

    # C owns col:budget → approves that item; col:notes (B) still pending → no apply.
    h.login_as("C")
    out_c = api.approve_change(change_request=cr)
    assert out_c["kind"] == "suggested"
    assert frappe.db.get_value("Tree Node Value", {"node": _x(fx), "column": fx["columns"]["budget"]}, "value") not in ('4242', 4242)

    # B owns col:notes → final approval completes the batch → both apply atomically.
    h.login_as("B")
    out_b = api.approve_change(change_request=cr)
    assert out_b["kind"] == "executed"
    assert h.cr_row(cr)["status"] == "approved"
    snap = api.get_sheet_snapshot(sheet=fx["sheet"])
    x = [n for n in snap["nodes"] if n["name"] == _x(fx)][0]
    assert x["values"][fx["columns"]["budget"]] == 4242
    assert x["values"][fx["columns"]["notes"]] == "batched"


def test_batch_reject_drops_whole_batch(fx):
    h.login_as("E")
    cr = api.suggest_changes(
        sheet=fx["sheet"],
        changes=[
            {"action": "updateCell", "params": {"node": _x(fx), "column": fx["columns"]["budget"], "value": 99}},
            {"action": "updateCell", "params": {"node": _x(fx), "column": fx["columns"]["notes"], "value": "x"}},
        ],
    )["change_request"]
    h.login_as("C")  # an approver of one item may reject the whole batch
    api.reject_change(change_request=cr)
    assert h.cr_row(cr)["status"] == "rejected"
    snap = api.get_sheet_snapshot(sheet=fx["sheet"])
    x = [n for n in snap["nodes"] if n["name"] == _x(fx)][0]
    assert x["values"].get(fx["columns"]["budget"]) != 99
