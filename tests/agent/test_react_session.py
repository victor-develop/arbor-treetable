"""Agent-lane integration tests: the Re-Act session runner over the pure core
doubles + a scripted MockLLMProvider (no bench, no network).

Maps to agent.md: AGENT-004..009 (loop mechanics + guards), 010..016 (authority
& governance keystone), 017..019 (axis independence), 020/023 (orchestration),
031/032 (denied control), 033 (withdraw own), 039 (validation), 040 (provider
error), 048 (bad reference), and surface-parity intent (041/042).
"""

from __future__ import annotations

from arbor.arbor.agent.react import run_agent_session
from arbor.core.testing import MockLLMProvider, RecordingEventSink
from arbor.core.types import Actor, ActorType, CRStatus
from tests.fixtures.canonical import AGENT, B, C, D, seed_canonical_sheet


def _actor(user: str) -> Actor:
    return Actor(user, ActorType.AGENT)


def _tool(name, args, call_id="t1"):
    return {"id": call_id, "name": name, "arguments": args}


def _run(fx, actor, turns, sink=None, snapshot_fn=None, max_steps=12):
    sink = sink or RecordingEventSink()
    provider = MockLLMProvider(turns)
    session = run_agent_session(
        "do it",
        actor,
        fx.repo,
        sink,
        provider,
        snapshot_fn=snapshot_fn,
        max_steps=max_steps,
    )
    return session, sink


# --- A/B. Guards & loop mechanics ------------------------------------------
def test_hidden_tool_is_refused_not_executed():
    # AGENT-004 — internalReset must never reach execute_action.
    fx = seed_canonical_sheet()
    session, sink = _run(
        fx,
        _actor(AGENT),
        [
            {"content": None, "tool_calls": [_tool("internalReset", {"sheet": fx.sheet, "confirm": True})]},
            {"content": "I cannot reset the sheet.", "tool_calls": []},
        ],
    )
    obs = session.tool_calls[0]["observation"]
    assert obs["kind"] == "tool_error"
    assert sink.events == []  # no Tree Event emitted


def test_transcript_orders_thought_action_observation():
    # AGENT-006 — count(action) == count(observation), interleaved order.
    fx = seed_canonical_sheet()
    session, _ = _run(
        fx,
        _actor(C),
        [
            {"content": "I'll set the budget.", "tool_calls": [_tool("updateCell", {"sheet": fx.sheet, "node": fx.Y, "column": fx.col_budget, "value": 7})]},
            {"content": "Done.", "tool_calls": []},
        ],
    )
    kinds = [t["kind"] for t in session.transcript]
    assert kinds.count("action") == kinds.count("observation")
    ai = kinds.index("action")
    assert kinds[ai + 1] == "observation"
    assert session.transcript[-1]["kind"] == "final"
    assert session.terminated_by == "final"


def test_loop_terminates_on_final_with_no_tool_calls():
    # AGENT-008
    fx = seed_canonical_sheet()
    session, sink = _run(fx, _actor(AGENT), [{"content": "Nothing to do.", "tool_calls": []}])
    assert session.final_message == "Nothing to do."
    assert session.terminated_by == "final"
    assert not any(t["kind"] == "action" for t in session.transcript)


def test_max_steps_guard_halts_runaway_provider():
    # AGENT-009 — provider always asks for a tool; loop stops at max_steps.
    fx = seed_canonical_sheet()
    runaway = [{"content": None, "tool_calls": [_tool("getSheetSnapshot", {"sheet": fx.sheet})]}] * 50
    session, _ = _run(fx, _actor(AGENT), runaway, snapshot_fn=lambda s, a: {"sheet": s}, max_steps=4)
    assert session.terminated_by == "max_steps"
    assert sum(1 for t in session.transcript if t["kind"] == "action") == 4


