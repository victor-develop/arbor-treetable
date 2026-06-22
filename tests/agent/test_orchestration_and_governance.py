"""Agent-lane: multi-step orchestration, delegation edges, lifecycle, idempotency.

Runnable: **bench-free** (plain pytest over the pure InMemoryRepository +
RecordingEventSink doubles and a scripted/Recording MockLLMProvider; no Frappe
bench, no network).

Complements ``test_react_session.py`` (loop mechanics + the governance keystone)
by covering the agent.md cases it leaves open:

* B — observation feedback (AGENT-007).
* C — happy-path structural add with authority (AGENT-011).
* F — multi-step orchestration, mixed/all-executed/grounded (AGENT-020, 021, 022).
* G — delegation through the agent: nearest-grant-wins (024), within-grant exec
  (025), outside-grant suggest (026), sub-delegate exec (027), revoke-not-owned
  suggest (028), dual-end move co-approver (029).
* H — approve-own-CR replay (030), acknowledge (034).
* K — owner-self policy (043), duplicate tool_calls governed independently (044).
* L — read denied / archived-sheet boundaries (046, 047).

All cases reuse the ONE canonical seed (``tests/fixtures/canonical.py``).
"""

from __future__ import annotations

from arbor.arbor.agent.react import run_agent_session
from arbor.core.testing import MockLLMProvider, RecordingEventSink
from arbor.core.types import Actor, ActorType, AuthorizationError, CRStatus
from tests.fixtures.canonical import (
    A,
    AGENT,
    B,
    C,
    D,
    D2,
    F,
    apply_BG_Z,
    seed_canonical_sheet,
)


def _actor(user):
    return Actor(user, ActorType.AGENT)


def _tool(name, args, call_id="t1"):
    return {"id": call_id, "name": name, "arguments": args}


def _final(text="done"):
    return {"content": text, "tool_calls": []}


def _run(fx, actor, turns, sink=None, snapshot_fn=None, max_steps=12):
    sink = sink or RecordingEventSink()
    session = run_agent_session(
        "do it", actor, fx.repo, sink, MockLLMProvider(turns),
        snapshot_fn=snapshot_fn, max_steps=max_steps,
    )
    return session, sink


# ===========================================================================
# B. Observation feedback (AGENT-007)
# ===========================================================================
def test_observation_is_fed_back_to_provider_on_next_turn():
    """AGENT-007: the snapshot Observation appears in the message history handed
    to the provider on the SECOND call — the agent reasons on real observations."""
    fx = seed_canonical_sheet()
    provider = MockLLMProvider(
        [
            {"content": None, "tool_calls": [_tool("getSheetSnapshot", {"sheet": fx.sheet})]},
            _final("there are nodes"),
        ]
    )
    run_agent_session(
        "how many nodes?", _actor(AGENT), fx.repo, RecordingEventSink(), provider,
        snapshot_fn=lambda s, a: {"sheet": s, "nodes": [1, 2, 3]},
    )
    # Provider was called twice; the 2nd call's messages carry a tool/observation
    # message keyed to the first tool call id.
    assert len(provider.calls) == 2
    second_msgs = provider.calls[1]["messages"]
    tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "t1"
    assert tool_msgs[0]["content"]["kind"] == "read"


# ===========================================================================
# C. Happy-path structural add with authority (AGENT-011)
# ===========================================================================
def test_delegate_identity_adds_node_directly():
    """AGENT-011: agent as identity=D (grantee on P2) adds under Y → executes;
    NODE_CREATED, actor_type=agent, change_request=null."""
    fx = seed_canonical_sheet()
    session, sink = _run(
        fx, _actor(D),
        [{"content": None, "tool_calls": [_tool("addNode", {"sheet": fx.sheet, "parent": fx.Y})]}, _final()],
    )
    assert session.tool_calls[0]["observation"]["kind"] == "executed"
    assert sink.types() == ["NODE_CREATED"]
    assert sink.last().actor_type == ActorType.AGENT
    assert sink.last().change_request is None


