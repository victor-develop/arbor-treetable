"""The two-axis ACL resolver — the ONE resolver (ARCHITECTURE §2, PERMISSIONS §1).

Pure: operates on the injected Repository, never on frappe. Reused by web, REST,
and agent through ``execute_action`` so every surface resolves authority
identically (surface parity, ARCHITECTURE §11).

Axis 1 (structure): vertical, subtree-scoped, delegable — nearest active Branch
Grant on the ancestor chain wins, else the sheet's root ``structural_owner``.
Axis 2 (column): horizontal, field-scoped — ``column_owner`` + ``editors``.
"""

from __future__ import annotations

from typing import Optional

from .ports import ColumnView, Repository
from .types import Actor, Authority, Axis, Capability


# ---------- Axis 1: STRUCTURAL ----------------------------------------------
def resolve_structural_approver(repo: Repository, sheet: str, node: Optional[str]) -> str:
    """Authoritative approver for an add/move/delete affecting ``node``.

    Walks ancestors self -> root (nearest first); the nearest active
    ``scope=structure`` Branch Grant's grantee wins; falls back to the sheet's
    root ``structural_owner``. ``node is None`` means "add at root level" → the
    sheet owner directly (PERMISSIONS §1).
    """
    sheet_view = repo.get_sheet(sheet)
    if node is None:
        return sheet_view.structural_owner

    for ancestor in repo.ancestors_self(node):  # nearest-first
        grant = repo.find_active_branch_grant(
            sheet=sheet, branch_root=ancestor.name, scope="structure"
        )
        if grant is not None:
            return grant.grantee

    return sheet_view.structural_owner


# ---------- Axis 2: COLUMN --------------------------------------------------
def resolve_column_approvers(repo: Repository, sheet: str, column: str) -> set[str]:
    """The set of users who may edit/approve this column's values: the owner
    plus every editor (PERMISSIONS §1)."""
    col = repo.get_column(sheet, column)
    approvers = {col.column_owner}
    approvers |= set(col.editors or [])
    return approvers


# ---------- Read-ACL (Feature 3, LEAN 3-level) ------------------------------
def can_read_column(
    repo: Repository, sheet, col: ColumnView, actor: Actor
) -> bool:
    """Whether ``actor`` may read ``col``'s values (Feature 3, LEAN model).

    ``sheet`` is a SheetView (or its name) — accepted for signature symmetry
    with the rest of the resolver; only ``col.name`` is needed to resolve the
    column approvers. The rule lives ONLY here (and ``visible_columns``).

    Order (the executable contract in tests/core/test_acl_read.py):
      1. ``actor.is_admin``                         -> True
      2. ``col.is_label``                           -> True (labels always show)
      3. ``actor.user in column approvers``         -> True (editors-can-read;
                                                       also covers owner-only)
      4. dispatch on ``col.read_level``:
           ``public``           -> True
           ``owner-only``       -> False  (only non-approvers reach here)
           ``explicit-readers`` -> ``actor.user in col.readers``
    Any unknown/legacy level coalesces to ``public``.
    """
    if getattr(actor, "is_admin", False):
        return True
    if col.is_label:
        return True

    sheet_name = getattr(sheet, "name", sheet)
    approvers = resolve_column_approvers(repo, sheet_name, col.name)
    if actor.user in approvers:
        return True

    level = (getattr(col, "read_level", None) or "public")
    if level == "public":
        return True
    if level == "explicit-readers":
        return actor.user in (col.readers or [])
    # owner-only (and any unknown level treated conservatively as owner-only here,
    # because public/explicit are handled above): non-approvers are denied.
    if level == "owner-only":
        return False
    # Unknown level -> coalesce to public (legacy-row safety).
    return True


def visible_columns(repo: Repository, sheet, actor: Actor, columns) -> list:
    """Filter ``columns`` to those ``actor`` may read, preserving input order.

    The ONE place the read-ACL filter is applied for BOTH the snapshot and the
    explore surface, so they can never diverge.
    """
    return [c for c in columns if can_read_column(repo, sheet, c, actor)]


