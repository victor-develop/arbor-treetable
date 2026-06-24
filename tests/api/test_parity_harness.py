"""Cross-surface parity harness — TEST-PLAN §5.4 (the PRIMARY invariant).

Runnable: **bench-free** (plain pytest, no Frappe bench, no running app).

ARCHITECTURE §11 / PERMISSIONS §4.6: a capability+actor produces the *identical*
authority decision, mutation, and Tree Event regardless of surface. The three
surfaces all funnel through the ONE ``arbor.core.executor.execute_action``:

* **in-process** ``execute_action(...)`` — the web ``executeAction`` path and the
  in-process peer the REST method delegates to verbatim (``arbor.api._dispatch``
  is a thin frappe shim around this exact call);
* **agent tool-call** — ``arbor.arbor.agent.react.run_agent_session`` routes each
  scripted ``MockLLMProvider`` tool call through the same ``execute_action`` as
  the agent's OWN user.

Rather than re-deriving the REST path (which needs a bench — covered in
``test_rest_parity_bench.py``), this harness proves the *shared core* is the
single point of truth: it compares the in-process Outcome/event against the
agent-surface Outcome/event for the same params+actor on identically reset
fixtures, asserting field-for-field equality modulo ids/timestamps and the one
documented difference (``actor_type``: ``human`` in-process / REST vs ``agent``).

It also pins the registry→REST reachability contract (API-013) and the
``internalReset`` exclusion, and adds the bench-free half of the ``unsubscribe``
surface-parity gap flagged in TEST-PLAN §5.1 (a matching REST case lives in
``test_rest_parity_bench.py`` and a Web-UI note is recorded for the frontend
lane).

Covers: API-010, API-011, API-012, API-013, API-149 (core half), AGENT-041,
AGENT-042, and the §5.4 cross-surface harness; TEST-PLAN §5.1 unsubscribe gap.
"""

from __future__ import annotations

import pytest

from arbor.arbor.agent.react import run_agent_session
from arbor.core.executor import execute_action
from arbor.core.registry import all_capabilities, get_llm_tools
from arbor.core.testing import MockLLMProvider, RecordingEventSink
from arbor.core.types import Actor, ActorType
from tests.fixtures.canonical import A, B, C, E, seed_canonical_sheet

# Capability ids that have a named whitelisted REST method (arbor.api.*).
# Mirrors ARCHITECTURE §8.1 and the adapter-api build manifest. internalReset is
# present as a method but NOT auto-exposed to the ordinary LLM/whitelist surface
# the way the others are (it is system/admin only) — see test below.
REST_METHODS = {
    "getSheetSnapshot": "get_sheet_snapshot",
    # explore: bounded, navigable LLM read API (each has a named arbor.<m> endpoint)
    "getSheetOverview": "sheet_overview",
    "listChildren": "list_children",
    "getSubtree": "get_subtree",
    "getNode": "get_node",
    "searchNodes": "search_nodes",
    "getCells": "get_cells",
    "addNode": "add_node",
    "updateCell": "update_cell",
    "moveNode": "move_node",
    "deleteNode": "delete_node",
    "addColumn": "add_column",
    "updateColumn": "update_column",
    "deleteColumn": "delete_column",
    "suggestChange": "suggest_change",
    "suggestChanges": "suggest_changes",
    "approveChange": "approve_change",
    "rejectChange": "reject_change",
    "withdrawChange": "withdraw_change",
    "subscribe": "subscribe",
    "unsubscribe": "unsubscribe",
    "acknowledge": "acknowledge",
    "delegateBranch": "delegate_branch",
    "revokeDelegation": "revoke_delegation",
    "grantColumn": "grant_column",
    # role management (Feature: roles) — only the LLM-exposed caps. The admin /
    # decision caps (assignRole, revokeRole, approve/rejectRoleApplication) are
    # is_exposed_to_llm=False (the agent must never self-escalate or approve a
    # role), so they are NOT in the exposed set and intentionally absent here.
    "applyForRole": "apply_for_role",
    "withdrawRoleApplication": "withdraw_role_application",
}


# ---------------------------------------------------------------------------
# Surface drivers — each returns (outcome_kind, event_summary, repo, sink).
# ---------------------------------------------------------------------------
def _event_summary(ev):
    """Compare events field-for-field EXCEPT volatile ids/timestamps and the
    documented ``actor_type`` difference."""
    if ev is None:
        return None
    return {
        "type": ev.type,
        "sheet": ev.sheet,
        "actor": ev.actor,
        "payload": ev.payload,
        "change_request": ev.change_request,
    }


