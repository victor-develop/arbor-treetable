"""Per-cell comment CAPABILITIES — the promoted, executor-routed contract
(Area 2 / WS-CMT-CAP). Bench-free (plain pytest, no Frappe bench).

The comment MUTATIONS (addComment / resolveComment / deleteComment) are now
registry capabilities routed through the ONE ``execute_action`` — mirroring the
impersonation / subscribe control caps: ``Axis.NONE``, ``emits=()`` (NO Tree
Event), authorized UNIFORMLY via the acl comment authority resolver, and they
ALWAYS execute or DENY (403 / ``AuthorizationError``) — a comment cap NEVER
becomes a Change Request.

This module proves, over the canonical in-memory fixture + ``InMemoryRepository``
comment doubles:

* authorize from the DENIED side (non-reader add -> 403; non-approver resolve ->
  403; non-author non-approver delete -> 403);
* cross-surface parity (in-process vs agent) for addComment;
* impersonation trace (an impersonated addComment stamps real_user != author);
* soft-delete leaves a tombstone + a read peer hides it + the delete is auditable;
* the 3 caps appear in ``get_llm_tools`` (agent-operable);
* no Tree Event is ever emitted by a comment cap.
"""

from __future__ import annotations

import pytest

from arbor.arbor.agent.react import run_agent_session
from arbor.core.executor import execute_action
from arbor.core.registry import get_capability, get_llm_tools
from arbor.core.testing import MockLLMProvider, RecordingEventSink
from arbor.core.types import Actor, ActorType, AuthorizationError, UnknownCapabilityError
from tests.fixtures.canonical import A, B, C, E, F, seed_canonical_sheet


def _actor(user: str, is_admin: bool = False) -> Actor:
    return Actor(user=user, actor_type=ActorType.HUMAN, is_admin=is_admin)


def _fx_owner_only():
    """Canonical fixture + an owner-only ``secret`` column (owner C, editors=[B]).

    Read matrix on ``secret``: C (owner) / B (editor) / admin -> yes; E / F -> no.
    This is the DENY-side cell the non-reader-add test keys on."""
    fx = seed_canonical_sheet()
    fx.repo.add_column(
        "col:secret", fx.sheet, "secret", column_owner=C, editors=[B],
        type="text", read_level="owner-only",
    )
    return fx


# ---------------------------------------------------------------------------
# addComment — read-gated (can_read_column). Denied side -> 403, never a CR.
# ---------------------------------------------------------------------------
def test_add_comment_by_reader_executes_no_event():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    out = execute_action(
        "addComment",
        {"sheet": fx.sheet, "node": fx.X, "column": fx.col_budget, "body": "hi"},
        _actor(E),  # E can read the public budget column
        fx.repo,
        sink,
    )
    assert out.kind == "executed"
    assert sink.events == []  # comment caps emit NO Tree Event
    name = out.data["comment"]
    c = fx.repo.get_comment(name)
    assert c.author == E and c.body == "hi" and c.deleted is False


def test_add_comment_by_non_reader_is_denied_403_not_a_cr():
    """A non-reader (E) cannot even discuss an owner-only cell — hard 403, and NO
    Change Request is created (comment caps always execute or deny)."""
    fx = _fx_owner_only()
    sink = RecordingEventSink()
    with pytest.raises(AuthorizationError):
        execute_action(
            "addComment",
            {"sheet": fx.sheet, "node": fx.X, "column": "col:secret", "body": "peek"},
            _actor(E),
            fx.repo,
            sink,
        )
    assert sink.events == []
    assert fx.repo.change_requests == {}  # never routed to a CR
    assert fx.repo.comments == {}  # nothing written


