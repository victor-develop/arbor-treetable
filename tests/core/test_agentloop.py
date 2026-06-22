"""Re-Act loop control flow against a scripted MockLLMProvider (ARCHITECTURE §8).

Proves: the agent is subject to the same ACL (an unauthorized agent tool call
becomes a Change Request), tools come from getLLMTools (internalReset absent),
and the loop terminates on a no-tool-call turn."""

from __future__ import annotations

from arbor.core.agentloop import run_agent
from arbor.core.executor import execute_action
from arbor.core.testing import MockLLMProvider, RecordingEventSink
from arbor.core.types import Actor, ActorType
from tests.fixtures.canonical import AGENT, seed_canonical_sheet


def _make_execute_tool(fx, sink):
    agent_actor = Actor(AGENT, ActorType.AGENT)

    def execute_tool(name, args):
        outcome = execute_action(name, args, agent_actor, fx.repo, sink)
        return {"kind": outcome.kind, "change_request": outcome.change_request}

    return execute_tool


def test_loop_terminates_on_final_answer():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    provider = MockLLMProvider([{"content": "Nothing to do.", "tool_calls": []}])
    result = run_agent("hello", provider, _make_execute_tool(fx, sink))
    assert result.final_message == "Nothing to do."
    assert result.tool_calls == []


def test_agent_unauthorized_action_becomes_change_request():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    # AGENT owns nothing → updateCell becomes a CR (no privilege escalation).
    provider = MockLLMProvider(
        [
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "t1",
                        "name": "updateCell",
                        "arguments": {
                            "sheet": fx.sheet,
                            "node": fx.X,
                            "column": fx.col_status,
                            "value": "done",
                        },
                    }
                ],
            },
            {"content": "Filed a change request for C.", "tool_calls": []},
        ]
    )
    result = run_agent("set X status done", provider, _make_execute_tool(fx, sink))
    assert result.tool_calls[0]["observation"]["kind"] == "suggested"
    assert sink.types() == ["CHANGE_PROPOSED"]
    assert "change request" in result.final_message.lower()


def test_loop_passes_llm_tools_to_provider():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    provider = MockLLMProvider([{"content": "done", "tool_calls": []}])
    run_agent("hi", provider, _make_execute_tool(fx, sink))
    tool_names = {t["function"]["name"] for t in provider.calls[0]["tools"]}
    assert "internalReset" not in tool_names
    assert "updateCell" in tool_names