def _drive_in_process(action_id, params, user):
    """The web ``executeAction`` path == the peer the REST method delegates to."""
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    actor = Actor(user, ActorType.HUMAN)
    outcome = execute_action(action_id, params(fx), actor, fx.repo, sink)
    return outcome, sink, fx


def _drive_agent(action_id, params, user):
    """The agent tool-call path: one scripted tool call, then a final turn."""
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    actor = Actor(user, ActorType.AGENT)
    provider = MockLLMProvider(
        [
            {"content": None, "tool_calls": [{"id": "t1", "name": action_id, "arguments": params(fx)}]},
            {"content": "done", "tool_calls": []},
        ]
    )
    session = run_agent_session("do it", actor, fx.repo, sink, provider, max_steps=4)
    obs = session.tool_calls[0]["observation"]
    return obs, sink, fx


# ---------------------------------------------------------------------------
# B. Surface-parity invariant — authorized write (API-010 / AGENT-041)
# ---------------------------------------------------------------------------
def test_authorized_updatecell_parity_inprocess_vs_agent():
    """API-010 + AGENT-041: C (owns col:budget) updateCell → identical authority,
    identical NODE_VALUE_UPDATED payload, identical version bump. Only actor_type
    differs (human vs agent)."""
    params = lambda fx: {"sheet": fx.sheet, "node": fx.Y, "column": fx.col_budget, "value": 42}

    out, ip_sink, ip_fx = _drive_in_process("updateCell", params, C)
    obs, ag_sink, ag_fx = _drive_agent("updateCell", params, C)

    # Both executed; both bumped version on Y/col:budget by exactly 1.
    assert out.kind == "executed"
    assert obs["kind"] == "executed"
    assert ip_fx.repo.versions[(ip_fx.Y, ip_fx.col_budget)] == 2  # seeded at 1
    assert ag_fx.repo.versions[(ag_fx.Y, ag_fx.col_budget)] == 2

    # Exactly one NODE_VALUE_UPDATED on each surface.
    assert ip_sink.types() == ["NODE_VALUE_UPDATED"] == ag_sink.types()

    # Field-for-field event equality modulo ids/timestamps AND actor_type.
    assert _event_summary(ip_sink.last()) == _event_summary(ag_sink.last())
    assert ip_sink.last().actor_type == ActorType.HUMAN
    assert ag_sink.last().actor_type == ActorType.AGENT


# ---------------------------------------------------------------------------
# B. Surface-parity invariant — unauthorized write becomes a CR
#    (API-011 / AGENT-042)
# ---------------------------------------------------------------------------
def test_unauthorized_updatecell_parity_becomes_identical_cr():
    """API-011 + AGENT-042: a suggest-only actor's col:budget edit becomes the
    SAME Change Request (target_kind/operation/payload/requester/approver) and one
    CHANGE_PROPOSED on every surface; no NODE_VALUE_UPDATED."""
    params = lambda fx: {"sheet": fx.sheet, "node": fx.X, "column": fx.col_budget, "value": 7}

    out, ip_sink, ip_fx = _drive_in_process("updateCell", params, E)
    obs, ag_sink, ag_fx = _drive_agent("updateCell", params, E)

    assert out.kind == "suggested"
    assert obs["kind"] == "suggested"
    assert ip_sink.types() == ["CHANGE_PROPOSED"] == ag_sink.types()

    ip_cr = ip_fx.repo.get_change_request(out.change_request)
    ag_cr = ag_fx.repo.get_change_request(obs["change_request"])

    # CRs are equal except for their generated names.
    for key in ("target_kind", "operation", "resolved_approver", "requester", "status"):
        assert ip_cr[key] == ag_cr[key], key
    assert ip_cr["target_kind"] == "cell-value"
    assert ip_cr["operation"] == "update"
    assert ip_cr["resolved_approver"] == C
    assert ip_cr["requester"] == E
    # Payload carries the original params (plus the _action_id stamp) identically.
    assert ip_cr["payload"] == ag_cr["payload"]
    assert ip_cr["payload"]["_action_id"] == "updateCell"

    # No mutation on either surface.
    assert ip_fx.repo.get_value(ip_fx.X, ip_fx.col_budget) == 1000
    assert ag_fx.repo.get_value(ag_fx.X, ag_fx.col_budget) == 1000


