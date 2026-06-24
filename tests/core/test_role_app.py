"""Role management — pure-core lifecycle contract (Feature: roles).

Exercises the WHOLE feature through ``execute_action`` (surface parity + the
executor's admin gating) plus the ACL role->user expansion seam. Bench-free:
InMemoryRepository + RecordingEventSink only.

Two invariants this file pins down:
  * The closed 11-event set is UNCHANGED — every role emit is DELEGATION_CHANGED
    discriminated by ``payload.op`` (no new EventType).
  * ``applicable``/``active`` are enforced SERVER-SIDE in applyForRole (a hard
    AuthorizationError), not merely hidden in the UI.
"""

from __future__ import annotations

import pytest

from arbor.core import role_app
from arbor.core.acl import resolve_column_approvers
from arbor.core.executor import execute_action
from arbor.core.testing import InMemoryRepository, RecordingEventSink
from arbor.core.types import (
    Actor,
    AuthorizationError,
    CRStateError,
    EVENT_TYPES,
    EventType,
)

ADMIN = Actor(user="admin@x.com", is_admin=True)
ALICE = Actor(user="alice@x.com")
BOB = Actor(user="bob@x.com")


def _repo() -> InMemoryRepository:
    repo = InMemoryRepository()
    repo.add_admin(ADMIN.user)
    repo.add_role("pm", label="PM", applicable=True)
    repo.add_role("developer", label="Developer", applicable=True)
    repo.add_role("marketing", label="Marketing", applicable=False)  # admin-grant-only
    repo.add_role("retired", label="Retired", applicable=True, active=False)
    return repo


def _run(action, params, actor, repo, sink):
    return execute_action(action, params, actor, repo, sink)


# ---------------------------------------------------------------------------
# Admin direct grant / revoke
# ---------------------------------------------------------------------------
def test_assign_role_admin_creates_active_grant_and_emits_op_granted():
    repo, sink = _repo(), RecordingEventSink()
    out = _run("assignRole", {"role": "pm", "grantee": ALICE.user}, ADMIN, repo, sink)
    assert out.kind == "executed"
    assert repo.list_active_role_grantees("pm") == [ALICE.user]
    grant = repo.find_active_role_grant("pm", ALICE.user)
    assert grant.source == "admin-grant" and grant.granted_by == ADMIN.user
    assert sink.last().type == EventType.DELEGATION_CHANGED.value
    assert sink.last().payload["op"] == role_app.OP_GRANTED


def test_assign_role_is_idempotent():
    repo, sink = _repo(), RecordingEventSink()
    _run("assignRole", {"role": "pm", "grantee": ALICE.user}, ADMIN, repo, sink)
    _run("assignRole", {"role": "pm", "grantee": ALICE.user}, ADMIN, repo, sink)
    assert repo.list_active_role_grantees("pm") == [ALICE.user]  # still one


def test_assign_role_non_admin_is_rejected():
    repo, sink = _repo(), RecordingEventSink()
    with pytest.raises(AuthorizationError):
        _run("assignRole", {"role": "pm", "grantee": BOB.user}, ALICE, repo, sink)


def test_revoke_role_admin_deactivates_and_emits_op_revoked():
    repo, sink = _repo(), RecordingEventSink()
    _run("assignRole", {"role": "pm", "grantee": ALICE.user}, ADMIN, repo, sink)
    out = _run("revokeRole", {"role": "pm", "grantee": ALICE.user}, ADMIN, repo, sink)
    assert out.kind == "executed"
    assert repo.list_active_role_grantees("pm") == []
    assert sink.last().payload["op"] == role_app.OP_REVOKED


def test_revoke_role_is_idempotent_on_missing_grant():
    repo, sink = _repo(), RecordingEventSink()
    out = _run("revokeRole", {"role": "pm", "grantee": ALICE.user}, ADMIN, repo, sink)
    assert out.kind == "executed"  # no error, no grant


def test_revoke_role_non_admin_is_rejected():
    repo, sink = _repo(), RecordingEventSink()
    _run("assignRole", {"role": "pm", "grantee": ALICE.user}, ADMIN, repo, sink)
    with pytest.raises(AuthorizationError):
        _run("revokeRole", {"role": "pm", "grantee": ALICE.user}, BOB, repo, sink)


