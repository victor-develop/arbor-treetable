"""Change Request lifecycle incl. moveNode dual-approval replay (ARCHITECTURE §5)."""

from __future__ import annotations

import pytest

from arbor.core.executor import execute_action
from arbor.core.testing import RecordingEventSink
from arbor.core.types import (
    Actor,
    AuthorizationError,
    CRStateError,
)
from tests.fixtures.canonical import A, B, C, D, E, seed_canonical_sheet


def _suggest_status_edit(fx):
    sink = RecordingEventSink()
    outcome = execute_action(
        "updateCell",
        {"sheet": fx.sheet, "node": fx.X, "column": fx.col_status, "value": "done"},
        Actor(E),
        fx.repo,
        sink,
    )
    return outcome.change_request, sink


def test_approve_replays_handler_and_emits_real_event():
    fx = seed_canonical_sheet()
    cr, _ = _suggest_status_edit(fx)
    sink = RecordingEventSink()
    outcome = execute_action("approveChange", {"change_request": cr}, Actor(C), fx.repo, sink)
    assert outcome.kind == "executed"
    # Real mutation event THEN CHANGE_APPROVED.
    assert sink.types() == ["NODE_VALUE_UPDATED", "CHANGE_APPROVED"]
    assert fx.repo.get_value(fx.X, fx.col_status) == "done"
    stored = fx.repo.get_change_request(cr)
    assert stored["status"] == "approved"
    assert stored["resulting_event"] == sink.events[0].event_id


def test_reject_emits_rejected_no_mutation():
    fx = seed_canonical_sheet()
    cr, _ = _suggest_status_edit(fx)
    sink = RecordingEventSink()
    execute_action("rejectChange", {"change_request": cr}, Actor(C), fx.repo, sink)
    assert sink.types() == ["CHANGE_REJECTED"]
    assert fx.repo.get_value(fx.X, fx.col_status) == "todo"
    assert fx.repo.get_change_request(cr)["status"] == "rejected"


def test_withdraw_by_requester_emits_rejected_reason_withdrawn():
    fx = seed_canonical_sheet()
    cr, _ = _suggest_status_edit(fx)
    sink = RecordingEventSink()
    execute_action("withdrawChange", {"change_request": cr}, Actor(E), fx.repo, sink)
    assert sink.types() == ["CHANGE_REJECTED"]
    assert sink.last().payload["reason"] == "withdrawn"
    assert fx.repo.get_change_request(cr)["status"] == "withdrawn"


def test_non_approver_cannot_approve():
    fx = seed_canonical_sheet()
    cr, _ = _suggest_status_edit(fx)
    sink = RecordingEventSink()
    with pytest.raises(AuthorizationError):
        execute_action("approveChange", {"change_request": cr}, Actor(E), fx.repo, sink)


def test_non_requester_cannot_withdraw():
    fx = seed_canonical_sheet()
    cr, _ = _suggest_status_edit(fx)
    sink = RecordingEventSink()
    with pytest.raises(AuthorizationError):
        execute_action("withdrawChange", {"change_request": cr}, Actor(B), fx.repo, sink)


def test_terminal_state_is_immutable():
    fx = seed_canonical_sheet()
    cr, _ = _suggest_status_edit(fx)
    sink = RecordingEventSink()
    execute_action("rejectChange", {"change_request": cr}, Actor(C), fx.repo, sink)
    with pytest.raises(CRStateError):
        execute_action("approveChange", {"change_request": cr}, Actor(C), fx.repo, sink)


def test_move_node_dual_approval_requires_both():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    out = execute_action(
        "moveNode", {"sheet": fx.sheet, "node": fx.X, "new_parent": fx.P2}, Actor(A), fx.repo, sink
    )
    cr = out.change_request
    # resolved_approver = D, co_approver = A.
    # D approves first → still pending (A must also approve), no mutation yet.
    s1 = RecordingEventSink()
    r1 = execute_action("approveChange", {"change_request": cr}, Actor(D), fx.repo, s1)
    assert r1.kind == "suggested"
    assert "A" in r1.data["pending_approvers"]
    assert s1.events == []  # nothing emitted yet
    assert fx.repo.get_node(fx.X).parent == fx.P1  # unchanged

    # A (co-approver) approves → all required approvals collected → replay + move.
    s2 = RecordingEventSink()
    r2 = execute_action("approveChange", {"change_request": cr}, Actor(A), fx.repo, s2)
    assert r2.kind == "executed"
    assert s2.types() == ["NODE_MOVED", "CHANGE_APPROVED"]
    assert fx.repo.get_node(fx.X).parent == fx.P2
    assert fx.repo.get_change_request(cr)["status"] == "approved"


def test_explicit_suggest_change_creates_cr():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    out = execute_action(
        "suggestChange",
        {
            "sheet": fx.sheet,
            "target_kind": "cell-value",
            "operation": "update",
            "payload": {
                "sheet": fx.sheet,
                "node": fx.X,
                "column": fx.col_budget,
                "value": 9999,
                "resolved_approver": C,
            },
        },
        Actor(E),
        fx.repo,
        sink,
    )
    assert out.kind == "suggested"
    assert sink.types() == ["CHANGE_PROPOSED"]
    # Approving an explicit suggestChange replays via (target_kind, operation).
    s2 = RecordingEventSink()
    execute_action("approveChange", {"change_request": out.change_request}, Actor(C), fx.repo, s2)
    assert fx.repo.get_value(fx.X, fx.col_budget) == 9999