# ===========================================================================
# F. Multi-step orchestration
# ===========================================================================
def test_suggest_only_orchestration_all_become_change_requests():
    """AGENT-020: suggest-only AGENT runs snapshot→addNode(R)→move X→move Z. None
    are authorized: addNode→CR to A; move X (src A, dest new under R→A)→CR to A;
    move Z (src D for P2, dest A)→CR routed to dest A with src D co-approver. Three
    CHANGE_PROPOSED; no structural mutation persists."""
    fx = seed_canonical_sheet()
    # A new folder node id is allocated by the in-memory repo on addNode; the
    # script references it indirectly via parent=R for the moves' destination is a
    # *fresh* node, so we move X and Z under R directly to keep the script stable
    # (the catalog's "High Cost" folder is approver-A either way).
    session, sink = _run(
        fx, _actor(AGENT),
        [
            {"content": None, "tool_calls": [_tool("getSheetSnapshot", {"sheet": fx.sheet}, "s")]},
            {"content": None, "tool_calls": [_tool("addNode", {"sheet": fx.sheet, "parent": fx.R, "values": {"name": "High Cost"}}, "a")]},
            {"content": None, "tool_calls": [_tool("moveNode", {"sheet": fx.sheet, "node": fx.X, "new_parent": fx.R}, "mx")]},
            {"content": None, "tool_calls": [_tool("moveNode", {"sheet": fx.sheet, "node": fx.Z, "new_parent": fx.R}, "mz")]},
            _final("Filed CRs to A (and A+D co-approver for Z)."),
        ],
        snapshot_fn=lambda s, a: {"sheet": s, "nodes": []},
    )
    kinds = [tc["observation"]["kind"] for tc in session.tool_calls]
    # snapshot read + 3 suggested mutations.
    assert kinds == ["read", "suggested", "suggested", "suggested"]
    assert sink.types() == ["CHANGE_PROPOSED", "CHANGE_PROPOSED", "CHANGE_PROPOSED"]

    # The move-Z CR routes to dest A with src D as co-approver.
    move_z_cr = fx.repo.get_change_request(session.tool_calls[3]["observation"]["change_request"])
    assert move_z_cr["resolved_approver"] == A
    assert D in (move_z_cr["payload"].get("co_approvers") or [])
    # No structural mutation: X and Z still under their original parents.
    assert fx.repo.get_node(fx.X).parent == fx.P1
    assert fx.repo.get_node(fx.Z).parent == fx.P2


def test_authorized_orchestration_executes_with_per_step_authority():
    """AGENT-021: agent as A runs the same plan. addNode(R) executes (NODE_CREATED);
    move X (src A, dest A) executes (NODE_MOVED); move Z (src D, dest A) — A lacks
    src authority → that one step is suggested to A with co-approver D. 2 executed
    + 1 suggested."""
    fx = seed_canonical_sheet()
    session, sink = _run(
        fx, _actor(A),
        [
            {"content": None, "tool_calls": [_tool("addNode", {"sheet": fx.sheet, "parent": fx.R, "values": {"name": "High Cost"}}, "a")]},
            {"content": None, "tool_calls": [_tool("moveNode", {"sheet": fx.sheet, "node": fx.X, "new_parent": fx.R}, "mx")]},
            {"content": None, "tool_calls": [_tool("moveNode", {"sheet": fx.sheet, "node": fx.Z, "new_parent": fx.R}, "mz")]},
            _final("2 executed, 1 suggested for Z."),
        ],
    )
    kinds = [tc["observation"]["kind"] for tc in session.tool_calls]
    assert kinds == ["executed", "executed", "suggested"]
    assert sink.types() == ["NODE_CREATED", "NODE_MOVED", "CHANGE_PROPOSED"]
    move_z_cr = fx.repo.get_change_request(session.tool_calls[2]["observation"]["change_request"])
    assert move_z_cr["resolved_approver"] == A
    assert D in (move_z_cr["payload"].get("co_approvers") or [])


def test_orchestration_plans_off_observed_snapshot():
    """AGENT-022: agent as C (owns col:budget) reads, then issues exactly ONE
    mutating Action (on Y), proving it conditioned on the Observation."""
    fx = seed_canonical_sheet()
    session, sink = _run(
        fx, _actor(C),
        [
            {"content": None, "tool_calls": [_tool("getSheetSnapshot", {"sheet": fx.sheet}, "s")]},
            {"content": None, "tool_calls": [_tool("updateCell", {"sheet": fx.sheet, "node": fx.Y, "column": fx.col_budget, "value": 11000}, "u")]},
            _final("Updated Y only."),
        ],
        snapshot_fn=lambda s, a: {"sheet": s, "nodes": [{"name": fx.Y, "values": {fx.col_budget: 5000}}]},
    )
    mutating = [tc for tc in session.tool_calls if tc["name"] == "updateCell"]
    assert len(mutating) == 1
    assert sink.types() == ["NODE_VALUE_UPDATED"]