# ---------------------------------------------------------------------------
# User self-application
# ---------------------------------------------------------------------------
def test_apply_for_applicable_role_creates_proposed_and_notifies_admins():
    repo, sink = _repo(), RecordingEventSink()
    out = _run("applyForRole", {"role": "pm", "justification": "I lead the roadmap"}, ALICE, repo, sink)
    assert out.kind == "executed"
    app_name = out.data["role_application"]
    app = repo.get_role_application(app_name)
    assert app["status"] == "proposed" and app["requester"] == ALICE.user
    assert sink.last().type == EventType.DELEGATION_CHANGED.value
    assert sink.last().payload["op"] == role_app.OP_APPLIED
    # the admin got an in-app notification (direct recipient resolution, no sheet)
    notes = [n for n in repo.notifications.values() if n.get("recipient") == ADMIN.user]
    assert len(notes) == 1 and notes[0]["role"] == "pm" and notes[0]["channel"] == "in-app"


def test_apply_for_non_applicable_role_is_rejected_server_side():
    repo, sink = _repo(), RecordingEventSink()
    with pytest.raises(AuthorizationError):
        _run("applyForRole", {"role": "marketing"}, ALICE, repo, sink)


def test_apply_for_inactive_role_is_rejected():
    repo, sink = _repo(), RecordingEventSink()
    with pytest.raises(AuthorizationError):
        _run("applyForRole", {"role": "retired"}, ALICE, repo, sink)


def test_apply_for_unknown_role_is_rejected():
    repo, sink = _repo(), RecordingEventSink()
    with pytest.raises(AuthorizationError):
        _run("applyForRole", {"role": "nope"}, ALICE, repo, sink)


def test_duplicate_open_application_is_rejected():
    repo, sink = _repo(), RecordingEventSink()
    _run("applyForRole", {"role": "pm"}, ALICE, repo, sink)
    with pytest.raises(CRStateError):
        _run("applyForRole", {"role": "pm"}, ALICE, repo, sink)


def test_approve_application_grants_role_and_notifies_requester():
    repo, sink = _repo(), RecordingEventSink()
    out = _run("applyForRole", {"role": "pm"}, ALICE, repo, sink)
    app_name = out.data["role_application"]
    dec = _run("approveRoleApplication", {"role_application": app_name}, ADMIN, repo, sink)
    assert dec.kind == "executed"
    app = repo.get_role_application(app_name)
    assert app["status"] == "approved" and app["decided_by"] == ADMIN.user
    grant = repo.find_active_role_grant("pm", ALICE.user)
    assert grant is not None and grant.source == "application"
    assert app["resulting_grant"] == grant.name
    assert sink.last().payload["op"] == role_app.OP_APPROVED
    notes = [n for n in repo.notifications.values() if n.get("recipient") == ALICE.user]
    assert any(n["op"] == role_app.OP_APPROVED for n in notes)


def test_approve_by_non_admin_is_rejected():
    repo, sink = _repo(), RecordingEventSink()
    out = _run("applyForRole", {"role": "pm"}, ALICE, repo, sink)
    with pytest.raises(AuthorizationError):
        _run("approveRoleApplication", {"role_application": out.data["role_application"]}, BOB, repo, sink)


def test_approve_terminal_application_is_rejected():
    repo, sink = _repo(), RecordingEventSink()
    out = _run("applyForRole", {"role": "pm"}, ALICE, repo, sink)
    app_name = out.data["role_application"]
    _run("approveRoleApplication", {"role_application": app_name}, ADMIN, repo, sink)
    with pytest.raises(CRStateError):
        _run("approveRoleApplication", {"role_application": app_name}, ADMIN, repo, sink)


def test_reject_application_sets_rejected_no_grant():
    repo, sink = _repo(), RecordingEventSink()
    out = _run("applyForRole", {"role": "pm"}, ALICE, repo, sink)
    app_name = out.data["role_application"]
    _run("rejectRoleApplication", {"role_application": app_name}, ADMIN, repo, sink)
    assert repo.get_role_application(app_name)["status"] == "rejected"
    assert repo.find_active_role_grant("pm", ALICE.user) is None
    assert sink.last().payload["op"] == role_app.OP_REJECTED


