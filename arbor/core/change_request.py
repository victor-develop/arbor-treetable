"""Change Request lifecycle — the state machine (ARCHITECTURE §5).

States: proposed -> approved | rejected | withdrawn (all terminal). A CR is a
*deferred capability call*: it stores ``{operation, target_kind, payload,
requester, resolved_approver}`` and, on approval, REPLAYS the same capability
handler AS the approver — guaranteeing the identical mutation path and event.

moveNode dual-approval (DECISIONS ADR-001): a single CR carries
``payload.co_approvers`` and an ``approvals[]`` list; it transitions to approved
(and replays) only once the ``resolved_approver`` AND every co-approver has
approved.

Pure: operates on the Repository + EventSink protocols. The frappe adapter
persists CR rows; this module owns the transition logic.
"""

from __future__ import annotations

from typing import Any, Callable

from .ports import EventSink, Repository
from .types import (
    Actor,
    ActorType,
    AuthorizationError,
    CRStateError,
    CRStatus,
    EventType,
    Outcome,
    TreeEvent,
)

# Injected by executor to avoid an import cycle: replay must run the capability
# handler with full emit semantics. Signature: (cr, approver_actor, repo, sink).
HandlerReplay = Callable[[dict, Actor, Repository, EventSink], TreeEvent]


def create_change_request(
    repo: Repository,
    sheet: str,
    target_kind: str,
    operation: str,
    payload: dict[str, Any],
    requester: Actor,
    resolved_approver: str,
    co_approvers: tuple[str, ...] = (),
) -> str:
    """Persist a PROPOSED Change Request. ``co_approvers`` (moveNode) are stored
    on the payload so the single CR tracks all required approvals."""
    enriched = dict(payload)
    if co_approvers:
        enriched["co_approvers"] = list(co_approvers)
    return repo.create_change_request(
        {
            "sheet": sheet,
            "target_kind": target_kind,
            "operation": operation,
            "payload": enriched,
            "requester": requester.user,
            # Impersonation trace (Area 1): the truly-authenticated admin when the
            # CR was proposed under an "act as" overlay; None for a normal CR (so
            # the persisted row is byte-for-byte as today).
            "real_requester": requester.real_user if requester.is_impersonated else None,
            "resolved_approver": resolved_approver,
            "status": CRStatus.PROPOSED.value,
            "approvals": [],  # users who have approved so far (multi-approval)
        }
    )


def create_batch_change_request(
    repo: Repository,
    sheet: str,
    requester: Actor,
    items: list[dict[str, Any]],
) -> str:
    """Persist a PROPOSED multi-change Change Request: ``items`` is the ordered
    list of changes, each ``{action, target_kind, operation, payload,
    resolved_approver, co_approvers?}``. Reviewed/approved/applied atomically."""
    changes = []
    for it in items:
        changes.append(
            {
                "action": it["action"],
                "target_kind": it.get("target_kind"),
                "operation": it.get("operation"),
                "payload": dict(
                    it.get("payload") or {},
                    **({"co_approvers": list(it["co_approvers"])} if it.get("co_approvers") else {}),
                ),
                "resolved_approver": it.get("resolved_approver"),
                "item_approved": False,
                "approved_by": None,
            }
        )
    return repo.create_change_request(
        {
            "sheet": sheet,
            "target_kind": "batch",
            "operation": "multi",
            "payload": {},
            "requester": requester.user,
            "real_requester": requester.real_user if requester.is_impersonated else None,
            "resolved_approver": "",
            "status": CRStatus.PROPOSED.value,
            "approvals": [],
            "changes": changes,
        }
    )