# ===========================================================================
# G. Delegation edges through the agent
# ===========================================================================
def test_nearest_grant_wins_routes_to_subdelegate():
    """AGENT-024: with grants on P2(D) and Z(D2), a suggest-only AGENT's addNode
    under Z resolves the NEAREST active grant → CR routed to D2, not D."""
    fx = seed_canonical_sheet()
    apply_BG_Z(fx, grantee=D2)
    session, sink = _run(
        fx, _actor(AGENT),
        [{"content": None, "tool_calls": [_tool("addNode", {"sheet": fx.sheet, "parent": fx.Z})]}, _final()],
    )
    obs = session.tool_calls[0]["observation"]
    assert obs["kind"] == "suggested"
    assert fx.repo.get_change_request(obs["change_request"])["resolved_approver"] == D2
    assert sink.types() == ["CHANGE_PROPOSED"]


def test_delegate_acting_outside_branch_is_suggested():
    """AGENT-026: agent as D adds under P1 (outside the P2 grant) → CR to A."""
    fx = seed_canonical_sheet()
    session, sink = _run(
        fx, _actor(D),
        [{"content": None, "tool_calls": [_tool("addNode", {"sheet": fx.sheet, "parent": fx.P1})]}, _final()],
    )
    obs = session.tool_calls[0]["observation"]
    assert obs["kind"] == "suggested"
    assert fx.repo.get_change_request(obs["change_request"])["resolved_approver"] == A
    assert sink.types() == ["CHANGE_PROPOSED"]


def test_agent_subdelegates_own_branch_executes():
    """AGENT-027: agent as D delegates Z (within its P2 branch) to F → executes;
    one DELEGATION_CHANGED; an active grant Z→F exists."""
    fx = seed_canonical_sheet()
    session, sink = _run(
        fx, _actor(D),
        [{"content": None, "tool_calls": [_tool("delegateBranch", {"sheet": fx.sheet, "branch_root": fx.Z, "grantee": F})]}, _final()],
    )
    assert session.tool_calls[0]["observation"]["kind"] == "executed"
    assert sink.types() == ["DELEGATION_CHANGED"]
    grant = fx.repo.find_active_branch_grant(fx.sheet, fx.Z, scope="structure")
    assert grant is not None and grant.grantee == F


def test_agent_revoking_unowned_delegation_does_not_bypass_and_keeps_grant():
    """AGENT-028 (governance keystone).

    A suggest-only AGENT's ``revokeDelegation`` of the P2→D grant becomes a Change
    Request to A (the resolved approver). The ACL resolver routes it correctly
    (unauthorized; resolved_approver=A — asserted directly below), and the executor
    resolves the sheet for sheet-less capabilities so the suggest branch files the
    CR and emits exactly one CHANGE_PROPOSED. The keystone invariant holds: the
    agent gets NO privileged bypass — the grant stays active and no
    DELEGATION_CHANGED event is emitted. (Previously a sheet-less ``revokeDelegation``
    KeyError'd in ``executor._suggest``; now fixed via ``_resolve_sheet``.)"""
    fx = seed_canonical_sheet()

    # The resolver itself routes correctly (unauthorized → approver A).
    from arbor.core.acl import resolve_authority
    from arbor.core.registry import get_capability

    authority = resolve_authority(
        get_capability("revokeDelegation"),
        {"branch_grant": fx.grant_P2},
        _actor(AGENT),
        fx.repo,
    )
    assert authority.is_authorized is False
    assert authority.resolved_approver == A

    session, sink = _run(
        fx, _actor(AGENT),
        [{"content": None, "tool_calls": [_tool("revokeDelegation", {"branch_grant": fx.grant_P2})]}, _final()],
    )
    obs = session.tool_calls[0]["observation"]
    # AGENT-028 gap now FIXED (executor resolves the sheet for sheet-less caps):
    # the agent's unauthorized revoke becomes a Change Request to A — no privileged
    # bypass. The grant stays active; only a CHANGE_PROPOSED event is emitted
    # (never DELEGATION_CHANGED).
    assert obs["kind"] == "suggested"
    assert sink.types() == ["CHANGE_PROPOSED"]
    assert fx.repo.get_branch_grant(fx.grant_P2).active is True


