"""Workspace (sheetless) agent session — the Sheet-List home-page flow.

The GOAL: from the sheet-LIST home page a user talks to the agent in natural
language ("create a sheet for X with columns A (owner Alice), B (owner Bob), and
a process where a row triggers filling A, then A triggers filling B") and the
agent uses CAPABILITIES to build it end to end.

These tests exercise the WORKSPACE mode of ``run_agent_session`` (no active sheet,
no pre-fetched snapshot): a scripted ``MockLLMProvider`` calls createSheet FIRST,
threads the returned sheet id into every later tool call, adds the two owner
columns, wires the trigger->expectation process DAG, and enables it. Assertions
run against the pure in-memory Repository (no bench, no network).
"""

from __future__ import annotations

from arbor.arbor.agent.react import run_agent_session
from arbor.core.testing import InMemoryRepository, MockLLMProvider, RecordingEventSink
from arbor.core.types import Actor, ActorType

WS = "WS_OWNER"  # the creating (workspace) user
ALICE = "alice"
BOB = "bob"
SHEET = "launch-plan"  # explicit id so the scripted turns can reference it


def _actor(user: str = WS) -> Actor:
    return Actor(user, ActorType.AGENT)


def _tool(name, args, call_id="t"):
    return {"id": call_id, "name": name, "arguments": args}


def _workspace_turns():
    """The scripted end-to-end build: createSheet -> addColumn A(alice) ->
    addColumn B(bob) -> defineProcess(row->A; A->B) -> enableProcess -> final."""
    return [
        {"content": "Creating the sheet first.", "tool_calls": [
            _tool("createSheet", {"title": "Launch Plan", "name": SHEET, "label_column": "Item"}, "c1")]},
        {"content": None, "tool_calls": [
            _tool("addColumn", {"sheet": SHEET, "field": "A", "label": "Step A",
                                "type": "text", "column_owner": ALICE}, "c2")]},
        {"content": None, "tool_calls": [
            _tool("addColumn", {"sheet": SHEET, "field": "B", "label": "Step B",
                                "type": "text", "column_owner": BOB}, "c3")]},
        {"content": None, "tool_calls": [
            _tool("defineProcess", {"sheet": SHEET, "title": "Flow", "rules": [
                {"trigger_kind": "row", "trigger_op": "created", "expected_columns": ["A"]},
                {"trigger_kind": "column", "trigger_columns": ["A"], "trigger_op": "updated",
                 "expected_columns": ["B"], "within_seconds": 3600},
            ]}, "c4")]},
        {"content": None, "tool_calls": [_tool("enableProcess", {"sheet": SHEET}, "c5")]},
        {"content": "Built the sheet, both columns, and enabled the process.", "tool_calls": []},
    ]


def _run(turns, actor=None, snapshot_fn=None):
    repo = InMemoryRepository()
    sink = RecordingEventSink()
    session = run_agent_session(
        "create a sheet for launch with A (owner alice), B (owner bob), row->A, A->B",
        actor or _actor(),
        repo,
        sink,
        MockLLMProvider(turns),
        snapshot_fn=snapshot_fn,
        max_steps=12,
    )
    return session, repo, sink


def test_workspace_session_builds_sheet_columns_and_process_end_to_end():
    session, repo, sink = _run(_workspace_turns())

    # Terminated cleanly (a tool-free final turn), not on the step budget.
    assert session.terminated_by == "final"

    # (1) createSheet made the sheet with the CREATOR as structural_owner.
    assert SHEET in repo.sheets
    assert repo.get_sheet(SHEET).structural_owner == WS

    # (2) Both owner columns exist with the right column_owner.
    cols = {c.field: c for c in repo.list_columns(SHEET)}
    assert cols["A"].column_owner == ALICE
    assert cols["B"].column_owner == BOB
    # The default label column from createSheet is present and owned by the creator.
    assert any(c.is_label and c.column_owner == WS for c in repo.list_columns(SHEET))

    # (3) An ENABLED process with the row->A and A->B rules.
    process = repo.get_process(SHEET)
    assert process is not None and process.enabled is True
    by_expected = {tuple(r.expected_columns): r for r in process.rules}
    assert ("A",) in by_expected and ("B",) in by_expected
    row_rule = by_expected[("A",)]
    col_rule = by_expected[("B",)]
    assert row_rule.trigger_kind == "row"
    assert col_rule.trigger_kind == "column"
    assert col_rule.trigger_columns == ["A"]

    # Every scripted tool call executed (no validation/authorization/tool errors),
    # and the first observation carried the new sheet id back to the model.
    kinds = [tc["observation"]["kind"] for tc in session.tool_calls]
    assert kinds == ["executed", "executed", "executed", "executed", "executed"]
    assert session.tool_calls[0]["observation"]["data"]["sheet"] == SHEET


def test_workspace_session_with_none_sheet_does_not_crash_on_missing_snapshot():
    """A workspace session must never pre-fetch or require a sheet snapshot. Even
    if the model (wrongly) calls getSheetSnapshot before any sheet exists, the loop
    surfaces a clean observation instead of crashing — and a well-behaved run that
    only calls createSheet first works with snapshot_fn absent entirely."""
    # snapshot_fn present but the sheet does not exist yet -> clean not_found obs.
    def snapshot_fn(sheet_name, actor):
        raise KeyError(sheet_name)  # in-memory "no such sheet"

    turns = [
        {"content": None, "tool_calls": [_tool("getSheetSnapshot", {"sheet": "nope"}, "s1")]},
        {"content": "no sheet yet", "tool_calls": []},
    ]
    session, repo, sink = _run(turns, snapshot_fn=snapshot_fn)
    assert session.tool_calls[0]["observation"]["kind"] == "not_found"
    assert sink.events == []  # nothing mutated

    # And the happy path works with NO snapshot_fn injected at all (workspace start).
    session2, repo2, _ = _run(_workspace_turns(), snapshot_fn=None)
    assert session2.terminated_by == "final"
    assert repo2.get_process(SHEET).enabled is True