# --- C/D. Authority: executes vs Change Request (governance keystone) ------
def test_authorized_column_owner_executes_updatecell():
    # AGENT-010 / AGENT-012 — identity C owns col:budget; actor_type=agent.
    fx = seed_canonical_sheet()
    session, sink = _run(
        fx,
        _actor(C),
        [
            {"content": None, "tool_calls": [_tool("updateCell", {"sheet": fx.sheet, "node": fx.Y, "column": fx.col_budget, "value": 5000})]},
            {"content": "Updated.", "tool_calls": []},
        ],
    )
    assert session.tool_calls[0]["observation"]["kind"] == "executed"
    assert sink.types() == ["NODE_VALUE_UPDATED"]
    assert sink.last().actor_type == ActorType.AGENT
    assert sink.last().actor == C
    assert sink.last().change_request is None


def test_suggest_only_agent_celledit_becomes_change_request():
    # AGENT-013 — AGENT owns nothing; updateCell -> CR to C, value unchanged.
    fx = seed_canonical_sheet()
    before = fx.repo.get_value(fx.X, fx.col_budget)
    session, sink = _run(
        fx,
        _actor(AGENT),
        [
            {"content": None, "tool_calls": [_tool("updateCell", {"sheet": fx.sheet, "node": fx.X, "column": fx.col_budget, "value": 9000})]},
            {"content": "Filed a change request for C.", "tool_calls": []},
        ],
    )
    obs = session.tool_calls[0]["observation"]
    assert obs["kind"] == "suggested"
    cr = fx.repo.get_change_request(obs["change_request"])
    assert cr["resolved_approver"] == C
    assert cr["target_kind"] == "cell-value"
    assert sink.types() == ["CHANGE_PROPOSED"]
    assert fx.repo.get_value(fx.X, fx.col_budget) == before  # unchanged


def test_agent_cannot_escalate_privilege():
    # AGENT-015 — suggest-only deleteNode(Z) -> CR to D (Z in D's P2 branch).
    fx = seed_canonical_sheet()
    session, sink = _run(
        fx,
        _actor(AGENT),
        [
            {"content": None, "tool_calls": [_tool("deleteNode", {"sheet": fx.sheet, "node": fx.Z})]},
            {"content": "Filed CR.", "tool_calls": []},
        ],
    )
    obs = session.tool_calls[0]["observation"]
    assert obs["kind"] == "suggested"
    assert fx.repo.get_change_request(obs["change_request"])["resolved_approver"] == D
    assert sink.types() == ["CHANGE_PROPOSED"]
    assert fx.Z in fx.repo.nodes  # not deleted


def test_explicit_suggest_always_creates_cr_even_when_authorized():
    # AGENT-016 — C could edit col:budget but deliberately suggests.
    fx = seed_canonical_sheet()
    session, sink = _run(
        fx,
        _actor(C),
        [
            {"content": None, "tool_calls": [_tool("suggestChange", {"sheet": fx.sheet, "target_kind": "cell-value", "operation": "update", "payload": {"node": fx.Y, "column": fx.col_budget, "value": 1}})]},
            {"content": "Suggested.", "tool_calls": []},
        ],
    )
    assert session.tool_calls[0]["observation"]["kind"] == "suggested"
    assert sink.types() == ["CHANGE_PROPOSED"]


# --- E. Axis independence ---------------------------------------------------
def test_column_owner_edits_inside_branch_it_does_not_own():
    # AGENT-017 — B owns col:name; edits Z (in D's branch) -> executes (Axis 2).
    fx = seed_canonical_sheet()
    session, sink = _run(
        fx,
        _actor(B),
        [
            {"content": None, "tool_calls": [_tool("updateCell", {"sheet": fx.sheet, "node": fx.Z, "column": fx.col_name, "value": "renamed"})]},
            {"content": "ok", "tool_calls": []},
        ],
    )
    assert session.tool_calls[0]["observation"]["kind"] == "executed"
    assert sink.types() == ["NODE_VALUE_UPDATED"]


