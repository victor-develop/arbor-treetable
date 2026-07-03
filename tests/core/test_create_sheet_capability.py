"""createSheet — the self-service sheet-bootstrap CAPABILITY (control-cap path).

Promoted from a standalone REST shim to a registry capability routed through the
ONE ``execute_action`` (Axis.NONE, emits=(), is_exposed_to_llm=True). Tests the
capability contract, the "authenticated (non-Guest)" ACL gate, the creator ->
structural_owner invariant, and cross-surface parity (in-process vs agent).
"""

from __future__ import annotations

import pytest

from arbor.arbor.agent.react import run_agent_session
from arbor.core.executor import execute_action
from arbor.core.registry import get_capability, get_llm_tools
from arbor.core.testing import InMemoryRepository, MockLLMProvider, RecordingEventSink
from arbor.core.types import (
    Axis,
    Actor,
    ActorType,
    AuthorizationError,
    Operation,
    TargetKind,
)

CREATOR = "creator"


def _fresh():
    return InMemoryRepository(), RecordingEventSink()


# --- capability contract ----------------------------------------------------
def test_create_sheet_capability_contract():
    cap = get_capability("createSheet")
    assert cap.axis == Axis.NONE
    assert cap.target_kind == TargetKind.NONE
    assert cap.operation == Operation.NONE
    assert cap.is_exposed_to_llm is True  # the workspace agent may create a sheet
    assert cap.emits == ()  # NO Tree Event — the closed 11-event set is preserved
    # title is the only required param; name / label_column are optional.
    assert cap.params_schema["required"] == ["title"]
    # exposed as an LLM tool.
    assert "createSheet" in {t["function"]["name"] for t in get_llm_tools()}


# --- creator becomes structural_owner + default label column ----------------
def test_create_sheet_makes_creator_the_structural_owner_with_label_column():
    repo, sink = _fresh()
    out = execute_action(
        "createSheet",
        {"title": "Roadmap", "name": "roadmap", "label_column": "Initiative"},
        Actor(CREATOR, ActorType.HUMAN),
        repo,
        sink,
    )
    assert out.kind == "executed"
    sheet = out.data["sheet"]
    assert sheet == "roadmap"
    assert repo.get_sheet(sheet).structural_owner == CREATOR
    # a default LABEL column owned by the creator, carrying the label_column text.
    labels = [c for c in repo.list_columns(sheet) if c.is_label]
    assert len(labels) == 1
    assert labels[0].column_owner == CREATOR
    assert labels[0].label == "Initiative"
    # emits=() -> no Tree Event on the closed stream.
    assert sink.events == []


def test_create_sheet_default_label_text_is_item():
    repo, sink = _fresh()
    out = execute_action(
        "createSheet", {"title": "Untitled"}, Actor(CREATOR, ActorType.HUMAN), repo, sink
    )
    sheet = out.data["sheet"]
    label = next(c for c in repo.list_columns(sheet) if c.is_label)
    assert label.label == "Item"


# --- ACL: any authenticated non-Guest may create; a Guest is denied ---------
def test_create_sheet_allows_any_authenticated_user():
    """Permissive self-service gate — NOT admin-only. A plain (non-admin) user
    creates a sheet directly (executed), never a Change Request."""
    repo, sink = _fresh()
    out = execute_action(
        "createSheet",
        {"title": "Mine"},
        Actor("plain-user", ActorType.HUMAN, is_admin=False),
        repo,
        sink,
    )
    assert out.kind == "executed"
    assert repo.get_sheet(out.data["sheet"]).structural_owner == "plain-user"


def test_create_sheet_denied_for_guest():
    """A Guest (unauthenticated) is denied with a hard AuthorizationError (403),
    never a Change Request — there is no sheet to route one to."""
    repo, sink = _fresh()
    with pytest.raises(AuthorizationError):
        execute_action(
            "createSheet", {"title": "X"}, Actor("Guest", ActorType.HUMAN), repo, sink
        )
    assert repo.sheets == {}
    assert sink.events == []


# --- cross-surface parity: in-process vs agent tool-call --------------------
def test_create_sheet_parity_inprocess_vs_agent():
    """createSheet produces the identical outcome + sheet on both the in-process
    executor path and the agent tool-call path (only actor_type differs)."""
    params = {"title": "Parity", "name": "parity-sheet", "label_column": "Row"}

    ip_repo, ip_sink = _fresh()
    ip_out = execute_action(
        "createSheet", dict(params), Actor(CREATOR, ActorType.HUMAN), ip_repo, ip_sink
    )

    ag_repo, ag_sink = _fresh()
    provider = MockLLMProvider(
        [
            {"content": None, "tool_calls": [
                {"id": "t1", "name": "createSheet", "arguments": dict(params)}]},
            {"content": "done", "tool_calls": []},
        ]
    )
    ag_session = run_agent_session(
        "make it", Actor(CREATOR, ActorType.AGENT), ag_repo, ag_sink, provider, max_steps=4
    )
    ag_obs = ag_session.tool_calls[0]["observation"]

    # Same outcome kind, same created sheet id, same creator-as-owner invariant.
    assert ip_out.kind == ag_obs["kind"] == "executed"
    assert ip_out.data["sheet"] == ag_obs["data"]["sheet"] == "parity-sheet"
    assert ip_repo.get_sheet("parity-sheet").structural_owner == CREATOR
    assert ag_repo.get_sheet("parity-sheet").structural_owner == CREATOR
    # Neither surface emitted a Tree Event (emits=()).
    assert ip_sink.events == [] and ag_sink.events == []
