"""Server-side Re-Act session runner (ARCHITECTURE §8).

This is the Frappe-side wrapper around the framework-free loop
``arbor.core.agentloop.run_agent``. It owns ZERO mutation/ACL/reasoning logic; it
only:

1. builds the ``execute_tool`` closure that routes each tool call through the ONE
   ``arbor.core.executor.execute_action`` **as the caller's OWN Frappe User**
   (so an unauthorized agent action becomes a Change Request — never a privileged
   bypass; PERMISSIONS invariant #5);
2. guards tool dispatch — unknown/hidden tools (e.g. a hallucinated
   ``internalReset``), schema-validation failures, authorization failures on
   control capabilities, and bad references — are surfaced back to the model as
   structured **Observations** rather than crashing the loop (AGENT-004, 031,
   032, 039, 046, 047, 048);
3. wraps the loop to add the streamed Thought/Action/Observation transcript and a
   ``terminated_by`` reason (``final`` | ``max_steps`` | ``provider_error``;
   AGENT-006, 009, 040).

The Repository + EventSink are injected (so this is unit-testable with the pure
in-memory doubles); on a live site ``arbor.agent.chat`` supplies the Frappe
adapter implementations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from arbor.core.agentloop import run_agent
from arbor.core.executor import execute_action
from arbor.core.ports import EventSink, Repository
from arbor.core.registry import get_capability
from arbor.core.types import (
    Actor,
    ActorType,
    AuthorizationError,
    SchemaValidationError,
    UnknownCapabilityError,
)

from .tools import exposed_tool_names

# A turn observation that the loop feeds back to the provider, and the transcript
# records. ``kind`` is one of: executed | suggested | read | tool_error |
# validation_error | authorization_error | not_found.
Observation = dict[str, Any]

# A read serializer the chat endpoint injects so getSheetSnapshot returns the
# canonical snapshot shape (the SAME serializer web/REST use). Signature:
# ``(sheet_name, actor) -> dict``. Optional; if absent, the loop returns the read
# stub from the core executor.
SnapshotFn = Callable[[str, Actor], dict[str, Any]]


@dataclass
class AgentSession:
    """The streamed result of one chat turn — a superset of core ``AgentResult``
    carrying the ordered Thought/Action/Observation transcript and the
    termination reason for the thin React shell to render."""

    final_message: str
    transcript: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    terminated_by: str = "final"  # final | max_steps | provider_error

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_DEFAULT_SYSTEM = (
    "You are Arbor's assistant operating on a governed tree-table. Use the "
    "provided tools to read and change the sheet. You act under the user's own "
    "identity and the same permissions: when you lack authority an action becomes "
    "a Change Request routed to the right owner for approval — tell the user when "
    "that happens. "
    "Read before mutating, but do NOT pull a whole sheet blindly: large trees can "
    "be thousands of nodes. Prefer getSheetOverview first to learn the structure "
    "(it is always safe and returns no cell payload), then navigate "
    "piece-by-piece with listChildren, getSubtree, getNode, searchNodes and "
    "getCells. Reserve getSheetSnapshot for small sheets — if it returns a "
    "SheetTooLargeError, that is your cue to switch to the explore tools "
    "(getSheetOverview then listChildren / getSubtree) and never retry the "
    "snapshot. "
    "Finish with a concise natural-language summary of what you did and which "
    "Change Requests you filed and to whom."
)


def make_execute_tool(
    actor: Actor,
    repo: Repository,
    sink: EventSink,
    snapshot_fn: Optional[SnapshotFn] = None,
) -> Callable[[str, dict[str, Any]], Observation]:
    """Build the tool executor closure for ``run_agent``.

    Every call funnels through the ONE ``execute_action`` as ``actor`` (the
    caller's own User). Failures become structured observations so the loop keeps
    going and the model can re-plan.
    """
    allowed = exposed_tool_names()

    def execute_tool(name: str, args: dict[str, Any]) -> Observation:
        # (1) Hidden/unknown tool guard — never dispatch internalReset or a
        # hallucinated name to execute_action (AGENT-004).
        if name not in allowed:
            return {
                "kind": "tool_error",
                "error": f"unknown or unavailable tool: {name!r}",
            }

        # (2) Read short-circuit: return the canonical snapshot via the SAME
        # serializer web/REST use, with the actor's ACL hints (AGENT-005).
        if name == "getSheetSnapshot" and snapshot_fn is not None:
            try:
                snapshot = snapshot_fn(args["sheet"], actor)
            except AuthorizationError as exc:
                return {"kind": "authorization_error", "error": str(exc)}
            except KeyError as exc:
                return {"kind": "not_found", "error": f"missing argument: {exc}"}
            except Exception as exc:  # not-found / no-view -> clean observation
                return {"kind": "not_found", "error": str(exc)}
            return {"kind": "read", "data": snapshot}

        try:
            outcome = execute_action(name, args, actor, repo, sink)
        except SchemaValidationError as exc:
            # (3) Bad/missing tool arguments — surfaced, not executed (AGENT-039).
            return {"kind": "validation_error", "error": str(exc)}
        except AuthorizationError as exc:
            # (4) Denied control op (approve/withdraw not yours, etc.) — surfaced;
            # does NOT itself spawn a CR (AGENT-031, 032).
            return {"kind": "authorization_error", "error": str(exc)}
        except UnknownCapabilityError as exc:  # pragma: no cover - guarded above
            return {"kind": "tool_error", "error": str(exc)}
        except (KeyError, ValueError) as exc:
            # (5) Bad reference (unknown node/column/sheet) — clean tool error,
            # not a crash (AGENT-046, 047, 048).
            return {"kind": "not_found", "error": str(exc)}

        return {
            "kind": outcome.kind,
            "change_request": outcome.change_request,
            "event": _event_summary(outcome),
            "data": outcome.data,
        }

    return execute_tool


def _event_summary(outcome: Any) -> Optional[dict[str, Any]]:
    ev = getattr(outcome, "event", None)
    if ev is None:
        return None
    return {
        "event_id": ev.event_id,
        "type": ev.type,
        "actor": ev.actor,
        "actor_type": ev.actor_type.value
        if hasattr(ev.actor_type, "value")
        else ev.actor_type,
        "change_request": ev.change_request,
    }


def run_agent_session(
    message: str,
    actor: Actor,
    repo: Repository,
    sink: EventSink,
    provider: Any,
    snapshot_fn: Optional[SnapshotFn] = None,
    system: Optional[str] = None,
    max_steps: int = 12,
) -> AgentSession:
    """Run one chat turn through the core Re-Act loop and build the streamed
    transcript.

    ``provider`` is any object implementing the core ``LLMProvider`` protocol
    (the LiteLLM adapter on a live site; ``MockLLMProvider`` in tests).
    """
    execute_tool = make_execute_tool(actor, repo, sink, snapshot_fn)
    terminated_by = "final"

    try:
        result = run_agent(
            message,
            provider,
            execute_tool,
            system=system or _DEFAULT_SYSTEM,
            max_steps=max_steps,
        )
    except Exception as exc:  # provider transport-style error (AGENT-040)
        return AgentSession(
            final_message=f"The assistant stopped due to a provider error: {exc}",
            transcript=[{"kind": "final", "content": "", "terminated_by": "provider_error"}],
            tool_calls=[],
            terminated_by="provider_error",
        )

    # Detect the max-steps guard: the loop ran the full budget AND the last step
    # still requested tool calls (it never produced a tool-free final turn).
    if len(result.steps) >= max_steps and result.steps and result.steps[-1].get("tool_calls"):
        terminated_by = "max_steps"

    transcript = _build_transcript(result, terminated_by)
    return AgentSession(
        final_message=result.final_message,
        transcript=transcript,
        tool_calls=result.tool_calls,
        terminated_by=terminated_by,
    )


def _build_transcript(result: Any, terminated_by: str) -> list[dict[str, Any]]:
    """Render the core ``AgentResult.steps`` into an ordered
    Thought/Action/Observation/Final transcript.

    Re-Act ordering invariant: every Action entry is immediately followed by its
    Observation, so ``count(action) == count(observation)`` (AGENT-006).
    """
    transcript: list[dict[str, Any]] = []
    tool_calls = list(result.tool_calls)
    obs_idx = 0

    for step in result.steps:
        content = step.get("content")
        calls = step.get("tool_calls") or []
        if content:
            transcript.append({"kind": "thought", "content": content})
        for call in calls:
            transcript.append(
                {
                    "kind": "action",
                    "tool": call["name"],
                    "arguments": call.get("arguments") or {},
                }
            )
            observation = (
                tool_calls[obs_idx]["observation"]
                if obs_idx < len(tool_calls)
                else None
            )
            obs_idx += 1
            transcript.append({"kind": "observation", "observation": observation})

    transcript.append(
        {
            "kind": "final",
            "content": result.final_message,
            "terminated_by": terminated_by,
        }
    )
    return transcript