# ---------- Composition -----------------------------------------------------
def resolve_authority(
    cap: Capability, params: dict, actor: Actor, repo: Repository
) -> Authority:
    """Decide whether ``actor`` may directly perform ``cap`` with ``params``.

    Returns ``Authority(is_authorized, resolved_approver, co_approvers)``. When
    not authorized, ``resolved_approver`` is who the Change Request routes to.
    """
    sheet = params.get("sheet")

    if cap.axis == Axis.STRUCTURE:
        return _resolve_structure_authority(cap, params, actor, repo, sheet)

    if cap.axis == Axis.COLUMN:
        return _resolve_column_authority(cap, params, actor, repo, sheet)

    if cap.axis == Axis.META:
        return _resolve_meta_authority(cap, params, actor, repo, sheet)

    # Axis.NONE — control actions (snapshot, CR lifecycle, subscribe/ack).
    # These are gated by the executor/change_request module, not here; treat as
    # authorized so they take the direct path.
    return Authority(is_authorized=True, resolved_approver=actor.user)


def _resolve_structure_authority(cap, params, actor, repo, sheet) -> Authority:
    if cap.id == "moveNode":
        src_node = repo.get_node(params["node"]).parent  # source PARENT branch
        src = resolve_structural_approver(repo, sheet, src_node)
        dest = resolve_structural_approver(repo, sheet, params.get("new_parent"))
        authorized = actor.user == src and actor.user == dest
        # Route to dest; the OTHER (src) end becomes a required co-approver on a
        # single CR via payload.co_approvers (DECISIONS ADR-001). When src==dest
        # there is no distinct second approver. The co-approver is recorded even
        # if it equals the actor — the actor's authority over that end is exactly
        # what must be re-confirmed at decision time.
        co = (src,) if src != dest else ()
        return Authority(
            is_authorized=authorized,
            resolved_approver=dest,
            co_approvers=co,
        )

    if cap.id == "addNode":
        approver = resolve_structural_approver(repo, sheet, params.get("parent"))
    elif cap.id == "deleteNode":
        approver = resolve_structural_approver(repo, sheet, params["node"])
    elif cap.id == "delegateBranch":
        approver = resolve_structural_approver(repo, sheet, params["branch_root"])
    elif cap.id == "revokeDelegation":
        # granted_by OR an ancestor structural owner may revoke.
        grant_view = repo.get_branch_grant(params["branch_grant"])
        if grant_view is None:
            return Authority(is_authorized=False, resolved_approver=actor.user)
        anc_owner = resolve_structural_approver(repo, grant_view.sheet, grant_view.branch_root)
        authorized = actor.user in {grant_view.granted_by, anc_owner}
        return Authority(is_authorized=authorized, resolved_approver=grant_view.granted_by)
    else:
        approver = resolve_structural_approver(repo, sheet, params.get("node"))

    return Authority(is_authorized=(actor.user == approver), resolved_approver=approver)


def _resolve_column_authority(cap, params, actor, repo, sheet) -> Authority:
    if cap.id == "grantColumn":
        # current column_owner OR sheet structural_owner may re-grant.
        col = repo.get_column(sheet, params["column"])
        sheet_owner = repo.get_sheet(sheet).structural_owner
        authorized = actor.user in {col.column_owner, sheet_owner}
        return Authority(is_authorized=authorized, resolved_approver=col.column_owner)

    col = repo.get_column(sheet, params["column"])
    approvers = resolve_column_approvers(repo, sheet, params["column"])
    return Authority(
        is_authorized=(actor.user in approvers),
        resolved_approver=col.column_owner,  # CR routes to owner
    )


def _resolve_meta_authority(cap, params, actor, repo, sheet) -> Authority:
    if cap.id == "addColumn":
        # column_creation policy placeholder, default "owner-only" (DECISIONS
        # ADR-002): authority = sheet structural_owner.
        sheet_view = repo.get_sheet(sheet)
        policy = (sheet_view.settings or {}).get("column_creation", "owner-only")
        owner = sheet_view.structural_owner
        if policy == "owner-only":
            return Authority(is_authorized=(actor.user == owner), resolved_approver=owner)
        # future policies fall back to owner-only semantics
        return Authority(is_authorized=(actor.user == owner), resolved_approver=owner)

    # updateColumn / deleteColumn → column approvers.
    col = repo.get_column(sheet, params["column"])
    approvers = resolve_column_approvers(repo, sheet, params["column"])
    return Authority(
        is_authorized=(actor.user in approvers),
        resolved_approver=col.column_owner,
    )
