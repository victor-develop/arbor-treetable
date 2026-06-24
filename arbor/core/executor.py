"""The centralized executor — the ONE path every surface funnels through
(ARCHITECTURE §4.2). Humans, the agent, and API/external callers are
indistinguishable here except by ``actor`` / ``actor_type``.

    validate schema
      -> resolve authority
          -> authorized:     run handler + sink.emit(event)
          -> not authorized: create Change Request + sink.emit(CHANGE_PROPOSED)

Control capabilities (snapshot read, CR lifecycle, subscribe/unsubscribe/ack,
suggestChange) are routed to their dedicated logic but STILL flow through this
single entrypoint, so surface parity holds for all 26 capabilities.

Pure: parameterized by the Repository + EventSink protocols. ZERO frappe.
"""

from __future__ import annotations

from typing import Any

from . import change_request as cr_module
from . import explore
from . import registry
from . import role_app as role_module
from .acl import resolve_authority
from .ports import EventSink, Repository
from .schema import validate_schema
from .types import (
    Actor,
    ActorType,
    AuthorizationError,
    Capability,
    EventType,
    Outcome,
    TreeEvent,
)

# Capabilities the executor dispatches to dedicated control logic rather than to
# the axis-gated mutate-or-suggest branch.
_CONTROL = {
    "getSheetSnapshot",
    "getSheetOverview",
    "listChildren",
    "getSubtree",
    "getNode",
    "searchNodes",
    "getCells",
    "suggestChange",
    "suggestChanges",
    "approveChange",
    "rejectChange",
    "withdrawChange",
    "subscribe",
    "unsubscribe",
    "acknowledge",
    # role management (Feature: roles)
    "assignRole",
    "revokeRole",
    "applyForRole",
    "approveRoleApplication",
    "rejectRoleApplication",
    "withdrawRoleApplication",
}

# Role capabilities gated on platform admin (System Manager) at dispatch time —
# the framework-free axis resolver has no notion of platform roles, so (like
# internalReset) the gate is explicit here. ``actor.is_admin`` is recomputed by
# the surface per request, so this IS decision-time re-resolution.
_ADMIN_ROLE_CAPS = {"assignRole", "revokeRole", "approveRoleApplication", "rejectRoleApplication"}

# Bounded read capabilities (the *explore* surface). All route to pure
# functions in ``explore`` and return ``Outcome(kind="read", data=...)``; none
# mutate or emit a Tree Event. ``getSheetSnapshot`` is handled separately (it
# carries the >500 guard and lets the adapter serialize the rows).
_READS = {
    "getSheetOverview",
    "listChildren",
    "getSubtree",
    "getNode",
    "searchNodes",
    "getCells",
}


def execute_action(
    action_id: str,
    params: dict[str, Any],
    actor: Actor,
    repo: Repository,
    sink: EventSink,
    validate: bool = True,
) -> Outcome:
    """The single mutation/control entrypoint (ARCHITECTURE §4.2)."""
    cap = registry.get_capability(action_id)  # 1. validate exists
    if validate:
        validate_schema(params, cap.params_schema)  # 2. validate params

    if action_id in _CONTROL:
        return _dispatch_control(cap, params, actor, repo, sink)

    # 3. resolve ACL on the relevant axis/axes.
    authority = resolve_authority(cap, params, actor, repo)

    # internalReset: administrative, never on the Tree Event stream. Allowed only
    # for an admin actor or the SYSTEM identity (the axis resolver has no notion of
    # platform roles, so this is gated explicitly here).
    if action_id == "internalReset":
        if not (getattr(actor, "is_admin", False) or actor.actor_type == ActorType.SYSTEM):
            raise AuthorizationError("internalReset is system/admin only")
        result = cap.handler(params, actor, repo)
        return Outcome(kind="executed", result=result, data=result.data)

    owner_self_cr = _forces_self_cr(repo, params, authority)

    if authority.is_authorized and not owner_self_cr:
        # 4a. AUTHORIZED → mutate + emit exactly one Tree Event.
        return _run_and_emit(cap, params, actor, repo, sink)

    # 4b. NOT AUTHORIZED (or owner-self policy) → create a Change Request.
    return _suggest(cap, params, actor, repo, sink, authority)


def _resolve_sheet(params: dict, repo: Repository) -> str | None:
    """Most capabilities carry ``sheet`` directly; the few sheet-less ones
    (e.g. ``revokeDelegation`` → ``branch_grant``) resolve it from their target so
    every emitted Tree Event and Change Request stays sheet-scoped."""
    sheet = params.get("sheet")
    if sheet:
        return sheet
    if params.get("branch_grant"):
        grant = repo.get_branch_grant(params["branch_grant"])
        return grant.sheet if grant else None
    return None


