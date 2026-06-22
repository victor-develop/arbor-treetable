"""Provider-agnostic Re-Act loop (ARCHITECTURE §8).

Pure control flow: the loop is parameterized by an ``LLMProvider`` (LiteLLM in
the adapter, ``MockLLMProvider`` in tests) and an injected ``execute_tool``
callable that routes a tool call through ``execute_action`` as the AGENT's own
user. The agent therefore owns ZERO mutation logic and is subject to the same
two-axis ACL — an unauthorized agent action becomes a Change Request, never a
privileged bypass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .ports import LLMProvider
from .registry import get_llm_tools

# Routes a single tool call to execute_action as the agent user; returns a
# JSON-serializable observation (typically the Outcome rendered to a dict).
ExecuteTool = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass
class AgentResult:
    """Outcome of one ``run_agent`` invocation."""

    final_message: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def run_agent(
    message: str,
    provider: LLMProvider,
    execute_tool: ExecuteTool,
    system: str | None = None,
    max_steps: int = 12,
) -> AgentResult:
    """Run the Re-Act loop until the provider returns no tool calls (final
    answer) or ``max_steps`` is hit.

    Each step: provider.complete(messages, tools) -> assistant turn. If it
    contains tool calls, each is routed through ``execute_tool`` and its
    observation appended as a tool message; the loop continues. Otherwise the
    assistant ``content`` is the final answer.
    """
    tools = get_llm_tools()
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": message})

    result = AgentResult(final_message="")

    for _ in range(max_steps):
        turn = provider.complete(messages, tools)
        content = turn.get("content")
        calls = turn.get("tool_calls") or []

        messages.append(
            {"role": "assistant", "content": content, "tool_calls": calls}
        )
        result.steps.append({"content": content, "tool_calls": calls})

        if not calls:
            result.final_message = content or ""
            return result

        for call in calls:
            name = call["name"]
            args = call.get("arguments") or {}
            observation = execute_tool(name, args)
            result.tool_calls.append({"name": name, "arguments": args, "observation": observation})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", name),
                    "name": name,
                    "content": observation,
                }
            )

    # Loop budget exhausted — surface what we have.
    result.final_message = (
        result.steps[-1].get("content") if result.steps else ""
    ) or "Agent reached the step limit without a final answer."
    return result
