"""The snapshot serializer — the ONE shape consumed by web, REST, and agent
(ARCHITECTURE §4.3). Pure: given already-fetched data + ACL hints, it produces a
plain dict. The frappe adapter fetches the rows and computes the hints, then
calls this; the agent and REST reuse the exact same output.
"""

from __future__ import annotations

from typing import Any, Iterable

from .ports import ColumnView, NodeView, SheetView


def serialize_snapshot(
    sheet: SheetView,
    columns: Iterable[ColumnView],
    nodes: Iterable[NodeView],
    values: dict[tuple[str, str], Any],
    acl_hints: dict[str, Any],
    versions: dict[tuple[str, str], int] | None = None,
    pending: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Serialize one sheet into the canonical snapshot.

    ``values`` is keyed by ``(node_name, column_name)``. ``acl_hints`` carries
    the per-actor affordances the thin React shell renders edit-vs-suggest from:
    ``{"can_edit_column": {col_name: bool}, "can_change_structure": {node: bool},
       "actor": str}``. The serializer copies hints onto each column/node so the
    UI never re-derives ACL.

    Feature 1 — optimistic concurrency: ``versions`` (keyed identically to
    ``values``) supplies the per-cell stored version. Each node emits a parallel
    ``versions`` map covering exactly the same columns as ``values`` (an
    absent/empty cell -> 0), which the FE folds into a base_version for the next
    edit. Omitting ``versions`` yields a safe all-zero map (kept present for FE
    symmetry); the existing ``values`` dict and every other key are byte-identical
    regardless, so the existing snapshot + FE golden tests don't move.
    """
    versions = versions or {}
    pending = pending or {}
    col_list = list(columns)
    node_list = list(nodes)
    can_edit_col = acl_hints.get("can_edit_column", {})
    can_struct = acl_hints.get("can_change_structure", {})

    serialized_columns = [
        {
            "name": c.name,
            "field": c.field,
            "label": getattr(c, "label", c.field),
            "type": getattr(c, "type", "text"),
            "is_label": c.is_label,
            "column_owner": c.column_owner,
            "editors": list(c.editors or []),
            "can_edit": bool(can_edit_col.get(c.name, False)),
            # Read-ACL (Feature 3): the caller has ALREADY filtered ``columns``
            # through ``visible_columns``, so every serialized column is readable
            # by definition (can_read always True). ``read_level`` is carried for
            # FE symmetry (e.g. a lock badge on restricted columns).
            "can_read": True,
            "read_level": getattr(c, "read_level", "public") or "public",
            # select-split columns carry their option groups; the cell renders
            # segments from this (None for non-select columns).
            "options": getattr(c, "options", None),
        }
        for c in col_list
    ]

    label_col = next((c.name for c in col_list if c.is_label), None)

    serialized_nodes = []
    for n in sorted(node_list, key=lambda x: x.lft):
        cells = {
            c.name: values.get((n.name, c.name)) for c in col_list
        }
        # Parallel per-cell version map (Feature 1): same keys as ``cells``,
        # 0 for an absent/empty cell so the FE always has a base_version.
        cell_versions = {
            c.name: versions.get((n.name, c.name), 0) for c in col_list
        }
        # Per-cell pending suggestions (open Change Requests targeting this cell).
        # Sparse: only cells with >=1 pending mark appear (a cell with none is
        # simply absent), keyed by column name. Each mark is
        # ``{change_request, requester, value}`` so the FE can light the marker
        # AND show "by <requester> -> <proposed value>" without a second fetch.
        # The caller (api.get_sheet_snapshot) builds this from the sheet's
        # ``proposed`` CRs, and ONLY for the already read-ACL-filtered columns —
        # so a marker can never leak a column the viewer cannot read.
        cell_pending = {
            c.name: pending[(n.name, c.name)]
            for c in col_list
            if pending.get((n.name, c.name))
        }
        serialized_nodes.append(
            {
                "name": n.name,
                "parent": n.parent,
                "lft": n.lft,
                "rgt": n.rgt,
                # idx orders siblings under the same parent (NestedSet lft only
                # encodes nesting + a name-based order; user reordering lives in idx).
                "idx": getattr(n, "idx", 0) or 0,
                "label": values.get((n.name, label_col)) if label_col else None,
                "values": cells,
                "versions": cell_versions,
                "pending": cell_pending,
                "can_change_structure": bool(can_struct.get(n.name, False)),
            }
        )

    return {
        "sheet": {
            "name": sheet.name,
            "structural_owner": sheet.structural_owner,
            "settings": dict(getattr(sheet, "settings", {}) or {}),
        },
        "columns": serialized_columns,
        "nodes": serialized_nodes,
        "label_column": label_col,
        "actor": acl_hints.get("actor"),
        # Sheet-level affordances for the thin shell (add-column gate, and the
        # viewer's own sheet subscription for the subscribe/unsubscribe control).
        "viewer": {
            "can_add_column": bool(acl_hints.get("can_add_column", False)),
            # Platform-admin hint — gates the admin Roles panel (Feature: roles).
            "is_admin": bool(acl_hints.get("is_admin", False)),
            "subscribed": bool(acl_hints.get("subscribed", False)),
            "subscription": acl_hints.get("subscription"),
            # active branch delegations on this sheet (for the delegation control);
            # each carries can_revoke so the UI gates the revoke affordance.
            "branch_grants": list(acl_hints.get("branch_grants", [])),
        },
    }
