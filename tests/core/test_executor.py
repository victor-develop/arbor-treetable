"""execute_action: authorized-vs-suggested branches, the governance keystone
(ARCHITECTURE §4.2), owner-self policy, surface-parity of the one path."""

from __future__ import annotations

from arbor.core.executor import execute_action
from arbor.core.testing import RecordingEventSink
from arbor.core.types import Actor, ActorType
from tests.fixtures.canonical import A, B, C, D, E, seed_canonical_sheet


def _run(fx, action, params, actor):
    sink = RecordingEventSink()
    outcome = execute_action(action, params, Actor(actor), fx.repo, sink)
    return outcome, sink


def test_authorized_mutates_and_emits():
    fx = seed_canonical_sheet()
    # B owns col:name → updateCell executes.
    outcome, sink = _run(
        fx, "updateCell", {"sheet": fx.sheet, "node": fx.X, "column": fx.col_name, "value": "Renamed"}, B
    )
    assert outcome.kind == "executed"
    assert sink.types() == ["NODE_VALUE_UPDATED"]
    assert fx.repo.get_value(fx.X, fx.col_name) == "Renamed"
    assert sink.last().payload["new_value"] == "Renamed"


def test_unauthorized_becomes_change_request():
    fx = seed_canonical_sheet()
    # E owns nothing → suggestion to col owner C, no mutation.
    outcome, sink = _run(
        fx, "updateCell", {"sheet": fx.sheet, "node": fx.X, "column": fx.col_status, "value": "done"}, E
    )
    assert outcome.kind == "suggested"
    assert sink.types() == ["CHANGE_PROPOSED"]
    cr = fx.repo.get_change_request(outcome.change_request)
    assert cr["resolved_approver"] == C
    assert cr["status"] == "proposed"
    # NOT mutated.
    assert fx.repo.get_value(fx.X, fx.col_status) == "todo"


def test_add_node_authorized_for_branch_owner():
    fx = seed_canonical_sheet()
    # D owns P2 → addNode under Y executes.
    outcome, sink = _run(fx, "addNode", {"sheet": fx.sheet, "parent": fx.Y}, D)
    assert outcome.kind == "executed"
    assert sink.types() == ["NODE_CREATED"]


def test_add_node_outside_branch_suggests_to_root():
    fx = seed_canonical_sheet()
    # D adding under P1 (A's structure) → suggest to A.
    outcome, sink = _run(fx, "addNode", {"sheet": fx.sheet, "parent": fx.P1}, D)
    assert outcome.kind == "suggested"
    cr = fx.repo.get_change_request(outcome.change_request)
    assert cr["resolved_approver"] == A


def test_owner_self_policy_forces_cr():
    fx = seed_canonical_sheet(settings={"owners_must_use_change_requests": True})
    # B is authorized for col:name but policy forces a self-approver CR.
    outcome, sink = _run(
        fx, "updateCell", {"sheet": fx.sheet, "node": fx.X, "column": fx.col_name, "value": "x"}, B
    )
    assert outcome.kind == "suggested"
    cr = fx.repo.get_change_request(outcome.change_request)
    assert cr["resolved_approver"] == B  # self-approver
    assert fx.repo.get_value(fx.X, fx.col_name) == "Task X"  # not mutated yet


def test_move_node_dual_end_suggests_with_co_approver():
    fx = seed_canonical_sheet()
    outcome, sink = _run(fx, "moveNode", {"sheet": fx.sheet, "node": fx.X, "new_parent": fx.P2}, A)
    assert outcome.kind == "suggested"
    cr = fx.repo.get_change_request(outcome.change_request)
    assert cr["resolved_approver"] == D
    assert cr["payload"]["co_approvers"] == [A]


def test_internal_reset_emits_no_event():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    outcome = execute_action(
        "internalReset",
        {"sheet": fx.sheet, "confirm": True},
        Actor(A, ActorType.SYSTEM),
        fx.repo,
        sink,
    )
    assert outcome.kind == "executed"
    assert sink.events == []  # never on the append-only stream


def test_snapshot_is_read_outcome():
    fx = seed_canonical_sheet()
    outcome, sink = _run(fx, "getSheetSnapshot", {"sheet": fx.sheet}, A)
    assert outcome.kind == "read"
    assert sink.events == []


def test_actor_type_flows_to_event():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    execute_action(
        "updateCell",
        {"sheet": fx.sheet, "node": fx.X, "column": fx.col_name, "value": "Agent edit"},
        Actor(B, ActorType.AGENT),
        fx.repo,
        sink,
    )
    assert sink.last().actor_type == ActorType.AGENT