def _synthetic_item_cr(cr: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """Wrap one batch item as a single-change-CR-shaped dict so the existing
    re-resolution / replay helpers apply unchanged."""
    payload = dict(item.get("payload") or {})
    payload["_action_id"] = item["action"]
    return {
        "sheet": cr["sheet"],
        "target_kind": item.get("target_kind"),
        "operation": item.get("operation"),
        "resolved_approver": item.get("resolved_approver") or cr.get("requester"),
        "payload": payload,
    }


def _require_proposed(cr: dict[str, Any]) -> None:
    if cr["status"] != CRStatus.PROPOSED.value:
        raise CRStateError(
            f"Change Request is {cr['status']!r}; only 'proposed' is mutable"
        )


def _column_editor_approvers(cr: dict[str, Any], repo: Repository) -> set[str]:
    """For a column-targeted CR, the column's owner+editors may also approve
    (CAPABILITIES: approveChange ACL). Empty for structural CRs."""
    if cr.get("target_kind") not in ("cell-value", "column-schema"):
        return set()
    column = (cr.get("payload") or {}).get("column")
    if not column:
        return set()
    try:
        col = repo.get_column(cr["sheet"], column)
    except Exception:
        return set()
    return {col.column_owner} | set(col.editors or [])


def _reresolve_approver(cr: dict[str, Any], repo: Repository) -> tuple[str, tuple[str, ...]]:
    """Recompute (approver, co_approvers) from the CURRENT grants/ownership at
    decision time (ARCHITECTURE §5). A CR filed while a branch grant / column
    owner was in place must route to whoever holds that authority NOW — a revoked
    grant falls back to the ancestor owner, a nearer grant wins, a removed editor
    drops out. Falls back to the stored approver when the originating capability
    isn't recorded (explicit suggestChange)."""
    # Local imports avoid an import cycle (acl/registry don't import this module).
    from .acl import resolve_authority
    from .registry import get_capability

    payload = cr.get("payload") or {}
    action_id = payload.get("_action_id")
    cap = get_capability(action_id) if action_id else None
    if cap is None:
        return cr["resolved_approver"], tuple(payload.get("co_approvers") or ())
    auth = resolve_authority(cap, payload, Actor(cr["resolved_approver"]), repo)
    return auth.resolved_approver, tuple(auth.co_approvers or ())


def approve_change(
    repo: Repository,
    sink: EventSink,
    change_request: str,
    actor: Actor,
    replay: HandlerReplay,
    comment: str | None = None,
) -> Outcome:
    """Approve (one approver). Only the ``resolved_approver`` or a listed
    co-approver may approve. When the LAST required approval lands, replay the
    handler AS the resolved_approver, link ``resulting_event``, emit
    ``CHANGE_APPROVED``, and move to APPROVED. Until then the CR stays PROPOSED.
    """
    cr = repo.get_change_request(change_request)
    _require_proposed(cr)

    # Multi-change (batch) CR: approve the items this actor is authorized for;
    # apply the whole batch atomically once every item is approved.
    if cr.get("changes"):
        return _approve_batch(repo, sink, cr, change_request, actor, replay, comment)

    # Decision-time re-resolution (ARCHITECTURE §5): recompute the approver +
    # co-approvers from CURRENT grants/ownership, not the value stored at proposal.
    re_approver, re_co = _reresolve_approver(cr, repo)
    # required = approvals that must ALL land to complete (approver + any moveNode
    # co-approvers). allowed additionally includes a column's editors for
    # column-targeted CRs ("actor == resolved_approver OR a column editor").
    required = {re_approver} | set(re_co)
    allowed = set(required) | _column_editor_approvers(cr, repo)
    if actor.user not in allowed:
        raise AuthorizationError(
            f"{actor.user} is not an approver of {change_request}"
        )

    approvals = set(cr.get("approvals") or [])
    approvals.add(actor.user)
    patch: dict[str, Any] = {"approvals": sorted(approvals)}
    # Persist the re-routed approver so the CR row reflects who actually owns it now.
    if re_approver != cr.get("resolved_approver"):
        patch["resolved_approver"] = re_approver
    repo.update_change_request(change_request, patch)

    if re_co:
        # Dual-end (moveNode): approver AND every co-approver must approve.
        complete = required.issubset(approvals)
    else:
        # Single-approver CR: one approval from an allowed approver suffices (a
        # column editor approving stands in for the column owner).
        complete = True
    if not complete:
        return Outcome(
            kind="suggested",
            change_request=change_request,
            data={"pending_approvers": sorted(required - approvals)},
        )

    # All required approvals collected → replay AS the (re-resolved) approver.
    approver_actor = Actor(user=re_approver, actor_type=ActorType.HUMAN)
    resulting_event = replay(cr, approver_actor, repo, sink)

    # Carry the mutation's location (node/parent/column/ancestor_ids) into the
    # CHANGE_APPROVED event so branch/column-scoped subscribers match it the same
    # way they matched the underlying change — even when the target was deleted.
    loc = {
        k: resulting_event.payload[k]
        for k in ("node", "parent", "branch_root", "column", "ancestor_ids")
        if resulting_event and (resulting_event.payload or {}).get(k) is not None
    }
    approved_event = sink.emit(
        TreeEvent(
            sheet=cr["sheet"],
            type=EventType.CHANGE_APPROVED.value,
            payload={"change_request": change_request, "comment": comment, **loc},
            actor=actor.user,
            actor_type=actor.actor_type,
            change_request=change_request,
        )
    )
    repo.update_change_request(
        change_request,
        {
            "status": CRStatus.APPROVED.value,
            "decided_by": actor.user,
            "resulting_event": resulting_event.event_id if resulting_event else None,
        },
    )
    return Outcome(
        kind="executed",
        event=approved_event,
        change_request=change_request,
        data={"resulting_event": resulting_event.event_id if resulting_event else None},
    )


def _approve_batch(
    repo: Repository,
    sink: EventSink,
    cr: dict[str, Any],
    change_request: str,
    actor: Actor,
    replay: HandlerReplay,
    comment: str | None,
) -> Outcome:
    """Approve the batch items this actor owns (re-resolved at decision time);
    apply ALL items atomically once every item has an approval."""
    items = [dict(it) for it in cr["changes"]]
    approved_new = False
    for it in items:
        syn = _synthetic_item_cr(cr, it)
        re_app, re_co = _reresolve_approver(syn, repo)
        it["resolved_approver"] = re_app  # reflect current owner
        allowed = {re_app} | set(re_co) | _column_editor_approvers(syn, repo)
        if not it.get("item_approved") and actor.user in allowed:
            it["item_approved"] = True
            it["approved_by"] = actor.user
            approved_new = True

    all_done = all(it.get("item_approved") for it in items)
    if not approved_new and not all_done:
        raise AuthorizationError(
            f"{actor.user} is not an approver of any change in {change_request}"
        )
    repo.update_change_request(change_request, {"changes": items})

    if not all_done:
        pending = [it["resolved_approver"] for it in items if not it.get("item_approved")]
        return Outcome(
            kind="suggested",
            change_request=change_request,
            data={"pending_approvers": sorted(set(pending))},
        )

    # Every item approved → apply the whole batch atomically, in order.
    last_event = None
    for it in items:
        syn = _synthetic_item_cr(cr, it)
        re_app, _ = _reresolve_approver(syn, repo)
        last_event = replay(syn, Actor(user=re_app, actor_type=ActorType.HUMAN), repo, sink)
    approved_event = sink.emit(
        TreeEvent(
            sheet=cr["sheet"],
            type=EventType.CHANGE_APPROVED.value,
            payload={"change_request": change_request, "comment": comment, "changes": len(items)},
            actor=actor.user,
            actor_type=actor.actor_type,
            change_request=change_request,
        )
    )
    repo.update_change_request(
        change_request,
        {
            "status": CRStatus.APPROVED.value,
            "decided_by": actor.user,
            "resulting_event": approved_event.event_id,
        },
    )
    return Outcome(
        kind="executed",
        event=approved_event,
        change_request=change_request,
        data={"applied": len(items)},
    )


def reject_change(
    repo: Repository,
    sink: EventSink,
    change_request: str,
    actor: Actor,
    comment: str | None = None,
) -> Outcome:
    """Reject (no mutation). Only an approver may reject. Emits CHANGE_REJECTED."""
    cr = repo.get_change_request(change_request)
    _require_proposed(cr)
    # A batch CR: any actor who approves at least one item may reject the whole
    # batch (atomic — nothing applies).
    if cr.get("changes"):
        allowed = set()
        for it in cr["changes"]:
            syn = _synthetic_item_cr(cr, it)
            re_app, re_co = _reresolve_approver(syn, repo)
            allowed |= {re_app} | set(re_co) | _column_editor_approvers(syn, repo)
        if actor.user not in allowed:
            raise AuthorizationError(f"{actor.user} is not an approver of {change_request}")
    else:
        # Same decision-time re-resolution as approve: only a CURRENT approver/editor
        # may reject.
        re_approver, re_co = _reresolve_approver(cr, repo)
        allowed = {re_approver} | set(re_co) | _column_editor_approvers(cr, repo)
        if actor.user not in allowed:
            raise AuthorizationError(f"{actor.user} is not an approver of {change_request}")

    event = sink.emit(
        TreeEvent(
            sheet=cr["sheet"],
            type=EventType.CHANGE_REJECTED.value,
            payload={"change_request": change_request, "comment": comment},
            actor=actor.user,
            actor_type=actor.actor_type,
            change_request=change_request,
        )
    )
    repo.update_change_request(
        change_request,
        {"status": CRStatus.REJECTED.value, "decided_by": actor.user},
    )
    return Outcome(kind="executed", event=event, change_request=change_request)


def withdraw_change(
    repo: Repository,
    sink: EventSink,
    change_request: str,
    actor: Actor,
    comment: str | None = None,
) -> Outcome:
    """Withdraw (requester-only). Keeps the closed-event set: emits
    CHANGE_REJECTED with ``payload.reason="withdrawn"`` (DECISIONS ADR-003);
    status becomes WITHDRAWN."""
    cr = repo.get_change_request(change_request)
    _require_proposed(cr)
    if actor.user != cr["requester"]:
        raise AuthorizationError(
            f"only the requester {cr['requester']} may withdraw {change_request}"
        )

    event = sink.emit(
        TreeEvent(
            sheet=cr["sheet"],
            type=EventType.CHANGE_REJECTED.value,
            payload={
                "change_request": change_request,
                "reason": "withdrawn",
                "comment": comment,
            },
            actor=actor.user,
            actor_type=actor.actor_type,
            change_request=change_request,
        )
    )
    repo.update_change_request(
        change_request,
        {"status": CRStatus.WITHDRAWN.value, "decided_by": actor.user},
    )
    return Outcome(kind="executed", event=event, change_request=change_request)
