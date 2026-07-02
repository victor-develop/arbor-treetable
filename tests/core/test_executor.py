"""execute_action: authorized-vs-suggested branches, the governance keystone
(ARCHITECTURE §4.2), owner-self policy, surface-parity of the one path."""

from __future__ import annotations

import pytest

from arbor.core.executor import execute_action
from arbor.core.testing import RecordingEventSink
from arbor.core.types import Actor, ActorType, AuthorizationError
from tests.fixtures.canonical import A, B, C, D, E, seed_canonical_sheet


def _run(fx, action, params, actor):
    sink = RecordingEventSink()
    outcome = execute_action(action, params, Actor(actor), fx.repo, sink)
    return outcome, sink


# ---------------------------------------------------------------------------
# Actor.is_impersonated truth table (Area 1)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "user,real_user,expected",
    [
        ("bob", None, False),          # normal action: no real_user
        ("bob", "bob", False),         # real_user == user (self): not impersonated
        ("bob", "admin", True),        # admin acting as bob: impersonated
        ("admin", "admin", False),     # admin as themselves
    ],
)
def test_actor_is_impersonated_truth_table(user, real_user, expected):
    actor = Actor(user=user, real_user=real_user)
    assert actor.is_impersonated is expected


def _imp(user: str, real_user: str, is_admin: bool = False) -> Actor:
    """An impersonating Actor: effective ``user`` acted as by ``real_user``."""
    return Actor(user=user, real_user=real_user, impersonated_as=user, is_admin=is_admin)


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


# ---------------------------------------------------------------------------
# Impersonation trace stamping (Area 1) — additive; no-op without impersonation
# ---------------------------------------------------------------------------
def test_normal_actor_leaves_trace_none_on_authorized_event():
    fx = seed_canonical_sheet()
    _, sink = _run(
        fx, "updateCell", {"sheet": fx.sheet, "node": fx.X, "column": fx.col_name, "value": "x"}, B
    )
    ev = sink.last()
    assert ev.real_user is None and ev.impersonated_as is None


def test_impersonated_actor_stamps_trace_on_authorized_event():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    # admin acts as B (who owns col:name) → executes AS B, trace records admin.
    execute_action(
        "updateCell",
        {"sheet": fx.sheet, "node": fx.X, "column": fx.col_name, "value": "x"},
        _imp(user=B, real_user="admin", is_admin=True),
        fx.repo,
        sink,
    )
    ev = sink.last()
    assert ev.type == "NODE_VALUE_UPDATED"
    assert ev.actor == B                 # effective actor
    assert ev.real_user == "admin"       # truly-authenticated principal
    assert ev.impersonated_as == B


def test_impersonated_actor_stamps_real_requester_on_suggest():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    # admin acts as E (owns nothing) → CHANGE_PROPOSED to C; CR carries real_requester.
    outcome = execute_action(
        "updateCell",
        {"sheet": fx.sheet, "node": fx.X, "column": fx.col_status, "value": "done"},
        _imp(user=E, real_user="admin", is_admin=True),
        fx.repo,
        sink,
    )
    assert outcome.kind == "suggested"
    cr = fx.repo.get_change_request(outcome.change_request)
    assert cr["requester"] == E              # effective requester
    assert cr["real_requester"] == "admin"   # truly-authenticated principal
    # and the CHANGE_PROPOSED event carries the same trace.
    ev = sink.last()
    assert ev.real_user == "admin" and ev.impersonated_as == E


def test_normal_actor_leaves_real_requester_none_on_suggest():
    fx = seed_canonical_sheet()
    outcome, sink = _run(
        fx, "updateCell", {"sheet": fx.sheet, "node": fx.X, "column": fx.col_status, "value": "done"}, E
    )
    cr = fx.repo.get_change_request(outcome.change_request)
    assert cr["real_requester"] is None
    assert sink.last().real_user is None


def test_impersonated_actor_stamps_trace_on_batch_suggest():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    outcome = execute_action(
        "suggestChanges",
        {
            "sheet": fx.sheet,
            "changes": [
                {"action": "updateCell", "params": {"node": fx.X, "column": fx.col_status, "value": "done"}},
            ],
        },
        _imp(user=E, real_user="admin", is_admin=True),
        fx.repo,
        sink,
    )
    assert outcome.kind == "suggested"
    cr = fx.repo.get_change_request(outcome.change_request)
    assert cr["requester"] == E and cr["real_requester"] == "admin"
    ev = sink.last()
    assert ev.type == "CHANGE_PROPOSED"
    assert ev.real_user == "admin" and ev.impersonated_as == E


# ---------------------------------------------------------------------------
# begin/end impersonation admin gate (Area 1)
# ---------------------------------------------------------------------------
def test_begin_impersonation_denied_for_non_admin():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    with pytest.raises(AuthorizationError):
        execute_action(
            "beginImpersonation",
            {"impersonated_user": B},
            Actor(E, is_admin=False),
            fx.repo,
            sink,
        )
    assert sink.events == []  # no Tree Event either way


def test_begin_impersonation_denied_for_agent():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    # an agent-typed actor, even if flagged admin, is a human-only surface concept;
    # ActorType.AGENT is NOT SYSTEM so the gate requires is_admin — set False.
    with pytest.raises(AuthorizationError):
        execute_action(
            "beginImpersonation",
            {"impersonated_user": B},
            Actor(AGENT_USER := "agent-user", actor_type=ActorType.AGENT, is_admin=False),
            fx.repo,
            sink,
        )


def test_begin_and_end_impersonation_succeed_for_admin():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    out = execute_action(
        "beginImpersonation",
        {"impersonated_user": B, "reason": "support"},
        Actor("admin", is_admin=True),
        fx.repo,
        sink,
    )
    assert out.kind == "executed"
    assert out.data["impersonating"] == B
    # the session row IS the record — NO Tree Event emitted.
    assert sink.events == []
    active = fx.repo.get_active_impersonation("admin")
    assert active is not None and active["impersonated_user"] == B

    out2 = execute_action(
        "endImpersonation", {}, Actor("admin", is_admin=True), fx.repo, sink
    )
    assert out2.kind == "executed"
    assert out2.data["impersonating"] is None
    assert sink.events == []
    assert fx.repo.get_active_impersonation("admin") is None


def test_begin_impersonation_allowed_for_system_actor():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    out = execute_action(
        "beginImpersonation",
        {"impersonated_user": B},
        Actor("sys", actor_type=ActorType.SYSTEM, is_admin=False),
        fx.repo,
        sink,
    )
    assert out.kind == "executed"