def test_unauthorized_structural_add_parity():
    """API-041 + AGENT-014: column owner B's structural add → CR to A on both
    surfaces (Axis-1 walk, no grant)."""
    params = lambda fx: {"sheet": fx.sheet, "parent": fx.P1}

    out, ip_sink, ip_fx = _drive_in_process("addNode", params, B)
    obs, ag_sink, ag_fx = _drive_agent("addNode", params, B)

    assert out.kind == obs["kind"] == "suggested"
    assert ip_sink.types() == ["CHANGE_PROPOSED"] == ag_sink.types()
    assert ip_fx.repo.get_change_request(out.change_request)["resolved_approver"] == A
    assert ag_fx.repo.get_change_request(obs["change_request"])["resolved_approver"] == A
    assert ip_fx.repo.get_change_request(out.change_request)["target_kind"] == "node-structure"


# ---------------------------------------------------------------------------
# B. execute_action generic dispatch ≡ named capability (API-012)
# ---------------------------------------------------------------------------
def test_generic_dispatch_equals_named_capability_core():
    """API-012 (core half): the in-process executor IS the one funnel both the
    generic ``arbor.execute_action`` and the named ``arbor.update_cell`` method
    delegate to. Driving the same action_id twice yields identical event shapes —
    there is no second mutation path to diverge."""
    params = lambda fx: {"sheet": fx.sheet, "node": fx.Z, "column": fx.col_notes, "value": "n"}
    a_out, a_sink, _ = _drive_in_process("updateCell", params, B)
    b_out, b_sink, _ = _drive_in_process("updateCell", params, B)
    assert a_out.kind == b_out.kind == "executed"
    assert _event_summary(a_sink.last()) == _event_summary(b_sink.last())
    assert a_sink.types() == ["NODE_VALUE_UPDATED"]


# ---------------------------------------------------------------------------
# B. Registry → REST reachability completeness (API-013)
# ---------------------------------------------------------------------------
def test_every_llm_capability_has_a_named_rest_method():
    """API-013: every LLM-exposed capability (all except internalReset) is
    reachable via its named ``arbor.<method>`` endpoint AND via generic dispatch;
    internalReset is NOT auto-exposed."""
    exposed = {c.id for c in all_capabilities() if c.is_exposed_to_llm}
    # Named-method coverage is complete for the exposed set.
    assert exposed == set(REST_METHODS)
    assert "internalReset" not in REST_METHODS
    # Generic dispatch reaches every exposed capability by id (registry lookup).
    from arbor.core.registry import has_capability

    for cap_id in exposed:
        assert has_capability(cap_id)


def test_named_rest_methods_exist_on_api_when_importable():
    """API-013 (binding): when the adapter API module imports (frappe present),
    each named method is a real attribute. Bench-free, this asserts the manifest
    of method names; the live whitelist is exercised in the bench tier."""
    try:
        from arbor import api  # noqa: F401  (bench layout)
    except Exception:
        try:
            from arbor.arbor import api  # noqa: F401  (dev layout, needs frappe)
        except Exception:
            pytest.skip("arbor.api requires frappe; method-name manifest asserted above")
            return
    for method in REST_METHODS.values():
        assert hasattr(api, method), method
    assert hasattr(api, "execute_action")
    assert hasattr(api, "get_sheet_snapshot")
    # internalReset method exists but is the system/admin escape hatch.
    assert hasattr(api, "internal_reset")


# ---------------------------------------------------------------------------
# I. internalReset exclusion from the LLM/agent surface (API-149 / AGENT-002)
# ---------------------------------------------------------------------------
def test_internal_reset_absent_from_llm_tools():
    """API-149 + AGENT-002 (core half): internalReset never appears among the
    tools the agent surface offers; it exists in the registry but is filtered."""
    tool_names = {t["function"]["name"] for t in get_llm_tools()}
    assert "internalReset" not in tool_names
    assert any(c.id == "internalReset" for c in all_capabilities())  # exists, hidden


def test_internal_reset_via_agent_is_refused_not_executed():
    """API-149 + AGENT-004: a hallucinated internalReset tool-call is refused by
    the loop guard (tool_error) — it never reaches execute_action; no event."""
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    provider = MockLLMProvider(
        [
            {"content": None, "tool_calls": [{"id": "t1", "name": "internalReset", "arguments": {"sheet": fx.sheet, "confirm": True}}]},
            {"content": "cannot reset", "tool_calls": []},
        ]
    )
    session = run_agent_session("reset S", Actor(A, ActorType.AGENT), fx.repo, sink, provider)
    assert session.tool_calls[0]["observation"]["kind"] == "tool_error"
    assert sink.events == []


