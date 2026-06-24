"""Role management lifecycle — a CLEAN PARALLEL state machine to Change Request
(Feature: role management).

An Arbor Role is a site-wide persona (PM/Developer/Marketing...). Two flows:

* ADMIN DIRECT grant/revoke (``assign`` / ``revoke``) — admin-gated in the
  executor, immediate, no approval.
* USER SELF-APPLICATION (``create_application`` -> ``approve`` | ``reject`` |
  ``withdraw``) — mirrors the CR ``proposed -> approved|rejected|withdrawn``
  shape, with decision-time admin re-resolution.

Two deliberate reuse decisions keep the most load-bearing invariants untouched:

1. NO new EventType — every role emit is ``DELEGATION_CHANGED`` discriminated by
   ``payload.op`` (the closed 11-event set stays byte-identical).
2. Roles have NO sheet, so notifications are resolved to recipients DIRECTLY
   (admins on apply; requester on decide) and written via
   ``repo.create_notification`` — NOT through the sheet-scoped subscription
   matcher.

Pure: operates on the Repository + EventSink protocols. ZERO frappe.
"""

from __future__ import annotations

from typing import Any

from .ports import EventSink, Repository
from .types import (
    Actor,
    AuthorizationError,
    CRStateError,
    CRStatus,
    EventType,
    Outcome,
    TreeEvent,
)

# Role lifecycle ops carried on the reused DELEGATION_CHANGED event's payload.
OP_APPLIED = "role-applied"
OP_APPROVED = "role-approved"
OP_REJECTED = "role-rejected"
OP_WITHDRAWN = "role-withdrawn"
OP_GRANTED = "role-granted"
OP_REVOKED = "role-revoked"


def _emit(sink: EventSink, actor: Actor, op: str, **payload: Any) -> TreeEvent:
    """Emit a sheet-less DELEGATION_CHANGED event discriminated by ``op``."""
    return sink.emit(
        TreeEvent(
            sheet=None,
            type=EventType.DELEGATION_CHANGED.value,
            payload={"op": op, **payload},
            actor=actor.user,
            actor_type=actor.actor_type,
        )
    )


def _notify(repo: Repository, recipients, *, tree_event, op: str, role: str, application=None) -> None:
    """One in-app Notification per recipient, idempotent per (tree_event, recipient)."""
    for recipient in recipients:
        repo.create_notification(
            {
                "recipient": recipient,
                "channel": "in-app",
                "tree_event": getattr(tree_event, "event_id", None),
                "op": op,
                "role": role,
                "role_application": application,
            }
        )


# ---------------------------------------------------------------------------
# Admin direct grant / revoke (admin gate enforced by the executor)
# ---------------------------------------------------------------------------
def assign(repo: Repository, sink: EventSink, role: str, grantee: str, actor: Actor) -> Outcome:
    """Admin DIRECT grant. Idempotent on an already-active (role, grantee)."""
    existing = repo.find_active_role_grant(role, grantee)
    if existing is not None:
        name = existing.name
    else:
        name = repo.create_role_grant(
            role, grantee, granted_by=actor.user, source="admin-grant"
        )
    event = _emit(sink, actor, OP_GRANTED, role=role, grantee=grantee, role_grant=name)
    return Outcome(kind="executed", event=event, data={"role_grant": name})


def revoke(repo: Repository, sink: EventSink, role: str, grantee: str, actor: Actor) -> Outcome:
    """Admin revoke. Idempotent on a missing/already-inactive grant."""
    existing = repo.find_active_role_grant(role, grantee)
    if existing is not None:
        repo.deactivate_role_grant(existing.name)
    event = _emit(sink, actor, OP_REVOKED, role=role, grantee=grantee)
    return Outcome(kind="executed", event=event, data={"role": role, "grantee": grantee})