def _run_and_emit(
    cap: Capability, params: dict, actor: Actor, repo: Repository, sink: EventSink
) -> Outcome:
    result = cap.handler(params, actor, repo)  # the only mutation site
    event = sink.emit(
        TreeEvent(
            sheet=_resolve_sheet(params, repo) or result.event_payload.get("sheet"),
            type=cap.emits_primary,
            payload=result.event_payload,
            actor=actor.user,
            actor_type=actor.actor_type,
            change_request=None,
        )
    )
    return Outcome(kind="executed", event=event, result=result, data=result.data)


def _suggest(
    cap: Capability,
    params: dict,
    actor: Actor,
    repo: Repository,
    sink: EventSink,
    authority,
) -> Outcome:
    sheet = _resolve_sheet(params, repo)
    cr_name = cr_module.create_change_request(
        repo,
        sheet=sheet,
        target_kind=cap.target_kind.value,
        operation=cap.operation.value,
        payload=dict(params, _action_id=cap.id),
        requester=actor,
        resolved_approver=authority.resolved_approver,
        co_approvers=authority.co_approvers,
    )
    event = sink.emit(
        TreeEvent(
            sheet=sheet,
            type=EventType.CHANGE_PROPOSED.value,
            # Carry the original params so branch/column-scoped subscriptions and
            # webhooks can match a CHANGE_PROPOSED event to its target node/column
            # (the matcher reads payload.params.{node,parent,column}).
            payload={"change_request": cr_name, "action": cap.id, "params": dict(params)},
            actor=actor.user,
            actor_type=actor.actor_type,
            change_request=cr_name,
        )
    )
    return Outcome(
        kind="suggested",
        change_request=cr_name,
        event=event,
        resolved_approver=authority.resolved_approver,
        co_approvers=tuple(authority.co_approvers or ()),
    )


def _forces_self_cr(repo: Repository, params: dict, authority) -> bool:
    """Owner-self policy (PERMISSIONS §1.2): with
    ``settings.owners_must_use_change_requests``, an authorized owner's action
    still becomes a CR (self-approver)."""
    if not authority.is_authorized:
        return False
    sheet = params.get("sheet")
    if not sheet:
        return False
    try:
        settings = repo.get_sheet(sheet).settings or {}
    except Exception:  # pragma: no cover - defensive
        return False
    return bool(settings.get("owners_must_use_change_requests"))


# ---------------------------------------------------------------------------
# Control dispatch
# ---------------------------------------------------------------------------
def _dispatch_control(
    cap: Capability, params: dict, actor: Actor, repo: Repository, sink: EventSink
) -> Outcome:
    if cap.id == "getSheetSnapshot":
        # Whole-sheet read: refuse above EXPLORE_THRESHOLD so the agent/UI
        # navigates with the explore tools instead of pulling everything. The
        # guard raises SheetTooLargeError, which adapters surface as a 4xx — it
        # is a typed domain error, never an unhandled 500.
        explore.assert_snapshot_size(repo, params["sheet"])
        # Read; serialization is the adapter's job (it has the rows + hints).
        return Outcome(kind="read", data={"sheet": params["sheet"]})

    if cap.id in _READS:
        return _dispatch_read(cap, params, actor, repo)

    if cap.id == "suggestChange":
        return _explicit_suggest(params, actor, repo, sink)

    if cap.id == "suggestChanges":
        return _suggest_batch(params, actor, repo, sink)

    if cap.id == "approveChange":
        return cr_module.approve_change(
            repo, sink, params["change_request"], actor, replay=_replay_handler,
            comment=params.get("comment"),
        )
    if cap.id == "rejectChange":
        return cr_module.reject_change(
            repo, sink, params["change_request"], actor, comment=params.get("comment")
        )
    if cap.id == "withdrawChange":
        return cr_module.withdraw_change(
            repo, sink, params["change_request"], actor, comment=params.get("comment")
        )

    if cap.id == "subscribe":
        return _subscribe(params, actor, repo, sink)
    if cap.id == "unsubscribe":
        return _unsubscribe(params, actor, repo, sink)
    if cap.id == "acknowledge":
        return _acknowledge(params, actor, repo)

    if cap.id in _ADMIN_ROLE_CAPS or cap.id in ("applyForRole", "withdrawRoleApplication"):
        return _dispatch_role(cap, params, actor, repo, sink)

    raise AuthorizationError(f"unhandled control capability {cap.id}")  # pragma: no cover