def test_withdraw_by_requester_sets_withdrawn():
    repo, sink = _repo(), RecordingEventSink()
    out = _run("applyForRole", {"role": "pm"}, ALICE, repo, sink)
    app_name = out.data["role_application"]
    _run("withdrawRoleApplication", {"role_application": app_name}, ALICE, repo, sink)
    assert repo.get_role_application(app_name)["status"] == "withdrawn"
    assert sink.last().payload["op"] == role_app.OP_WITHDRAWN


def test_withdraw_by_non_requester_is_rejected():
    repo, sink = _repo(), RecordingEventSink()
    out = _run("applyForRole", {"role": "pm"}, ALICE, repo, sink)
    with pytest.raises(AuthorizationError):
        _run("withdrawRoleApplication", {"role_application": out.data["role_application"]}, BOB, repo, sink)


# ---------------------------------------------------------------------------
# ACL addressing — a column editor of the form ``role:<key>`` expands to holders
# ---------------------------------------------------------------------------
def test_column_editor_role_expands_to_active_grantees():
    repo, sink = _repo(), RecordingEventSink()
    repo.add_sheet("S", structural_owner="owner@x.com")
    repo.add_column("c1", "S", "col1", column_owner="owner@x.com", editors=["role:pm"])
    # no PM holders yet -> only the owner can approve
    assert resolve_column_approvers(repo, "S", "c1") == {"owner@x.com"}
    # grant PM to alice and bob -> both become column approvers via expansion
    _run("assignRole", {"role": "pm", "grantee": ALICE.user}, ADMIN, repo, sink)
    _run("assignRole", {"role": "pm", "grantee": BOB.user}, ADMIN, repo, sink)
    assert resolve_column_approvers(repo, "S", "c1") == {"owner@x.com", ALICE.user, BOB.user}
    # revoking PM from bob drops him from the approver set
    _run("revokeRole", {"role": "pm", "grantee": BOB.user}, ADMIN, repo, sink)
    assert resolve_column_approvers(repo, "S", "c1") == {"owner@x.com", ALICE.user}


def test_role_holder_can_update_cell_directly_via_role_editor():
    """A PM (via ``role:pm`` editor) is authorized to update the cell directly —
    no Change Request — proving the ACL expansion feeds the authority path."""
    repo, sink = _repo(), RecordingEventSink()
    repo.add_sheet("S", structural_owner="owner@x.com")
    repo.add_column("c1", "S", "col1", column_owner="owner@x.com", editors=["role:pm"])
    root = repo.add_node("n1", "S", None)
    _run("assignRole", {"role": "pm", "grantee": ALICE.user}, ADMIN, repo, sink)
    out = _run(
        "updateCell",
        {"sheet": "S", "node": root, "column": "c1", "value": "v"},
        ALICE,
        repo,
        sink,
    )
    assert out.kind == "executed"  # authorized directly, not suggested


# ---------------------------------------------------------------------------
# Closed-event-set guard — the headline regression to PREVENT
# ---------------------------------------------------------------------------
def test_role_flow_adds_no_event_type():
    assert len(EVENT_TYPES) == 11


def test_every_role_emit_is_delegation_changed():
    repo, sink = _repo(), RecordingEventSink()
    _run("assignRole", {"role": "pm", "grantee": ALICE.user}, ADMIN, repo, sink)
    _run("revokeRole", {"role": "pm", "grantee": ALICE.user}, ADMIN, repo, sink)
    out = _run("applyForRole", {"role": "pm"}, BOB, repo, sink)
    _run("approveRoleApplication", {"role_application": out.data["role_application"]}, ADMIN, repo, sink)
    out2 = _run("applyForRole", {"role": "developer"}, ALICE, repo, sink)
    _run("rejectRoleApplication", {"role_application": out2.data["role_application"]}, ADMIN, repo, sink)
    assert set(sink.types()) == {EventType.DELEGATION_CHANGED.value}