# ---------------------------------------------------------------------------
# User self-application lifecycle
# ---------------------------------------------------------------------------
def create_application(
    repo: Repository, sink: EventSink, role: str, actor: Actor, justification: str | None = None
) -> Outcome:
    """User self-apply. HARD-gates ``applicable`` & ``active`` (defense-in-depth
    for requirement #3, not just a hidden UI affordance); de-dupes open
    applications; notifies admins."""
    role_view = repo.get_role(role)
    if role_view is None or not role_view.active or not role_view.applicable:
        raise AuthorizationError(f"role {role!r} is not open for application")

    if repo.find_open_role_application(role, actor.user) is not None:
        raise CRStateError(f"{actor.user} already has an open application for {role!r}")

    name = repo.create_role_application(
        {
            "role": role,
            "requester": actor.user,
            "status": CRStatus.PROPOSED.value,
            "justification": justification,
        }
    )
    event = _emit(sink, actor, OP_APPLIED, role=role, role_application=name, requester=actor.user)
    _notify(repo, repo.list_admins(), tree_event=event, op=OP_APPLIED, role=role, application=name)
    return Outcome(kind="executed", event=event, data={"role_application": name})


def _require_proposed(app: dict[str, Any]) -> None:
    if app["status"] != CRStatus.PROPOSED.value:
        raise CRStateError(
            f"Role Application is {app['status']!r}; only 'proposed' is mutable"
        )


def approve_application(
    repo: Repository, sink: EventSink, role_application: str, actor: Actor, comment: str | None = None
) -> Outcome:
    """Admin approve (admin gate enforced by the executor at decision time). The
    ONE privileged effect: materialize an Arbor Role Grant {source:application}."""
    app = repo.get_role_application(role_application)
    _require_proposed(app)
    role, requester = app["role"], app["requester"]

    existing = repo.find_active_role_grant(role, requester)
    grant = existing.name if existing is not None else repo.create_role_grant(
        role, requester, granted_by=actor.user, source="application", granted_via=role_application
    )
    event = _emit(
        sink, actor, OP_APPROVED, role=role, role_application=role_application, requester=requester
    )
    repo.update_role_application(
        role_application,
        {
            "status": CRStatus.APPROVED.value,
            "decided_by": actor.user,
            "resulting_grant": grant,
            "decided_event": event.event_id,
        },
    )
    _notify(repo, [requester], tree_event=event, op=OP_APPROVED, role=role, application=role_application)
    return Outcome(kind="executed", event=event, data={"role_grant": grant})


def reject_application(
    repo: Repository, sink: EventSink, role_application: str, actor: Actor, comment: str | None = None
) -> Outcome:
    """Admin reject (admin gate enforced by the executor). No grant created."""
    app = repo.get_role_application(role_application)
    _require_proposed(app)
    role, requester = app["role"], app["requester"]
    event = _emit(
        sink, actor, OP_REJECTED, role=role, role_application=role_application, requester=requester
    )
    repo.update_role_application(
        role_application,
        {"status": CRStatus.REJECTED.value, "decided_by": actor.user, "decided_event": event.event_id},
    )
    _notify(repo, [requester], tree_event=event, op=OP_REJECTED, role=role, application=role_application)
    return Outcome(kind="executed", event=event, data={"role_application": role_application})


def withdraw_application(
    repo: Repository, sink: EventSink, role_application: str, actor: Actor, comment: str | None = None
) -> Outcome:
    """Withdraw (requester-only)."""
    app = repo.get_role_application(role_application)
    _require_proposed(app)
    if actor.user != app["requester"]:
        raise AuthorizationError(
            f"only the requester {app['requester']} may withdraw {role_application}"
        )
    event = _emit(
        sink, actor, OP_WITHDRAWN, role=app["role"], role_application=role_application
    )
    repo.update_role_application(
        role_application,
        {"status": CRStatus.WITHDRAWN.value, "decided_by": actor.user, "decided_event": event.event_id},
    )
    return Outcome(kind="executed", event=event, data={"role_application": role_application})