def test_structural_owner_editing_unowned_column_is_suggested():
    # AGENT-018 — D owns P2 structurally; status col owned by C -> CR to C.
    fx = seed_canonical_sheet()
    session, sink = _run(
        fx,
        _actor(D),
        [
            {"content": None, "tool_calls": [_tool("updateCell", {"sheet": fx.sheet, "node": fx.Y, "column": fx.col_status, "value": "done"})]},
            {"content": "Filed CR for C.", "tool_calls": []},
        ],
    )
    obs = session.tool_calls[0]["observation"]
    assert obs["kind"] == "suggested"
    assert fx.repo.get_change_request(obs["change_request"])["resolved_approver"] == C
    assert sink.types() == ["CHANGE_PROPOSED"]


def test_label_edit_is_axis2_not_axis1():
    # AGENT-019 — D owns Z structurally; label col owned by B -> CR to B.
    fx = seed_canonical_sheet()
    session, _ = _run(
        fx,
        _actor(D),
        [
            {"content": None, "tool_calls": [_tool("updateCell", {"sheet": fx.sheet, "node": fx.Z, "column": fx.col_name, "value": "Z2"})]},
            {"content": "Filed CR for B.", "tool_calls": []},
        ],
    )
    obs = session.tool_calls[0]["observation"]
    assert obs["kind"] == "suggested"
    assert fx.repo.get_change_request(obs["change_request"])["resolved_approver"] == B


# --- F. Multi-step orchestration & partial-failure independence ------------
def test_partial_failure_does_not_roll_back_prior_authorized_step():
    # AGENT-023 — C edits budget (exec) then name (suggested); first persists.
    fx = seed_canonical_sheet()
    session, sink = _run(
        fx,
        _actor(C),
        [
            {"content": None, "tool_calls": [_tool("updateCell", {"sheet": fx.sheet, "node": fx.Y, "column": fx.col_budget, "value": 42}, "a")]},
            {"content": None, "tool_calls": [_tool("updateCell", {"sheet": fx.sheet, "node": fx.Y, "column": fx.col_name, "value": "Y2"}, "b")]},
            {"content": "One updated, one suggested.", "tool_calls": []},
        ],
    )
    kinds = [tc["observation"]["kind"] for tc in session.tool_calls]
    assert kinds == ["executed", "suggested"]
    assert sink.types() == ["NODE_VALUE_UPDATED", "CHANGE_PROPOSED"]
    assert fx.repo.get_value(fx.Y, fx.col_budget) == 42  # not rolled back


# --- H. Control-capability authority via the agent -------------------------
def test_agent_withdraws_its_own_cr():
    # AGENT-033 — AGENT files then withdraws its own CR.
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    # First, file a CR by an unauthorized edit.
    s1, _ = _run(
        fx,
        _actor(AGENT),
        [
            {"content": None, "tool_calls": [_tool("updateCell", {"sheet": fx.sheet, "node": fx.X, "column": fx.col_budget, "value": 1})]},
            {"content": "filed", "tool_calls": []},
        ],
        sink=sink,
    )
    cr_name = s1.tool_calls[0]["observation"]["change_request"]
    # Now withdraw it.
    s2, _ = _run(
        fx,
        _actor(AGENT),
        [
            {"content": None, "tool_calls": [_tool("withdrawChange", {"change_request": cr_name})]},
            {"content": "withdrawn", "tool_calls": []},
        ],
        sink=sink,
    )
    assert s2.tool_calls[0]["observation"]["kind"] == "executed"
    assert fx.repo.get_change_request(cr_name)["status"] == CRStatus.WITHDRAWN.value
    assert sink.types()[-1] == "CHANGE_REJECTED"  # withdraw closes via CHANGE_REJECTED


def test_agent_withdrawing_anothers_cr_is_denied():
    # AGENT-032 — AGENT cannot withdraw E's CR; surfaced as authorization_error.
    fx = seed_canonical_sheet()
    cr = fx.repo.create_change_request(
        {"sheet": fx.sheet, "target_kind": "cell-value", "operation": "update", "payload": {}, "requester": "E", "resolved_approver": C, "status": CRStatus.PROPOSED.value}
    )
    session, sink = _run(
        fx,
        _actor(AGENT),
        [
            {"content": None, "tool_calls": [_tool("withdrawChange", {"change_request": cr})]},
            {"content": "cannot", "tool_calls": []},
        ],
    )
    assert session.tool_calls[0]["observation"]["kind"] == "authorization_error"
    assert fx.repo.get_change_request(cr)["status"] == CRStatus.PROPOSED.value
    assert sink.events == []