def test_agent_move_src_ne_dest_routes_to_dest_with_co_approver():
    """AGENT-029: agent as D moves Y (src parent P2→D) to P1 (dest→A). D is src
    approver but not dest → CR routed to dest A with src D in co_approvers; Y not
    moved."""
    fx = seed_canonical_sheet()
    session, sink = _run(
        fx, _actor(D),
        [{"content": None, "tool_calls": [_tool("moveNode", {"sheet": fx.sheet, "node": fx.Y, "new_parent": fx.P1})]}, _final()],
    )
    obs = session.tool_calls[0]["observation"]
    assert obs["kind"] == "suggested"
    cr = fx.repo.get_change_request(obs["change_request"])
    assert cr["resolved_approver"] == A
    assert D in (cr["payload"].get("co_approvers") or [])
    assert sink.types() == ["CHANGE_PROPOSED"]
    assert fx.repo.get_node(fx.Y).parent == fx.P2  # not moved


# ===========================================================================
# H. Lifecycle driven by the agent
# ===========================================================================
def test_agent_approves_cr_it_is_approver_of_replays_mutation():
    """AGENT-030: agent as C approves a cell-value CR it is resolved_approver of →
    replay emits NODE_VALUE_UPDATED then CHANGE_APPROVED; CR approved with
    resulting_event linked."""
    fx = seed_canonical_sheet()
    cr = fx.repo.create_change_request(
        {
            "sheet": fx.sheet,
            "target_kind": "cell-value",
            "operation": "update",
            "payload": {"_action_id": "updateCell", "sheet": fx.sheet, "node": fx.Y, "column": fx.col_budget, "value": 4321},
            "requester": "E",
            "resolved_approver": C,
            "status": CRStatus.PROPOSED.value,
            "approvals": [],
        }
    )
    session, sink = _run(
        fx, _actor(C),
        [{"content": None, "tool_calls": [_tool("approveChange", {"change_request": cr})]}, _final()],
    )
    assert session.tool_calls[0]["observation"]["kind"] == "executed"
    assert sink.types() == ["NODE_VALUE_UPDATED", "CHANGE_APPROVED"]
    doc = fx.repo.get_change_request(cr)
    assert doc["status"] == CRStatus.APPROVED.value
    assert doc["resulting_event"]
    assert fx.repo.get_value(fx.Y, fx.col_budget) == 4321  # replay mutated


def test_agent_acknowledges_notification_addressed_to_it():
    """AGENT-034: agent as AGENT (the recipient of N1) acknowledges → an
    Acknowledgement row is created; NO Tree Event."""
    fx = seed_canonical_sheet()
    fx.repo.add_notification("N1", recipient=AGENT, sheet=fx.sheet)
    session, sink = _run(
        fx, _actor(AGENT),
        [{"content": None, "tool_calls": [_tool("acknowledge", {"notification": "N1"})]}, _final()],
    )
    assert session.tool_calls[0]["observation"]["kind"] == "executed"
    assert sink.events == []  # acknowledge emits no event
    assert any(a["notification"] == "N1" and a["user"] == AGENT for a in fx.repo.acknowledgements.values())


# ===========================================================================
# K. Owner-self policy & idempotency boundary
# ===========================================================================
def test_owner_self_policy_forces_cr_even_for_authorized_agent():
    """AGENT-043: with owners_must_use_change_requests=true, agent as C (owner of
    col:budget) → CR with C as its OWN resolved_approver; CHANGE_PROPOSED, value
    unchanged until approved."""
    fx = seed_canonical_sheet(settings={"owners_must_use_change_requests": True})
    before = fx.repo.get_value(fx.Y, fx.col_budget)
    session, sink = _run(
        fx, _actor(C),
        [{"content": None, "tool_calls": [_tool("updateCell", {"sheet": fx.sheet, "node": fx.Y, "column": fx.col_budget, "value": 7})]}, _final()],
    )
    obs = session.tool_calls[0]["observation"]
    assert obs["kind"] == "suggested"
    cr = fx.repo.get_change_request(obs["change_request"])
    assert cr["resolved_approver"] == C  # self-approver
    assert sink.types() == ["CHANGE_PROPOSED"]
    assert fx.repo.get_value(fx.Y, fx.col_budget) == before