# ---------------------------------------------------------------------------
# TEST-PLAN §5.1 — unsubscribe surface-parity gap (bench-free half)
# ---------------------------------------------------------------------------
def test_unsubscribe_parity_inprocess_vs_agent():
    """TEST-PLAN §5.1 gap: ``subscribe`` was tested on all 8 surfaces but
    ``unsubscribe`` was missing from REST and Web-UI. This adds the cross-surface
    ``unsubscribe`` case so the subscription lifecycle is symmetric.

    A REST-tier counterpart lives in ``test_rest_parity_bench.py``; the Web-UI
    counterpart is recorded as a note for the frontend lane (see module docstring
    and the returned manifest)."""
    def _subscribe(fx, user):
        sink = RecordingEventSink()
        out = execute_action(
            "subscribe",
            {"scope": "sheet", "target": fx.sheet, "event_types": ["NODE_VALUE_UPDATED"], "delivery": "in-app"},
            Actor(user, ActorType.HUMAN),
            fx.repo,
            sink,
        )
        return out.data["subscription"]

    # In-process surface.
    ip_fx = seed_canonical_sheet()
    ip_sub = _subscribe(ip_fx, B)
    ip_sink = RecordingEventSink()
    ip_out = execute_action(
        "unsubscribe", {"subscription": ip_sub}, Actor(B, ActorType.HUMAN), ip_fx.repo, ip_sink
    )

    # Agent surface (same actor, own identity).
    ag_fx = seed_canonical_sheet()
    ag_sub = _subscribe(ag_fx, B)
    ag_sink = RecordingEventSink()
    ag_provider = MockLLMProvider(
        [
            {"content": None, "tool_calls": [{"id": "t1", "name": "unsubscribe", "arguments": {"subscription": ag_sub}}]},
            {"content": "unsubscribed", "tool_calls": []},
        ]
    )
    ag_session = run_agent_session("unsubscribe me", Actor(B, ActorType.AGENT), ag_fx.repo, ag_sink, ag_provider)
    ag_obs = ag_session.tool_calls[0]["observation"]

    assert ip_out.kind == "executed"
    assert ag_obs["kind"] == "executed"
    assert ip_sink.types() == ["SUBSCRIPTION_CHANGED"] == ag_sink.types()
    assert ip_sink.last().payload["op"] == "unsubscribe" == ag_sink.last().payload["op"]
    # The subscription row is gone on both surfaces.
    assert ip_sub not in ip_fx.repo.subscriptions
    assert ag_sub not in ag_fx.repo.subscriptions


def test_unsubscribe_by_non_owner_is_denied_on_both_surfaces():
    """§5.1 corollary: only the subscription owner may unsubscribe. In-process →
    AuthorizationError; agent surface → authorization_error observation (no event,
    subscription kept)."""
    # In-process: AuthorizationError.
    ip_fx = seed_canonical_sheet()
    ip_sink = RecordingEventSink()
    sub = execute_action(
        "subscribe",
        {"scope": "sheet", "target": ip_fx.sheet, "event_types": ["NODE_VALUE_UPDATED"], "delivery": "in-app"},
        Actor(B, ActorType.HUMAN),
        ip_fx.repo,
        ip_sink,
    ).data["subscription"]
    from arbor.core.types import AuthorizationError

    with pytest.raises(AuthorizationError):
        execute_action("unsubscribe", {"subscription": sub}, Actor(E, ActorType.HUMAN), ip_fx.repo, RecordingEventSink())

    # Agent surface: surfaced as an authorization_error observation, not a crash.
    ag_sink = RecordingEventSink()
    provider = MockLLMProvider(
        [
            {"content": None, "tool_calls": [{"id": "t1", "name": "unsubscribe", "arguments": {"subscription": sub}}]},
            {"content": "cannot", "tool_calls": []},
        ]
    )
    session = run_agent_session("unsubscribe", Actor(E, ActorType.AGENT), ip_fx.repo, ag_sink, provider)
    assert session.tool_calls[0]["observation"]["kind"] == "authorization_error"
    assert ag_sink.events == []
    assert sub in ip_fx.repo.subscriptions  # not removed
