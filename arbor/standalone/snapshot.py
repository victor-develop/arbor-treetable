"""Sheet-snapshot assembly for the standalone API — the SQLAlchemy twin of the
frappe ``arbor.arbor.api.get_sheet_snapshot`` assembly (ARCHITECTURE §4.3).

The serializer itself is the FROZEN pure one (``arbor.core.snapshot``); this
module only does what the frappe api layer did around it:

1. read-ACL filter the columns (``acl.visible_columns`` — the ONE rule);
2. bulk-load every cell value + version for the sheet in ONE query, filtered to
   visible columns x present nodes (nothing forbidden can leak);
3. mark pending cells from the sheet's OPEN (proposed) Change Requests —
   single-change payloads AND multi-change items;
4. mark per-cell comment summaries (thread ROOTS only, tombstones still count);
5. compute the per-actor ACL hints off the ONE resolver (never re-derived);
6. call ``serialize_snapshot`` and overlay the impersonation viewer block.

No Tree Event is emitted (read). Kept out of ``app.py`` so the route stays a
thin shim.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from arbor.core.acl import (
    resolve_column_approvers,
    resolve_structural_approver,
    visible_columns,
)
from arbor.core.snapshot import serialize_snapshot
from arbor.core.types import Actor

from . import models as m
from .repository import SQLRepository


def _pending_cell_marks(
    session: Session, repo: SQLRepository, sheet: str, visible_col_names: set[str]
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """``{(node, column): [{change_request, requester, value}]}`` from the
    sheet's OPEN (proposed) Change Requests that target a cell.

    Covers single-change CRs (whose ``payload`` carries node+column+value) and
    the items of a multi-change CR. Only cells whose column is in
    ``visible_col_names`` (already read-ACL-filtered) are marked, so a pending
    marker can never reveal a column the viewer may not read."""
    marks: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def _add(node: Any, column: Any, value: Any, cr_name: str, requester: Any) -> None:
        if not node or not column or column not in visible_col_names:
            return
        marks.setdefault((node, column), []).append(
            {"change_request": cr_name, "requester": requester, "value": value}
        )

    names = session.scalars(
        sa.select(m.ChangeRequest.name)
        .where(m.ChangeRequest.sheet == sheet, m.ChangeRequest.status == "proposed")
        .order_by(m.ChangeRequest.creation.asc())
    ).all()
    for name in names:
        cr = repo.get_change_request(name)
        requester = cr.get("requester")
        items = cr.get("changes") or []
        if items:
            for it in items:
                p = it.get("payload") or {}
                _add(p.get("node"), p.get("column"), p.get("value"), name, requester)
        else:
            p = cr.get("payload") or {}
            _add(p.get("node"), p.get("column"), p.get("value"), name, requester)
    return marks


def _cell_comment_marks(
    session: Session, sheet: str, visible_col_names: set[str], node_names: set[str]
) -> dict[tuple[str, str], dict[str, int]]:
    """``{(node, column): {open, resolved, unresolved}}`` — the per-cell comment
    summary the grid renders a glyph off (Area 2).

    Counts THREAD ROOTS only (``thread_root`` NULL — one badge per thread, not
    per reply); a deleted-but-tombstoned root still counts. Built ONLY over the
    read-ACL-visible columns + present nodes (same guarantee as the pending
    marks and the values loop). ONE grouped query, never N+1."""
    marks: dict[tuple[str, str], dict[str, int]] = {}
    if not visible_col_names or not node_names:
        return marks
    rows = session.execute(
        sa.select(m.CellComment.node, m.CellComment.column, m.CellComment.resolved).where(
            m.CellComment.sheet == sheet, m.CellComment.thread_root.is_(None)
        )
    ).all()
    for node, column, resolved in rows:
        if column not in visible_col_names or node not in node_names:
            continue
        summary = marks.setdefault((node, column), {"open": 0, "resolved": 0, "unresolved": 0})
        if resolved:
            summary["resolved"] += 1
        else:
            summary["open"] += 1
            summary["unresolved"] += 1
    return marks


def _acl_hints(
    session: Session, repo: SQLRepository, actor: Actor, sheet: str, columns, nodes
) -> dict[str, Any]:
    """The edit/structure affordances the thin React shell renders from
    (ARCHITECTURE §2.3). Reuses the ONE ACL resolver — no re-implementation."""
    can_edit_column = {
        c.name: actor.user in resolve_column_approvers(repo, sheet, c.name) for c in columns
    }
    can_change_structure = {
        n.name: actor.user == resolve_structural_approver(repo, sheet, n.name) for n in nodes
    }
    sheet_owner = repo.get_sheet(sheet).structural_owner
    # The viewer's own sheet-scoped subscription powers the subscribe control.
    subscription = session.scalars(
        sa.select(m.Subscription.name).where(
            m.Subscription.subscriber == actor.user,
            m.Subscription.scope == "sheet",
            m.Subscription.target == sheet,
        )
    ).first()
    # Active branch delegations. can_revoke gates the UI affordance (granter or
    # the sheet's structural owner); the server re-enforces on dispatch anyway.
    grants = session.scalars(
        sa.select(m.BranchGrant)
        .where(m.BranchGrant.sheet == sheet, m.BranchGrant.active.is_(True))
        .order_by(m.BranchGrant.creation.asc())
    ).all()
    branch_grants = [
        {
            "name": g.name,
            "branch_root": g.branch_root,
            "grantee": g.grantee,
            "granted_by": g.granted_by,
            "can_revoke": g.granted_by == actor.user or actor.user == sheet_owner,
        }
        for g in grants
    ]
    return {
        "can_edit_column": can_edit_column,
        "can_change_structure": can_change_structure,
        "actor": actor.user,
        "can_add_column": actor.user == sheet_owner,
        # Platform-admin hint: the ONLY gate for the admin Roles panel.
        "is_admin": bool(getattr(actor, "is_admin", False)),
        # Impersonation viewer block (Area 1): powers the "acting as" banner +
        # the stop control off snapshot hints, with NO ACL re-derivation.
        "impersonating": bool(getattr(actor, "is_impersonated", False)),
        "real_user": actor.real_user if getattr(actor, "is_impersonated", False) else None,
        "effective_user": actor.user,
        "subscribed": bool(subscription),
        "subscription": subscription,
        "branch_grants": branch_grants,
    }


def build_sheet_snapshot(
    session: Session, repo: SQLRepository, sheet: str, actor: Actor
) -> dict[str, Any]:
    """Assemble the canonical snapshot for one sheet + one viewer (steps 3-9 of
    the frappe assembly; the existence check + size guard are the route's)."""
    sheet_view = repo.get_sheet(sheet)
    # Read-ACL (Feature 3): filter BEFORE building values/hints, so a forbidden
    # column drops from headers AND every node's cells together.
    columns = visible_columns(repo, sheet_view, actor, repo.list_columns(sheet))
    nodes = repo.list_nodes(sheet)

    # Bulk-load every cell for the sheet in ONE query (never N+1), filtered to
    # the visible columns + present nodes. Values are a native JSON column here
    # (no string re-parse needed); version 0 / missing rows carry no version.
    visible_col_names = {c.name for c in columns}
    node_names = {n.name for n in nodes}
    values: dict[tuple[str, str], Any] = {}
    versions: dict[tuple[str, str], int] = {}
    rows = session.execute(
        sa.select(
            m.NodeValue.node, m.NodeValue.column, m.NodeValue.value, m.NodeValue.version
        ).where(m.NodeValue.sheet == sheet)
    ).all()
    for node, column, value, version in rows:
        if column not in visible_col_names or node not in node_names:
            continue
        key = (node, column)
        if value is not None:
            values[key] = value
        if version:
            versions[key] = int(version)

    pending = _pending_cell_marks(session, repo, sheet, visible_col_names)
    comments = _cell_comment_marks(session, sheet, visible_col_names, node_names)

    acl_hints = _acl_hints(session, repo, actor, sheet, columns, nodes)
    snap = serialize_snapshot(
        sheet_view, columns, nodes, values, acl_hints, versions=versions, pending=pending
    )

    # Fold the per-cell comment summary onto each node under a sparse
    # ``comments`` map (mirrors how ``pending`` is threaded).
    if comments:
        by_node: dict[str, dict[str, dict[str, int]]] = {}
        for (node_name, col_name), summary in comments.items():
            by_node.setdefault(node_name, {})[col_name] = summary
        for n in snap.get("nodes", []):
            cmap = by_node.get(n.get("name"))
            if cmap:
                n["comments"] = cmap

    # Impersonation viewer block (Area 1): the pure serializer's ``viewer`` is
    # framework-free; overlay the "act as" hints here so banner + grid agree.
    snap["viewer"]["impersonating"] = acl_hints.get("impersonating", False)
    snap["viewer"]["real_user"] = acl_hints.get("real_user")
    snap["viewer"]["effective_user"] = acl_hints.get("effective_user", actor.user)
    return snap