# ---------------------------------------------------------------------------
# resolveComment — column-approver gated. Non-approver -> 403.
# ---------------------------------------------------------------------------
def test_resolve_comment_by_approver_and_denied_for_non_approver():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    # E posts on col:budget (owner C). E is a reader, not an approver.
    add = execute_action(
        "addComment",
        {"sheet": fx.sheet, "node": fx.X, "column": fx.col_budget, "body": "q"},
        _actor(E), fx.repo, sink,
    )
    comment = add.data["comment"]

    # Non-approver E cannot resolve -> 403.
    with pytest.raises(AuthorizationError):
        execute_action("resolveComment", {"comment": comment, "resolved": True}, _actor(E), fx.repo, sink)
    assert fx.repo.get_comment(comment).resolved is False

    # Owner C resolves; then reopens (resolved=false).
    out = execute_action("resolveComment", {"comment": comment, "resolved": True}, _actor(C), fx.repo, sink)
    assert out.kind == "executed"
    assert fx.repo.get_comment(comment).resolved is True
    execute_action("resolveComment", {"comment": comment, "resolved": False}, _actor(C), fx.repo, sink)
    assert fx.repo.get_comment(comment).resolved is False
    assert sink.events == []  # still no Tree Event


def test_resolve_reply_resolves_thread_root():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    root = execute_action(
        "addComment", {"sheet": fx.sheet, "node": fx.X, "column": fx.col_budget, "body": "root"},
        _actor(C), fx.repo, sink,
    ).data["comment"]
    reply = execute_action(
        "addComment",
        {"sheet": fx.sheet, "node": fx.X, "column": fx.col_budget, "body": "reply", "parent_comment": root},
        _actor(C), fx.repo, sink,
    ).data["comment"]
    # Resolving the REPLY resolves the whole thread (the root carries the flag).
    out = execute_action("resolveComment", {"comment": reply, "resolved": True}, _actor(C), fx.repo, sink)
    assert out.data["comment"] == root
    assert fx.repo.get_comment(root).resolved is True


# ---------------------------------------------------------------------------
# deleteComment — author OR column approver. Neither -> 403. SOFT delete.
# ---------------------------------------------------------------------------
def test_delete_comment_authority_matrix():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    # E (reader, non-approver) posts on col:budget (owner C).
    comment = execute_action(
        "addComment", {"sheet": fx.sheet, "node": fx.X, "column": fx.col_budget, "body": "x"},
        _actor(E), fx.repo, sink,
    ).data["comment"]

    # F is neither author nor approver -> 403.
    with pytest.raises(AuthorizationError):
        execute_action("deleteComment", {"comment": comment}, _actor(F), fx.repo, sink)
    assert fx.repo.get_comment(comment).deleted is False

    # Author E may delete their own (soft).
    out = execute_action("deleteComment", {"comment": comment}, _actor(E), fx.repo, sink)
    assert out.kind == "executed"
    assert fx.repo.get_comment(comment).deleted is True


def test_delete_comment_by_approver_moderation():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    comment = execute_action(
        "addComment", {"sheet": fx.sheet, "node": fx.X, "column": fx.col_budget, "body": "y"},
        _actor(E), fx.repo, sink,
    ).data["comment"]
    # Approver C (owner) may delete anyone's comment.
    execute_action("deleteComment", {"comment": comment}, _actor(C), fx.repo, sink)
    assert fx.repo.get_comment(comment).deleted is True


def test_soft_delete_leaves_auditable_tombstone_and_list_hides_it():
    """Delete is a SOFT delete: the row is preserved (deleted_by/deleted_at set) so
    it stays auditable, but a read peer (list_comments) filters it out."""
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    keep = execute_action(
        "addComment", {"sheet": fx.sheet, "node": fx.X, "column": fx.col_budget, "body": "keep"},
        _actor(C), fx.repo, sink,
    ).data["comment"]
    drop = execute_action(
        "addComment", {"sheet": fx.sheet, "node": fx.X, "column": fx.col_budget, "body": "drop"},
        _actor(C), fx.repo, sink,
    ).data["comment"]
    execute_action("deleteComment", {"comment": drop}, _actor(C), fx.repo, sink)

    # Tombstone: row preserved + auditable.
    tomb = fx.repo.get_comment(drop)
    assert tomb is not None and tomb.deleted is True
    assert tomb.deleted_by == C and tomb.deleted_at is not None
    assert tomb.body == "drop"  # content preserved for audit

    # A read peer hides the tombstone but keeps the live comment.
    listed = [c.name for c in fx.repo.list_comments(fx.sheet, fx.X, fx.col_budget)]
    assert keep in listed and drop not in listed