def test_agent_approving_cr_it_does_not_own_is_denied():
    # AGENT-031
    fx = seed_canonical_sheet()
    cr = fx.repo.create_change_request(
        {"sheet": fx.sheet, "target_kind": "cell-value", "operation": "update", "payload": {"_action_id": "updateCell", "sheet": fx.sheet, "node": fx.Y, "column": fx.col_budget, "value": 1}, "requester": "E", "resolved_approver": C, "status": CRStatus.PROPOSED.value}
    )
    session, sink = _run(
        fx,
        _actor(AGENT),
        [
            {"content": None, "tool_calls": [_tool("approveChange", {"change_request": cr})]},
            {"content": "cannot", "tool_calls": []},
        ],
    )
    assert session.tool_calls[0]["observation"]["kind"] == "authorization_error"
    assert fx.repo.get_change_request(cr)["status"] == CRStatus.PROPOSED.value
    assert sink.events == []


# --- I/L. Validation, provider error, bad reference ------------------------
def test_malformed_tool_args_are_surfaced_not_executed():
    # AGENT-039 — updateCell missing required `value`.
    fx = seed_canonical_sheet()
    session, sink = _run(
        fx,
        _actor(C),
        [
            {"content": None, "tool_calls": [_tool("updateCell", {"sheet": fx.sheet, "node": fx.Y, "column": fx.col_budget})]},
            {"content": "bad args", "tool_calls": []},
        ],
    )
    assert session.tool_calls[0]["observation"]["kind"] == "validation_error"
    assert sink.events == []


def test_bad_reference_yields_clean_tool_error():
    # AGENT-048 — a bad reference (unknown column, which the repo cannot resolve
    # during ACL) surfaces a clean not_found observation, no crash, no event.
    # (Unknown-node validation is adapter-enforced; see test_chat_endpoint_bench.)
    fx = seed_canonical_sheet()
    session, sink = _run(
        fx,
        _actor(C),
        [
            {"content": None, "tool_calls": [_tool("updateCell", {"sheet": fx.sheet, "node": fx.Y, "column": "col:DOES_NOT_EXIST", "value": 1})]},
            {"content": "not found", "tool_calls": []},
        ],
    )
    assert session.tool_calls[0]["observation"]["kind"] == "not_found"
    assert sink.events == []


def test_provider_error_ends_loop_gracefully():
    # AGENT-040 — provider raises; loop returns terminated_by=provider_error.
    fx = seed_canonical_sheet()

    class _BoomProvider:
        def complete(self, messages, tools):
            raise RuntimeError("transport down")

    session = run_agent_session("x", _actor(C), fx.repo, RecordingEventSink(), _BoomProvider())
    assert session.terminated_by == "provider_error"
    assert "provider error" in session.final_message.lower()


# --- B. Read flow via the shared snapshot serializer -----------------------
def test_read_returns_snapshot_via_injected_serializer():
    # AGENT-005 — getSheetSnapshot routes to the shared serializer; no event.
    fx = seed_canonical_sheet()
    calls = {}

    def snapshot_fn(sheet, actor):
        calls["sheet"] = sheet
        calls["actor"] = actor
        return {"sheet": {"name": sheet}, "nodes": [1, 2, 3, 4, 5]}

    session, sink = _run(
        fx,
        _actor(AGENT),
        [
            {"content": None, "tool_calls": [_tool("getSheetSnapshot", {"sheet": fx.sheet})]},
            {"content": "There are 5 nodes.", "tool_calls": []},
        ],
        snapshot_fn=snapshot_fn,
    )
    obs = session.tool_calls[0]["observation"]
    assert obs["kind"] == "read"
    assert len(obs["data"]["nodes"]) == 5
    assert calls["sheet"] == fx.sheet
    assert sink.events == []  # read-only