def _dispatch_role(
    cap: Capability, params: dict, actor: Actor, repo: Repository, sink: EventSink
) -> Outcome:
    """Role-management control dispatch (Feature: roles). Admin caps are gated on
    ``actor.is_admin`` HERE (explicit platform-role gate, like internalReset);
    applyForRole is open to any authenticated actor (with role_app enforcing the
    applicable/active gate); withdraw is requester-gated inside role_app."""
    if cap.id in _ADMIN_ROLE_CAPS and not getattr(actor, "is_admin", False):
        raise AuthorizationError(f"{cap.id} is admin only")

    if cap.id == "assignRole":
        return role_module.assign(repo, sink, params["role"], params["grantee"], actor)
    if cap.id == "revokeRole":
        return role_module.revoke(repo, sink, params["role"], params["grantee"], actor)
    if cap.id == "applyForRole":
        return role_module.create_application(
            repo, sink, params["role"], actor, justification=params.get("justification")
        )
    if cap.id == "approveRoleApplication":
        return role_module.approve_application(
            repo, sink, params["role_application"], actor, comment=params.get("comment")
        )
    if cap.id == "rejectRoleApplication":
        return role_module.reject_application(
            repo, sink, params["role_application"], actor, comment=params.get("comment")
        )
    if cap.id == "withdrawRoleApplication":
        return role_module.withdraw_application(
            repo, sink, params["role_application"], actor, comment=params.get("comment")
        )
    raise AuthorizationError(f"unhandled role capability {cap.id}")  # pragma: no cover


def _dispatch_read(cap: Capability, params: dict, actor: Actor, repo: Repository) -> Outcome:
    """Route a bounded explore read to its pure ``explore`` function.

    Every branch returns ``Outcome(kind="read", data=<result dict>)``. The
    functions are pure over the Repository PORT; size/budget violations raise
    typed errors (``SheetTooLargeError`` / ``CellBudgetExceededError``) that
    adapters surface as 4xx, and a bad cursor / unknown node raises ``ValueError``.

    ``actor`` is threaded into every explore call so reads filter columns through
    the SAME ``acl.visible_columns`` rule as the snapshot — agent reads inherit it
    for free (the agent funnels through this exact path with its own Actor).
    """
    sheet = params["sheet"]
    if cap.id == "getSheetOverview":
        return Outcome(kind="read", data=explore.sheet_overview(repo, sheet, actor))
    if cap.id == "listChildren":
        return Outcome(
            kind="read",
            data=explore.list_children(
                repo,
                sheet,
                parent=params.get("parent"),
                cursor=params.get("cursor"),
                limit=int(params.get("limit", 50)),
                actor=actor,
            ),
        )
    if cap.id == "getSubtree":
        return Outcome(
            kind="read",
            data=explore.get_subtree(
                repo,
                sheet,
                params["node"],
                depth=int(params.get("depth", 1)),
                cursor=params.get("cursor"),
                limit=int(params.get("limit", 50)),
                actor=actor,
            ),
        )
    if cap.id == "getNode":
        return Outcome(
            kind="read", data=explore.get_node(repo, sheet, params["node"], actor=actor)
        )
    if cap.id == "searchNodes":
        return Outcome(
            kind="read",
            data=explore.search_nodes(
                repo,
                sheet,
                params["query"],
                column=params.get("column"),
                cursor=params.get("cursor"),
                limit=int(params.get("limit", 50)),
                actor=actor,
            ),
        )
    if cap.id == "getCells":
        return Outcome(
            kind="read",
            data=explore.get_cells(
                repo, sheet, params["nodes"], params["columns"], actor=actor
            ),
        )
    raise AuthorizationError(f"unhandled read capability {cap.id}")  # pragma: no cover


def _explicit_suggest(params: dict, actor: Actor, repo: Repository, sink: EventSink) -> Outcome:
    payload = params["payload"]
    cr_name = cr_module.create_change_request(
        repo,
        sheet=params["sheet"],
        target_kind=params["target_kind"],
        operation=params["operation"],
        payload=payload,
        requester=actor,
        resolved_approver=payload.get("resolved_approver", actor.user),
    )
    event = sink.emit(
        TreeEvent(
            sheet=params["sheet"],
            type=EventType.CHANGE_PROPOSED.value,
            payload={"change_request": cr_name, "action": "suggestChange"},
            actor=actor.user,
            actor_type=actor.actor_type,
            change_request=cr_name,
        )
    )
    return Outcome(
        kind="suggested",
        change_request=cr_name,
        event=event,
        resolved_approver=payload.get("resolved_approver", actor.user),
    )