def test_delete_unknown_comment_raises_not_found():
    fx = seed_canonical_sheet()
    with pytest.raises(UnknownCapabilityError):
        execute_action("deleteComment", {"comment": "nope"}, _actor(C), fx.repo, RecordingEventSink())


# ---------------------------------------------------------------------------
# Cross-surface parity (in-process vs agent) for addComment.
# ---------------------------------------------------------------------------
def _event_summary(ev):
    return None if ev is None else {"type": ev.type, "sheet": ev.sheet, "payload": ev.payload}


def test_add_comment_parity_inprocess_vs_agent():
    """The comment cap funnels through the SAME executor on both surfaces: an
    in-process addComment and the agent tool-call produce the identical Outcome
    shape + the identical (empty) event stream; only actor_type differs."""
    params = {"sheet": "S", "node": "X", "column": "col:budget", "body": "parity"}

    ip_fx = seed_canonical_sheet()
    ip_sink = RecordingEventSink()
    ip_out = execute_action("addComment", params, Actor(C, ActorType.HUMAN), ip_fx.repo, ip_sink)

    ag_fx = seed_canonical_sheet()
    ag_sink = RecordingEventSink()
    provider = MockLLMProvider(
        [
            {"content": None, "tool_calls": [{"id": "t1", "name": "addComment", "arguments": params}]},
            {"content": "done", "tool_calls": []},
        ]
    )
    session = run_agent_session("comment it", Actor(C, ActorType.AGENT), ag_fx.repo, ag_sink, provider, max_steps=4)
    ag_obs = session.tool_calls[0]["observation"]

    assert ip_out.kind == "executed" == ag_obs["kind"]
    # No Tree Event on either surface.
    assert ip_sink.events == [] == ag_sink.events
    # Identical comment written (author + body) on both.
    ip_c = ip_fx.repo.get_comment(ip_out.data["comment"])
    ag_c = ag_fx.repo.get_comment(ag_obs["data"]["comment"])
    assert ip_c.author == ag_c.author == C
    assert ip_c.body == ag_c.body == "parity"


# ---------------------------------------------------------------------------
# Impersonation trace: an impersonated addComment stamps real_user != author.
# ---------------------------------------------------------------------------
def test_impersonated_add_comment_stamps_real_user_trace():
    """When an admin acts as another user, the comment records the effective author
    AND the real principal (audit trace), with real_user != author."""
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    # Admin 'root' acting as C: effective user=C, real_user=root.
    impersonated = Actor(
        user=C, actor_type=ActorType.HUMAN, is_admin=True, real_user="root", impersonated_as=C
    )
    assert impersonated.is_impersonated is True
    out = execute_action(
        "addComment",
        {"sheet": fx.sheet, "node": fx.X, "column": fx.col_budget, "body": "as C"},
        impersonated, fx.repo, sink,
    )
    c = fx.repo.get_comment(out.data["comment"])
    assert c.author == C  # effective identity is the author
    assert c.real_user == "root"  # the truly-authenticated principal
    assert c.impersonated_as == C
    assert c.real_user != c.author  # trace present


def test_non_impersonated_add_comment_has_no_trace():
    fx = seed_canonical_sheet()
    sink = RecordingEventSink()
    out = execute_action(
        "addComment",
        {"sheet": fx.sheet, "node": fx.X, "column": fx.col_budget, "body": "plain"},
        _actor(C), fx.repo, sink,
    )
    c = fx.repo.get_comment(out.data["comment"])
    assert c.real_user is None and c.impersonated_as is None


# ---------------------------------------------------------------------------
# Registry surface: the 3 caps are agent-operable + emit no event.
# ---------------------------------------------------------------------------
def test_comment_caps_are_llm_operable_and_emit_no_event():
    tool_names = {t["function"]["name"] for t in get_llm_tools()}
    for cid in ("addComment", "resolveComment", "deleteComment"):
        assert cid in tool_names  # FULL agent access
        cap = get_capability(cid)
        assert cap.emits == ()  # the doctype row is the audit record
        assert cap.is_exposed_to_llm is True