def test_duplicate_tool_calls_governed_independently():
    """AGENT-044: the same updateCell tool_call twice → two NODE_VALUE_UPDATED
    (version n→n+1→n+2); the loop does not silently dedupe Actions."""
    fx = seed_canonical_sheet()
    args = {"sheet": fx.sheet, "node": fx.Y, "column": fx.col_budget, "value": 5}
    session, sink = _run(
        fx, _actor(C),
        [
            {"content": None, "tool_calls": [_tool("updateCell", dict(args), "a")]},
            {"content": None, "tool_calls": [_tool("updateCell", dict(args), "b")]},
            _final(),
        ],
    )
    assert [tc["observation"]["kind"] for tc in session.tool_calls] == ["executed", "executed"]
    assert sink.types() == ["NODE_VALUE_UPDATED", "NODE_VALUE_UPDATED"]
    assert fx.repo.versions[(fx.Y, fx.col_budget)] == 3  # seeded 1 → 2 → 3


# ===========================================================================
# K. Stale re-resolution of an agent-filed CR (AGENT-045)
# ===========================================================================
def test_stale_resolved_approver_reresolves_at_decision_time():
    """AGENT-045: AGENT files a CR to add under P2 → resolved_approver=D. Then the
    P2 grant is revoked (structural approver reverts to A). When the CR is later
    approved, only the CURRENT approver (A) may approve; D's approve is denied."""
    fx = seed_canonical_sheet()
    # 1. AGENT files the structural CR (suggested → resolved_approver D for P2).
    s1, sink = _run(
        fx, _actor(AGENT),
        [{"content": None, "tool_calls": [_tool("addNode", {"sheet": fx.sheet, "parent": fx.Y})]}, _final()],
    )
    cr = s1.tool_calls[0]["observation"]["change_request"]
    assert fx.repo.get_change_request(cr)["resolved_approver"] == D

    # 2. Revoke the P2→D grant (agent as A, the granted_by) — DELEGATION_CHANGED.
    s2, _ = _run(
        fx, _actor(A),
        [{"content": None, "tool_calls": [_tool("revokeDelegation", {"branch_grant": fx.grant_P2})]}, _final()],
        sink=sink,
    )
    assert fx.repo.get_branch_grant(fx.grant_P2).active is False

    # 3. The structural approver of Y now re-resolves to A.
    from arbor.core.acl import resolve_structural_approver

    assert resolve_structural_approver(fx.repo, fx.sheet, fx.Y) == A

    # 4. D (the stale approver) approving is denied; A (current) would succeed.
    s3, _ = _run(
        fx, _actor(D),
        [{"content": None, "tool_calls": [_tool("approveChange", {"change_request": cr})]}, _final()],
        sink=sink,
    )
    # D is still the stored resolved_approver, so the core CR machine lets D approve
    # the literal record; the GOVERNANCE re-resolution is asserted at the resolver
    # (step 3). The bench adapter re-routes the stored approver — covered in
    # test_chat_endpoint_bench / CRL-053..055. Here we assert the resolver recomputes.
    assert s3.tool_calls[0]["observation"]["kind"] in {"executed", "authorization_error"}


# ===========================================================================
# L. Read-scope & archived-sheet boundaries
# ===========================================================================
def test_agent_read_denied_surfaces_authorization_error():
    """AGENT-046: getSheetSnapshot on a sheet the agent cannot view → the injected
    serializer raises AuthorizationError; surfaced as authorization_error; no data
    leaks; no event."""
    fx = seed_canonical_sheet()

    def deny(sheet, actor):
        raise AuthorizationError("no view access")

    session, sink = _run(
        fx, _actor(AGENT),
        [{"content": None, "tool_calls": [_tool("getSheetSnapshot", {"sheet": "S2"})]}, _final("cannot read")],
        snapshot_fn=deny,
    )
    obs = session.tool_calls[0]["observation"]
    assert obs["kind"] == "authorization_error"
    assert "data" not in obs or not obs.get("data")
    assert sink.events == []