def _suggest_batch(params: dict, actor: Actor, repo: Repository, sink: EventSink) -> Outcome:
    """Bundle several changes into ONE Change Request, reviewed/applied atomically.
    Each change is resolved to its own approver via the SAME two-axis ACL, so a
    batch can span multiple owners; nothing applies until every item is approved."""
    sheet = params["sheet"]
    items = []
    for change in params["changes"]:
        cap = registry.get_capability(change["action"])
        if cap is None:
            raise UnknownCapabilityError(change["action"])
        p = dict(change["params"], sheet=sheet)
        authority = resolve_authority(cap, p, actor, repo)
        items.append(
            {
                "action": cap.id,
                "target_kind": cap.target_kind.value,
                "operation": cap.operation.value,
                "payload": dict(p, _action_id=cap.id),
                "resolved_approver": authority.resolved_approver,
                "co_approvers": tuple(authority.co_approvers or ()),
            }
        )
    cr_name = cr_module.create_batch_change_request(repo, sheet, actor, items)
    event = sink.emit(
        TreeEvent(
            sheet=sheet,
            type=EventType.CHANGE_PROPOSED.value,
            payload={"change_request": cr_name, "action": "suggestChanges", "changes": len(items)},
            actor=actor.user,
            actor_type=actor.actor_type,
            change_request=cr_name,
        )
    )
    return Outcome(kind="suggested", change_request=cr_name, event=event)


def _subscribe(params: dict, actor: Actor, repo: Repository, sink: EventSink) -> Outcome:
    sub = repo.create_subscription(
        {
            "subscriber": params.get("subscriber") or actor.user,
            "subscriber_kind": "external" if params.get("subscriber") and params["subscriber"] != actor.user else "user",
            "scope": params["scope"],
            "target": params["target"],
            "event_types": params["event_types"],
            "delivery": params["delivery"],
            "requires_ack": params.get("requires_ack", False),
        }
    )
    sheet = params["target"] if params["scope"] == "sheet" else _sheet_of_target(repo, params)
    event = sink.emit(
        TreeEvent(
            sheet=sheet,
            type=EventType.SUBSCRIPTION_CHANGED.value,
            payload={"op": "subscribe", "subscription": sub},
            actor=actor.user,
            actor_type=actor.actor_type,
        )
    )
    return Outcome(kind="executed", event=event, data={"subscription": sub})


def _unsubscribe(params: dict, actor: Actor, repo: Repository, sink: EventSink) -> Outcome:
    sub = repo.get_subscription(params["subscription"])
    if sub.get("subscriber") != actor.user:
        raise AuthorizationError("only the subscription owner may unsubscribe")
    repo.delete_subscription(params["subscription"])
    sheet = sub.get("sheet") or sub.get("target")
    event = sink.emit(
        TreeEvent(
            sheet=sheet,
            type=EventType.SUBSCRIPTION_CHANGED.value,
            payload={"op": "unsubscribe", "subscription": params["subscription"]},
            actor=actor.user,
            actor_type=actor.actor_type,
        )
    )
    return Outcome(kind="executed", event=event)


def _acknowledge(params: dict, actor: Actor, repo: Repository) -> Outcome:
    notif = repo.get_notification(params["notification"])
    if notif.get("recipient") != actor.user:
        raise AuthorizationError("only the notification recipient may acknowledge")
    ack = repo.create_acknowledgement(params["notification"], actor.user)
    # No Tree Event (CAPABILITIES.md): the Acknowledgement row is the record.
    return Outcome(kind="executed", data={"acknowledgement": ack})


def _sheet_of_target(repo: Repository, params: dict) -> str:
    scope = params["scope"]
    target = params["target"]
    if scope == "branch":
        return repo.get_node(target).sheet
    if scope == "column":
        return repo.get_column_by_name(target).sheet if hasattr(repo, "get_column_by_name") else target
    return target


# ---------------------------------------------------------------------------
# CR replay — runs the original capability handler AS the approver and emits the
# REAL mutation event (ARCHITECTURE §5). This is the single bridge the CR state
# machine calls; it reuses _run_and_emit so the approved mutation is identical to
# a directly-authorized one (surface parity for the replay path).
# ---------------------------------------------------------------------------
def _replay_handler(cr: dict, approver: Actor, repo: Repository, sink: EventSink) -> TreeEvent:
    payload = dict(cr["payload"])
    action_id = payload.pop("_action_id", None) or _action_for_cr(cr)
    payload.pop("co_approvers", None)
    cap = registry.get_capability(action_id)
    outcome = _run_and_emit(cap, payload, approver, repo, sink)
    return outcome.event


def _action_for_cr(cr: dict) -> str:
    """Fallback: map (target_kind, operation) back to a capability id for CRs
    created via explicit ``suggestChange`` (which omit ``_action_id``)."""
    tk, op = cr["target_kind"], cr["operation"]
    table = {
        ("node-structure", "add"): "addNode",
        ("node-structure", "move"): "moveNode",
        ("node-structure", "delete"): "deleteNode",
        ("cell-value", "update"): "updateCell",
        ("column-schema", "add"): "addColumn",
        ("column-schema", "update"): "updateColumn",
        ("column-schema", "delete"): "deleteColumn",
    }
    return table[(tk, op)]
