"""Multi-change (batch) Change Requests — bench-free core semantics.

A CR can bundle N changes spanning different owners; it is approved/applied
ATOMICALLY: nothing mutates until EVERY item has its rightful approver, and a
reject drops the whole batch. Exercised end-to-end through ``execute_action``
against the in-memory repo (the same path the bench/REST surfaces funnel)."""

from __future__ import annotations

import pytest

from arbor.core.executor import execute_action
from arbor.core.testing import RecordingEventSink
from arbor.core.types import Actor, AuthorizationError, CRStatus
from tests.fixtures.canonical import A, B, C, E, seed_canonical_sheet


def _suggest_batch(fx, actor):
    """E proposes two edits in one CR: col:budget (owner C) + col:notes (owner B)."""
    sink = RecordingEventSink()
    out = execute_action(
        "suggestChanges",
        {
            "sheet": fx.sheet,
            "changes": [
                {"action": "updateCell", "params": {"node": fx.X, "column": fx.col_budget, "value": 4242}},
                {"action": "updateCell", "params": {"node": fx.X, "column": fx.col_notes, "value": "batched"}},
            ],
        },
        Actor(actor),
        fx.repo,
        sink,
    )
    return out, sink


def test_batch_spans_two_owners_and_applies_atomically():
    fx = seed_canonical_sheet()
    out, _ = _suggest_batch(fx, E)
    assert out.kind == "suggested"
    cr = out.change_request
    assert len(fx.repo.get_change_request(cr)["changes"]) == 2

    sink = RecordingEventSink()
    budget_before = fx.repo.get_value(fx.X, fx.col_budget)

    # C approves the budget item; the notes item (B's) is still pending → no apply.
    out_c = execute_action("approveChange", {"change_request": cr}, Actor(C), fx.repo, sink)
    assert out_c.kind == "suggested"  # batch not complete
    assert fx.repo.get_value(fx.X, fx.col_budget) == budget_before  # nothing applied yet
    assert fx.repo.get_change_request(cr)["status"] == CRStatus.PROPOSED.value

    # B approves the notes item → all items approved → whole batch applies atomically.
    out_b = execute_action("approveChange", {"change_request": cr}, Actor(B), fx.repo, sink)
    assert out_b.kind == "executed"
    assert fx.repo.get_change_request(cr)["status"] == CRStatus.APPROVED.value
    assert fx.repo.get_value(fx.X, fx.col_budget) == 4242
    assert fx.repo.get_value(fx.X, fx.col_notes) == "batched"


def test_batch_non_approver_cannot_approve():
    fx = seed_canonical_sheet()
    out, _ = _suggest_batch(fx, E)
    sink = RecordingEventSink()
    # A owns no columns in the batch → cannot approve any item.
    with pytest.raises(AuthorizationError):
        execute_action("approveChange", {"change_request": out.change_request}, Actor(A), fx.repo, sink)


def test_batch_reject_drops_whole_batch_no_mutation():
    fx = seed_canonical_sheet()
    out, _ = _suggest_batch(fx, E)
    cr = out.change_request
    sink = RecordingEventSink()
    budget_before = fx.repo.get_value(fx.X, fx.col_budget)
    notes_before = fx.repo.get_value(fx.X, fx.col_notes)

    # C (an approver of one item) may reject the whole batch.
    execute_action("rejectChange", {"change_request": cr}, Actor(C), fx.repo, sink)
    assert fx.repo.get_change_request(cr)["status"] == CRStatus.REJECTED.value
    assert fx.repo.get_value(fx.X, fx.col_budget) == budget_before
    assert fx.repo.get_value(fx.X, fx.col_notes) == notes_before
