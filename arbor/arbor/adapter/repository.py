"""FrappeRepository + FrappeEventSink — the data/event ADAPTERS.

These implement the pure core's ``Repository`` and ``EventSink`` protocols
(``arbor.core.ports``) over the Frappe ORM and the NestedSet mixin. The core's
ACL resolver, capability handlers, executor, CR state machine and snapshot
serializer call ONLY these methods — never frappe directly — so the exact same
governance logic that the bench-free core tests exercise against
``InMemoryRepository`` runs here against a real site.

Mapping (DATA-MODEL.md):

- ``Tree Sheet``         → SheetView(name, structural_owner, settings)
- ``Tree Node``          → NodeView(name, sheet, parent, lft, rgt)
- ``Tree Column``        → ColumnView(name, sheet, field, column_owner, editors, is_label)
- ``Tree Node Value``    → keyed by (node, column); value + version
- ``Branch Grant``       → BranchGrantView(name, sheet, branch_root, grantee, scope, active)
- ``Change Request`` / ``Subscription`` / ``Notification`` / ``Acknowledgement``
- ``Tree Event``         → written by FrappeEventSink.emit ONLY.

NestedSet ancestor walk uses the lft/rgt range query mandated by DATA-MODEL §3:
``WHERE sheet=? AND lft<=n.lft AND rgt>=n.rgt ORDER BY lft DESC`` (nearest-first).

Concurrency / integrity contracts the API layer relies on (api.md J + I):
- ``move_node`` raises ``StaleMoveError`` when the caller's positional revision
  is stale, and ``CycleError`` when the move would create a NestedSet cycle.
- ``set_value`` raises ``StaleVersionError`` when an ``expected_version`` guard
  does not match the stored counter (optimistic concurrency / lost-update).
These are surfaced as HTTP 409 by :mod:`arbor.api`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import frappe
from frappe.utils.nestedset import NestedSetRecursionError

from arbor.core.types import ActorType, TreeEvent

# DocType names (single source of truth for the adapter).
DT_SHEET = "Tree Sheet"
DT_COLUMN = "Tree Column"
DT_NODE = "Tree Node"
DT_VALUE = "Tree Node Value"
DT_GRANT = "Branch Grant"
DT_CR = "Change Request"
DT_SUBSCRIPTION = "Subscription"
DT_NOTIFICATION = "Notification"
DT_ACK = "Acknowledgement"
DT_EVENT = "Tree Event"
# role management (Feature: roles)
DT_ROLE = "Arbor Role"
DT_ROLE_GRANT = "Arbor Role Grant"
DT_ROLE_APP = "Arbor Role Application"
DT_IMPERSONATION = "Arbor Impersonation Session"
# process / SLA (Area 3)
DT_PROCESS = "Arbor Process"
DT_PROCESS_RUN = "Arbor Process Run"
SYSTEM_MANAGER = "System Manager"


def _change_item_row(ch: dict[str, Any]) -> dict[str, Any]:
    """A multi-change CR item (core dict) → a Change Request Change child row.
    ``payload`` is a JSON child column, so store the encoded string."""
    return {
        "action": ch["action"],
        "target_kind": ch.get("target_kind"),
        "operation": ch.get("operation"),
        "payload": frappe.as_json(ch.get("payload") or {}),
        "resolved_approver": ch.get("resolved_approver"),
        "item_approved": 1 if ch.get("item_approved") else 0,
        "approved_by": ch.get("approved_by"),
    }


def _change_item_view(row: Any) -> dict[str, Any]:
    """A Change Request Change child row → the core's item dict."""
    payload = row.payload
    if isinstance(payload, str):
        payload = frappe.parse_json(payload) if payload else {}
    return {
        "action": row.action,
        "target_kind": row.target_kind,
        "operation": row.operation,
        "payload": payload or {},
        "resolved_approver": row.resolved_approver,
        "item_approved": bool(row.item_approved),
        "approved_by": row.approved_by,
    }


# ---------------------------------------------------------------------------
# Adapter-specific integrity / concurrency errors. The API layer maps these to
# HTTP 409 (they are NOT core domain errors — they are storage-level conflicts).
# ---------------------------------------------------------------------------
class ConflictError(Exception):
    """A storage-level integrity/concurrency conflict → HTTP 409."""


class StaleMoveError(ConflictError):
    """The caller's positional revision for a move is stale (api.md API-160)."""


class CycleError(ConflictError):
    """A move would put a node under its own descendant (api.md API-150)."""


class StaleVersionError(ConflictError):
    """Optimistic-concurrency: stored cell version != expected (api.md API-161).

    Carries ``current_version`` and ``current_value`` (the authoritative stored
    state) so the API seam can build the VERSION_CONFLICT payload without a
    second read."""

    def __init__(
        self,
        message: str = "",
        *,
        current_version: int = 0,
        current_value: Any = None,
    ) -> None:
        super().__init__(message)
        self.current_version = current_version
        self.current_value = current_value


# ---------------------------------------------------------------------------
# Lightweight read views (duck-typed to ports.*View). Built from frappe docs so
# the core never touches a Document. Mirrors core.testing's view dataclasses.
# ---------------------------------------------------------------------------
@dataclass
class _SheetView:
    name: str
    structural_owner: str
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class _NodeView:
    name: str
    sheet: str
    parent: Optional[str]
    lft: int
    rgt: int
    idx: int = 0


@dataclass
class _ColumnView:
    name: str
    sheet: str
    field: str
    column_owner: str
    editors: list[str] = field(default_factory=list)
    is_label: bool = False
    label: str = ""
    type: str = "text"
    options: Optional[dict] = None
    # Read-ACL (Feature 3, LEAN): {public, explicit-readers, owner-only}.
    read_level: str = "public"
    readers: list[str] = field(default_factory=list)


@dataclass
class _GrantView:
    name: str
    sheet: str
    branch_root: str
    grantee: str
    scope: str = "structure"
    active: bool = True
    granted_by: Optional[str] = None


@dataclass
class _RoleView:
    name: str
    role: str
    label: str = ""
    applicable: bool = True
    active: bool = True


@dataclass
class _RoleGrantView:
    name: str
    role: str
    grantee: str
    granted_by: str
    active: bool = True
    source: str = "admin-grant"
    granted_via: Optional[str] = None


@dataclass
class _ProcessStageView:
    """One ordered stage (Area 3). ``idx`` is 0-based (mirrors the pure fixture +
    the run-stage ledger), derived from the Frappe child-row order — NOT the raw
    1-based frappe ``idx`` — so ``current_stage_idx`` and run-stage ``stage_idx``
    line up byte-for-byte with the bench-free machine."""

    idx: int
    column: str
    sla_seconds: int = 0
    notify_on_enter: bool = True


@dataclass
class _ProcessView:
    name: str
    sheet: str
    title: str = ""
    enabled: bool = False
    row_scope: str = "root-children"
    start_trigger: str = "node-created"
    sla_breach_notify: bool = True
    stages: list = field(default_factory=list)


class FrappeRepository:
    """``Repository`` implemented over the Frappe ORM + NestedSet.

    All methods take/return plain ids and view objects, never Documents, so the
    pure core stays framework-free. Existence misses raise
    ``frappe.DoesNotExistError`` (the API maps to 404).
    """

    # ---- view builders ----------------------------------------------------
    def _sheet_view(self, doc) -> _SheetView:
        settings = doc.settings
        if isinstance(settings, str):
            settings = frappe.parse_json(settings) if settings else {}
        return _SheetView(
            name=doc.name,
            structural_owner=doc.structural_owner,
            settings=settings or {},
        )

    def _node_view(self, doc) -> _NodeView:
        return _NodeView(
            name=doc.name,
            sheet=doc.sheet,
            parent=doc.parent_tree_node or None,
            lft=int(doc.lft or 0),
            rgt=int(doc.rgt or 0),
            idx=int(doc.idx or 0),
        )

    def _column_view(self, doc) -> _ColumnView:
        editors = [row.user for row in (doc.get("editors") or [])]
        readers = [row.user for row in (doc.get("readers") or [])]
        options = doc.options
        if isinstance(options, str):
            options = frappe.parse_json(options) if options else None
        return _ColumnView(
            name=doc.name,
            sheet=doc.sheet,
            field=doc.field,
            column_owner=doc.column_owner,
            editors=editors,
            is_label=bool(doc.is_label),
            label=doc.label or doc.field,
            type=doc.type or "text",
            options=options,
            # Coalesce legacy rows (no read_level) to 'public'.
            read_level=(doc.get("read_level") or "public"),
            readers=readers,
        )

    def _grant_view(self, doc) -> _GrantView:
        return _GrantView(
            name=doc.name,
            sheet=doc.sheet,
            branch_root=doc.branch_root,
            grantee=doc.grantee,
            scope=doc.scope or "structure",
            active=bool(doc.active),
            granted_by=doc.granted_by,
        )

    # ---- sheets / columns -------------------------------------------------
    def get_sheet(self, sheet: str) -> _SheetView:
        return self._sheet_view(frappe.get_doc(DT_SHEET, sheet))

    def get_column(self, sheet: str, column: str) -> _ColumnView:
        """Resolve by Tree Column ``name`` first, else by ``(sheet, field)``."""
        if frappe.db.exists(DT_COLUMN, column):
            doc = frappe.get_doc(DT_COLUMN, column)
            return self._column_view(doc)
        name = frappe.db.get_value(
            DT_COLUMN, {"sheet": sheet, "field": column}, "name"
        )
        if not name:
            raise frappe.DoesNotExistError(
                f"No column {column!r} in sheet {sheet!r}"
            )
        return self._column_view(frappe.get_doc(DT_COLUMN, name))

    def get_column_by_name(self, column: str) -> _ColumnView:
        return self._column_view(frappe.get_doc(DT_COLUMN, column))

    def list_columns(self, sheet: str) -> list[_ColumnView]:
        names = frappe.get_all(
            DT_COLUMN, filters={"sheet": sheet}, order_by="idx asc, creation asc", pluck="name"
        )
        return [self._column_view(frappe.get_doc(DT_COLUMN, n)) for n in names]

    # ---- nodes (NestedSet) ------------------------------------------------
    def get_node(self, node: str) -> _NodeView:
        return self._node_view(frappe.get_doc(DT_NODE, node))

    def count_nodes(self, sheet: str) -> int:
        """Total node count for ``sheet`` — a cheap ``COUNT(*)`` (the snapshot
        size guard + overview read on it).

        Issues a single ``SELECT COUNT(*)`` rather than materializing rows, so
        the ``get_sheet_snapshot`` >500 guard stays O(1) even on huge sheets.
        """
        return frappe.db.count(DT_NODE, filters={"sheet": sheet})

    def list_nodes(self, sheet: str) -> list[_NodeView]:
        rows = frappe.get_all(
            DT_NODE,
            filters={"sheet": sheet},
            fields=["name", "sheet", "parent_tree_node", "lft", "rgt", "idx"],
            order_by="lft asc",
        )
        return [
            _NodeView(
                name=r.name,
                sheet=r.sheet,
                parent=r.parent_tree_node or None,
                lft=int(r.lft or 0),
                rgt=int(r.rgt or 0),
                idx=int(r.idx or 0),
            )
            for r in rows
        ]

    def ancestors_self(self, node: str) -> list[_NodeView]:
        """[node, parent, ..., root] — nearest-first (DATA-MODEL §3 walk)."""
        n = frappe.db.get_value(
            DT_NODE, node, ["sheet", "lft", "rgt"], as_dict=True
        )
        if not n:
            raise frappe.DoesNotExistError(f"No node {node!r}")
        rows = frappe.get_all(
            DT_NODE,
            filters={
                "sheet": n.sheet,
                "lft": ["<=", n.lft],
                "rgt": [">=", n.rgt],
            },
            fields=["name", "sheet", "parent_tree_node", "lft", "rgt"],
            order_by="lft desc",  # nearest (deepest) ancestor first
        )
        return [
            _NodeView(
                name=r.name,
                sheet=r.sheet,
                parent=r.parent_tree_node or None,
                lft=int(r.lft or 0),
                rgt=int(r.rgt or 0),
            )
            for r in rows
        ]

    def descendants(self, node: str) -> list[_NodeView]:
        """Strict descendants via NestedSet range (DATA-MODEL §3)."""
        n = frappe.db.get_value(
            DT_NODE, node, ["sheet", "lft", "rgt"], as_dict=True
        )
        if not n:
            raise frappe.DoesNotExistError(f"No node {node!r}")
        rows = frappe.get_all(
            DT_NODE,
            filters={
                "sheet": n.sheet,
                "lft": [">", n.lft],
                "rgt": ["<", n.rgt],
            },
            fields=["name", "sheet", "parent_tree_node", "lft", "rgt"],
            order_by="lft asc",
        )
        return [
            _NodeView(
                name=r.name,
                sheet=r.sheet,
                parent=r.parent_tree_node or None,
                lft=int(r.lft or 0),
                rgt=int(r.rgt or 0),
            )
            for r in rows
        ]

    # ---- branch grants ----------------------------------------------------
    def find_active_branch_grant(
        self, sheet: str, branch_root: str, scope: str = "structure"
    ) -> Optional[_GrantView]:
        name = frappe.db.get_value(
            DT_GRANT,
            {
                "sheet": sheet,
                "branch_root": branch_root,
                "scope": scope,
                "active": 1,
            },
            "name",
        )
        if not name:
            return None
        return self._grant_view(frappe.get_doc(DT_GRANT, name))

    def get_branch_grant(self, branch_grant: str) -> Optional[_GrantView]:
        if not frappe.db.exists(DT_GRANT, branch_grant):
            return None
        return self._grant_view(frappe.get_doc(DT_GRANT, branch_grant))

    # ---- cell values ------------------------------------------------------
    def _value_name(self, node: str, column: str) -> Optional[str]:
        return frappe.db.get_value(
            DT_VALUE, {"node": node, "column": column}, "name"
        )

    def get_value(self, node: str, column: str) -> Any:
        name = self._value_name(node, column)
        if not name:
            return None
        raw = frappe.db.get_value(DT_VALUE, name, "value")
        return frappe.parse_json(raw) if isinstance(raw, str) and raw else raw

    def get_value_version(self, node: str, column: str) -> Optional[int]:
        name = self._value_name(node, column)
        if not name:
            return None
        return int(frappe.db.get_value(DT_VALUE, name, "version") or 0)

    # ---- mutators ---------------------------------------------------------
    def create_node(
        self, sheet: str, parent: Optional[str], after: Optional[str] = None
    ) -> str:
        doc = frappe.new_doc(DT_NODE)
        doc.sheet = sheet
        doc.parent_tree_node = parent or None
        doc.is_group = 1
        if after:
            doc.idx = self._sibling_idx_after(sheet, parent, after)
        doc.insert(ignore_permissions=True)  # NestedSet mixin assigns lft/rgt
        return doc.name

    def _sibling_idx_after(
        self, sheet: str, parent: Optional[str], after: str
    ) -> int:
        after_idx = frappe.db.get_value(DT_NODE, after, "idx")
        return (int(after_idx) + 1) if after_idx is not None else 0

    def set_value(
        self,
        sheet: str,
        node: str,
        column: str,
        value: Any,
        expected_version: Optional[int] = None,
    ) -> int:
        """Upsert a cell; return the new version counter.

        When ``expected_version`` is supplied (optimistic concurrency, API-161)
        and it does not match the stored counter, raise ``StaleVersionError``.
        """
        # The ``value`` column is a MariaDB JSON type (CHECK json_valid). Frappe only
        # json-encodes non-str values, so a bare string must be stored pre-encoded as a
        # valid JSON document (e.g. "Phase 1" → '"Phase 1"'); the read path (get_cell)
        # parses it back. None → SQL NULL.
        stored = frappe.as_json(value) if value is not None else None
        name = self._value_name(node, column)
        if name:
            doc = frappe.get_doc(DT_VALUE, name)
            if expected_version is not None and int(doc.version or 0) != int(expected_version):
                cur_raw = doc.value
                cur_val = (
                    frappe.parse_json(cur_raw)
                    if isinstance(cur_raw, str) and cur_raw
                    else cur_raw
                )
                raise StaleVersionError(
                    f"cell {node}/{column} is at version {doc.version}, "
                    f"expected {expected_version}",
                    current_version=int(doc.version or 0),
                    current_value=cur_val,
                )
            doc.value = stored
            doc.version = int(doc.version or 0) + 1
            doc.save(ignore_permissions=True)
            return int(doc.version)
        if expected_version is not None and int(expected_version) != 0:
            raise StaleVersionError(
                f"cell {node}/{column} does not exist; expected version {expected_version}",
                current_version=0,
                current_value=None,
            )
        doc = frappe.new_doc(DT_VALUE)
        doc.sheet = sheet
        doc.node = node
        doc.column = column
        doc.value = stored
        doc.version = 1
        doc.insert(ignore_permissions=True)
        return 1

    def move_node(
        self,
        node: str,
        new_parent: Optional[str],
        after: Optional[str] = None,
        expected_revision: Optional[Any] = None,
    ) -> None:
        """Re-parent a node. Raises ``CycleError`` if the move would create a
        NestedSet cycle, ``StaleMoveError`` if the positional revision is stale.
        """
        doc = frappe.get_doc(DT_NODE, node)
        if new_parent:
            # Reject moving a node under itself or its own descendant.
            if new_parent == node:
                raise CycleError(f"cannot move {node} under itself")
            dest = frappe.db.get_value(
                DT_NODE, new_parent, ["lft", "rgt"], as_dict=True
            )
            src_lft, src_rgt = int(doc.lft or 0), int(doc.rgt or 0)
            if dest and src_lft <= int(dest.lft) and int(dest.rgt) <= src_rgt:
                raise CycleError(
                    f"cannot move {node} under its own descendant {new_parent}"
                )
        if (
            after is not None
            and expected_revision is not None
            and not frappe.db.exists(DT_NODE, after)
        ):
            raise StaleMoveError(
                f"sibling {after!r} no longer exists; client revision is stale"
            )
        doc.parent_tree_node = new_parent or None
        if after:
            doc.idx = self._sibling_idx_after(doc.sheet, new_parent, after)
        try:
            doc.save(ignore_permissions=True)  # NestedSet mixin recomputes lft/rgt
        except NestedSetRecursionError as exc:  # pragma: no cover - belt & braces
            raise CycleError(str(exc)) from exc

    def delete_node(self, node: str, cascade: bool = True) -> list[str]:
        # descendants() returns lft-asc (shallow→deep); descendants come first.
        desc = self.descendants(node) if cascade else []
        deleted = [node] + [d.name for d in desc]
        # Remove cell values first (FK integrity).
        for name in deleted:
            for vname in frappe.get_all(
                DT_VALUE, filters={"node": name}, pluck="name"
            ):
                frappe.delete_doc(DT_VALUE, vname, force=True, ignore_permissions=True)
        # Delete nodes DEEPEST-FIRST so the NestedSet mixin never trips on
        # still-present children (NestedSetChildExistsError). `delete_for_subtree`
        # ordering: reverse of the lft-asc descendant list, then the root.
        for view in reversed(desc):
            frappe.delete_doc(DT_NODE, view.name, force=True, ignore_permissions=True)
        frappe.delete_doc(DT_NODE, node, force=True, ignore_permissions=True)
        return deleted

    def create_column(self, sheet: str, spec: dict[str, Any]) -> str:
        doc = frappe.new_doc(DT_COLUMN)
        doc.sheet = sheet
        doc.field = spec["field"]
        doc.label = spec.get("label") or spec["field"]
        doc.type = spec.get("type", "text")
        doc.options = spec.get("options")
        doc.column_owner = spec.get("column_owner") or ""
        doc.is_label = 1 if spec.get("is_label") else 0
        doc.editable = 1
        doc.read_level = spec.get("read_level") or "public"
        for u in spec.get("editors") or []:
            doc.append("editors", {"user": u})
        for u in spec.get("readers") or []:
            doc.append("readers", {"user": u})
        doc.insert(ignore_permissions=True)  # (sheet, field) unique + single-label enforced by DocType
        return doc.name

    def update_column(self, sheet: str, column: str, patch: dict[str, Any]) -> None:
        col = self.get_column(sheet, column)
        doc = frappe.get_doc(DT_COLUMN, col.name)
        editors = patch.pop("editors", None) if isinstance(patch, dict) else None
        readers = patch.pop("readers", None) if isinstance(patch, dict) else None
        for k, v in (patch or {}).items():
            if k in {"label", "type", "options", "width", "editable", "is_label", "read_level"}:
                doc.set(k, v)
        if editors is not None:
            doc.set("editors", [])
            for u in editors:
                doc.append("editors", {"user": u})
        if readers is not None:
            doc.set("readers", [])
            for u in readers:
                doc.append("readers", {"user": u})
        doc.save(ignore_permissions=True)

    def delete_column(self, sheet: str, column: str) -> None:
        col = self.get_column(sheet, column)
        for vname in frappe.get_all(
            DT_VALUE, filters={"column": col.name}, pluck="name"
        ):
            frappe.delete_doc(DT_VALUE, vname, force=True, ignore_permissions=True)
        frappe.delete_doc(DT_COLUMN, col.name, force=True, ignore_permissions=True)

    def create_branch_grant(
        self, sheet: str, branch_root: str, grantee: str, granted_by: str
    ) -> str:
        doc = frappe.new_doc(DT_GRANT)
        doc.sheet = sheet
        doc.branch_root = branch_root
        doc.grantee = grantee
        doc.granted_by = granted_by
        doc.scope = "structure"
        doc.active = 1
        doc.insert(ignore_permissions=True)
        return doc.name

    def deactivate_branch_grant(self, branch_grant: str) -> None:
        frappe.db.set_value(DT_GRANT, branch_grant, "active", 0)

    def set_column_authority(
        self,
        sheet: str,
        column: str,
        column_owner: Optional[str] = None,
        editors: Optional[list[str]] = None,
    ) -> None:
        col = self.get_column(sheet, column)
        doc = frappe.get_doc(DT_COLUMN, col.name)
        if column_owner is not None:
            doc.column_owner = column_owner
        if editors is not None:
            doc.set("editors", [])
            for u in editors:
                doc.append("editors", {"user": u})
        doc.save(ignore_permissions=True)

    # ---- change requests --------------------------------------------------
    def create_change_request(self, data: dict[str, Any]) -> str:
        doc = frappe.new_doc(DT_CR)
        doc.sheet = data["sheet"]
        doc.target_kind = data["target_kind"]
        doc.operation = data["operation"]
        doc.payload = data.get("payload") or {}
        doc.requester = data["requester"]
        # Impersonation trace (Area 1): the truly-authenticated admin when the CR
        # was proposed under an "act as" overlay; None for a normal CR (so the row
        # is byte-for-byte as before). The core populates this in
        # change_request.create_change_request / create_batch_change_request.
        doc.real_requester = data.get("real_requester")
        doc.resolved_approver = data.get("resolved_approver")
        doc.status = data.get("status", "proposed")
        # approvals[] is tracked in the JSON payload-adjacent field for parity
        # with the in-memory repo; stored as a JSON list on the doc.
        doc.approvals = data.get("approvals") or []
        # changes[] = the items of a multi-change (batch) CR (empty for single-change).
        for ch in data.get("changes") or []:
            doc.append("changes", _change_item_row(ch))
        doc.insert(ignore_permissions=True)
        return doc.name

    def get_change_request(self, change_request: str) -> dict[str, Any]:
        doc = frappe.get_doc(DT_CR, change_request)
        payload = doc.payload
        if isinstance(payload, str):
            payload = frappe.parse_json(payload) if payload else {}
        # approvals is a child table (Change Request Approval rows of {user});
        # the core works with a flat list of approver user-ids, so project it.
        approvals = [row.user for row in (doc.get("approvals") or [])]
        changes = [_change_item_view(row) for row in (doc.get("changes") or [])]
        return {
            "name": doc.name,
            "sheet": doc.sheet,
            "target_kind": doc.target_kind,
            "operation": doc.operation,
            "payload": payload or {},
            "requester": doc.requester,
            "resolved_approver": doc.resolved_approver,
            "status": doc.status,
            "approvals": approvals,
            "changes": changes,
            "decided_by": doc.get("decided_by"),
            "resulting_event": doc.get("resulting_event"),
        }

    def update_change_request(self, change_request: str, patch: dict[str, Any]) -> None:
        doc = frappe.get_doc(DT_CR, change_request)
        for k, v in (patch or {}).items():
            if k == "decided_by" and v:
                doc.decided_by = v
                doc.decided_at = frappe.utils.now()
            elif k == "real_decider":
                # Impersonation trace (Area 1): the truly-authenticated admin who
                # decided a CR under an "act as" overlay; None for a normal
                # decision. Only persisted when the field exists on the schema.
                if doc.meta.has_field("real_decider"):
                    doc.real_decider = v
            elif k == "approvals":
                # core passes a flat list of approver user-ids; materialize the
                # Change Request Approval child rows ({user}).
                doc.set("approvals", [{"user": u} for u in (v or [])])
            elif k == "changes":
                # core passes the full item list; rematerialize the child rows.
                doc.set("changes", [_change_item_row(ch) for ch in (v or [])])
            else:
                doc.set(k, v)
        doc.save(ignore_permissions=True)

    # ---- subscriptions / notifications / acks -----------------------------
    def create_subscription(self, data: dict[str, Any]) -> str:
        doc = frappe.new_doc(DT_SUBSCRIPTION)
        doc.subscriber = data["subscriber"]
        doc.subscriber_kind = data.get("subscriber_kind", "user")
        doc.scope = data["scope"]
        # target is a Dynamic Link; its companion target_doctype is resolved from
        # the scope (sheet→Tree Sheet, branch→Tree Node, column→Tree Column) so the
        # link validates against the right DocType.
        doc.target_doctype = {
            "sheet": "Tree Sheet",
            "branch": "Tree Node",
            "column": "Tree Column",
        }.get(data["scope"], "Tree Sheet")
        doc.target = data["target"]
        # event_types is a text column holding a JSON array (get_subscription parses
        # it back); store the encoded string so Frappe doesn't reject a raw list.
        doc.event_types = frappe.as_json(data.get("event_types") or [])
        doc.delivery = data["delivery"]
        doc.requires_ack = 1 if data.get("requires_ack") else 0
        doc.insert(ignore_permissions=True)
        return doc.name

    def delete_subscription(self, subscription: str) -> None:
        frappe.delete_doc(DT_SUBSCRIPTION, subscription, force=True, ignore_permissions=True)

    def get_subscription(self, subscription: str) -> dict[str, Any]:
        doc = frappe.get_doc(DT_SUBSCRIPTION, subscription)
        event_types = doc.event_types
        if isinstance(event_types, str):
            event_types = frappe.parse_json(event_types) if event_types else []
        sheet = doc.target if doc.scope == "sheet" else None
        if sheet is None:
            if doc.scope == "branch":
                sheet = frappe.db.get_value(DT_NODE, doc.target, "sheet")
            elif doc.scope == "column":
                sheet = frappe.db.get_value(DT_COLUMN, doc.target, "sheet")
        return {
            "name": doc.name,
            "subscriber": doc.subscriber,
            "subscriber_kind": doc.subscriber_kind,
            "scope": doc.scope,
            "target": doc.target,
            "event_types": event_types or [],
            "delivery": doc.delivery,
            "requires_ack": bool(doc.requires_ack),
            "sheet": sheet,
        }

    def create_acknowledgement(self, notification: str, user: str) -> str:
        # Acknowledging is idempotent: a repeat ack by the same user returns the
        # existing row rather than violating the (notification, user) uniqueness.
        existing = frappe.db.get_value(
            DT_ACK, {"notification": notification, "user": user}, "name"
        )
        if existing:
            return existing
        doc = frappe.new_doc(DT_ACK)
        doc.notification = notification
        doc.user = user
        doc.acked_at = frappe.utils.now()
        doc.insert(ignore_permissions=True)
        return doc.name

    def get_notification(self, notification: str) -> dict[str, Any]:
        doc = frappe.get_doc(DT_NOTIFICATION, notification)
        return {
            "name": doc.name,
            "tree_event": doc.tree_event,
            "change_request": doc.get("change_request"),
            "recipient": doc.recipient,
            "channel": doc.channel,
            "requires_ack": bool(doc.requires_ack),
        }

    def create_notification(self, data: dict[str, Any]) -> str:
        """Direct in-app Notification creation (sheet-less role fan-out). Only
        schema fields are persisted; the role ``op``/``role`` are recoverable
        from the linked Tree Event's payload (the renderer reads it)."""
        fields = {"doctype": DT_NOTIFICATION}
        for k in ("tree_event", "change_request", "recipient", "channel", "requires_ack"):
            if data.get(k) is not None:
                fields[k] = data[k]
        # Idempotent per (tree_event, recipient, channel).
        if fields.get("tree_event") and frappe.db.exists(
            DT_NOTIFICATION,
            {"tree_event": fields["tree_event"], "recipient": fields.get("recipient"), "channel": fields.get("channel", "in-app")},
        ):
            return frappe.db.get_value(
                DT_NOTIFICATION,
                {"tree_event": fields["tree_event"], "recipient": fields.get("recipient"), "channel": fields.get("channel", "in-app")},
                "name",
            )
        doc = frappe.get_doc(fields)
        doc.insert(ignore_permissions=True)
        return doc.name

    # ---- roles / role grants / role applications (Feature: roles) ---------
    def get_role(self, role: str) -> Optional[_RoleView]:
        if not frappe.db.exists(DT_ROLE, role):
            return None
        d = frappe.get_doc(DT_ROLE, role)
        return _RoleView(
            name=d.name, role=d.role, label=d.get("label") or d.role,
            applicable=bool(d.applicable), active=bool(d.active),
        )

    def list_active_role_grantees(self, role: str) -> list[str]:
        return sorted(
            frappe.get_all(
                DT_ROLE_GRANT, filters={"role": role, "active": 1}, pluck="grantee"
            )
        )

    def find_active_role_grant(self, role: str, grantee: str) -> Optional[_RoleGrantView]:
        name = frappe.db.get_value(
            DT_ROLE_GRANT, {"role": role, "grantee": grantee, "active": 1}, "name"
        )
        if not name:
            return None
        d = frappe.get_doc(DT_ROLE_GRANT, name)
        return _RoleGrantView(
            name=d.name, role=d.role, grantee=d.grantee, granted_by=d.granted_by,
            active=bool(d.active), source=d.get("source") or "admin-grant",
            granted_via=d.get("granted_via"),
        )

    def create_role_grant(
        self, role: str, grantee: str, granted_by: str,
        source: str = "admin-grant", granted_via: Optional[str] = None,
    ) -> str:
        doc = frappe.new_doc(DT_ROLE_GRANT)
        doc.role = role
        doc.grantee = grantee
        doc.granted_by = granted_by
        doc.source = source
        doc.granted_via = granted_via
        doc.active = 1
        doc.insert(ignore_permissions=True)
        return doc.name

    def deactivate_role_grant(self, role_grant: str) -> None:
        frappe.db.set_value(DT_ROLE_GRANT, role_grant, "active", 0)

    def create_role_application(self, data: dict[str, Any]) -> str:
        doc = frappe.new_doc(DT_ROLE_APP)
        doc.role = data["role"]
        doc.requester = data["requester"]
        doc.status = data.get("status", "proposed")
        doc.justification = data.get("justification")
        doc.insert(ignore_permissions=True)
        return doc.name

    def get_role_application(self, role_application: str) -> dict[str, Any]:
        d = frappe.get_doc(DT_ROLE_APP, role_application)
        return {
            "name": d.name,
            "role": d.role,
            "requester": d.requester,
            "status": d.status,
            "justification": d.get("justification"),
            "decided_by": d.get("decided_by"),
            "resulting_grant": d.get("resulting_grant"),
            "decided_event": d.get("decided_event"),
        }

    def update_role_application(self, role_application: str, patch: dict[str, Any]) -> None:
        doc = frappe.get_doc(DT_ROLE_APP, role_application)
        for k, v in (patch or {}).items():
            if k == "decided_by" and v:
                doc.decided_by = v
                if doc.meta.has_field("decided_at"):
                    doc.decided_at = frappe.utils.now()
            else:
                doc.set(k, v)
        doc.save(ignore_permissions=True)

    def find_open_role_application(self, role: str, requester: str) -> Optional[dict[str, Any]]:
        name = frappe.db.get_value(
            DT_ROLE_APP, {"role": role, "requester": requester, "status": "proposed"}, "name"
        )
        return self.get_role_application(name) if name else None

    def list_admins(self) -> list[str]:
        """Enabled users holding System Manager — the role-application recipients."""
        names = frappe.get_all(
            "Has Role",
            filters={"role": SYSTEM_MANAGER, "parenttype": "User"},
            distinct=True,
            pluck="parent",
        )
        return sorted(n for n in names if frappe.db.get_value("User", n, "enabled"))

    # ---- impersonation sessions (Area 1) ----------------------------------
    def create_impersonation_session(
        self, real_user: str, impersonated_user: str, reason: Optional[str] = None
    ) -> str:
        """Persist (and activate) an "act as" overlay for ``real_user`` acting as
        ``impersonated_user``. At most one active session per real_user: any prior
        active one is force-ended first (the handler contract). Returns the new
        session id. The row is the durable, auditable record of the window (there
        is NO Tree Event for begin/end — the session doctype IS the audit trail)."""
        self.end_impersonation(real_user)  # collapse any prior active overlay
        doc = frappe.new_doc(DT_IMPERSONATION)
        doc.real_user = real_user
        doc.impersonated_user = impersonated_user
        doc.active = 1
        doc.started_at = frappe.utils.now()
        doc.reason = reason
        doc.insert(ignore_permissions=True)
        return doc.name

    def get_active_impersonation(self, real_user: str) -> Optional[dict[str, Any]]:
        """The active overlay row for ``real_user`` (the single source of truth
        ``_actor()`` reads), or None. If more than one active row somehow exists,
        the most recent wins."""
        name = frappe.db.get_value(
            DT_IMPERSONATION,
            {"real_user": real_user, "active": 1},
            "name",
            order_by="creation desc",
        )
        if not name:
            return None
        d = frappe.get_doc(DT_IMPERSONATION, name)
        return {
            "name": d.name,
            "real_user": d.real_user,
            "impersonated_user": d.impersonated_user,
            "reason": d.get("reason"),
            "active": bool(d.active),
        }

    def end_impersonation(self, real_user: str) -> None:
        """Deactivate every active overlay for ``real_user`` (idempotent: a no-op
        when none is active). Stamps ``ended_at`` so the window is bounded in the
        audit trail."""
        for name in frappe.get_all(
            DT_IMPERSONATION,
            filters={"real_user": real_user, "active": 1},
            pluck="name",
        ):
            frappe.db.set_value(
                DT_IMPERSONATION,
                name,
                {"active": 0, "ended_at": frappe.utils.now()},
            )

    # ---- process / SLA (Area 3) -------------------------------------------
    def _process_view(self, doc) -> _ProcessView:
        """Build a ``ProcessView`` from an Arbor Process Document. Stage ``idx`` is
        0-based (child-row order), so the pure machine + the run-stage ledger agree
        with the bench-free fixture (which numbers stages 0,1,2…)."""
        stages = [
            _ProcessStageView(
                idx=i,
                column=row.column,
                sla_seconds=int(row.get("sla_seconds") or 0),
                notify_on_enter=bool(
                    1 if row.get("notify_on_enter") is None else row.get("notify_on_enter")
                ),
            )
            for i, row in enumerate(doc.get("stages") or [])
        ]
        return _ProcessView(
            name=doc.name,
            sheet=doc.sheet,
            title=doc.get("title") or "",
            enabled=bool(doc.enabled),
            row_scope=doc.get("row_scope") or "root-children",
            start_trigger=doc.get("start_trigger") or "node-created",
            sla_breach_notify=bool(doc.get("sla_breach_notify")),
            stages=stages,
        )

    def upsert_process(self, data: dict[str, Any]) -> str:
        """Create or replace the sheet's Arbor Process definition (+ ordered
        stages). Exactly one process per sheet: an existing one is updated in place
        (its ``enabled`` flag is preserved across a redefine)."""
        sheet = data["sheet"]
        existing = frappe.db.get_value(DT_PROCESS, {"sheet": sheet}, "name")
        doc = frappe.get_doc(DT_PROCESS, existing) if existing else frappe.new_doc(DT_PROCESS)
        doc.sheet = sheet
        if data.get("title") is not None:
            doc.title = data["title"]
        if data.get("row_scope"):
            doc.row_scope = data["row_scope"]
        if data.get("start_trigger"):
            doc.start_trigger = data["start_trigger"]
        if data.get("sla_breach_notify") is not None:
            doc.sla_breach_notify = 1 if data["sla_breach_notify"] else 0
        doc.set("stages", [])
        for st in data.get("stages") or []:
            doc.append(
                "stages",
                {
                    "column": st["column"],
                    "sla_seconds": int(st.get("sla_seconds") or 0),
                    "notify_on_enter": 1 if st.get("notify_on_enter", True) else 0,
                },
            )
        if existing:
            doc.save(ignore_permissions=True)
        else:
            doc.insert(ignore_permissions=True)
        return doc.name

    def get_process(self, sheet: str) -> Optional[_ProcessView]:
        name = frappe.db.get_value(DT_PROCESS, {"sheet": sheet}, "name")
        if not name:
            return None
        return self._process_view(frappe.get_doc(DT_PROCESS, name))

    def get_process_by_name(self, process: str) -> Optional[_ProcessView]:
        """The process by its own docname (the SLA sweep's ``process_of`` resolver
        maps a run's ``process`` link back to its definition)."""
        if not frappe.db.exists(DT_PROCESS, process):
            return None
        return self._process_view(frappe.get_doc(DT_PROCESS, process))

    def set_process_enabled(self, process: str, enabled: bool) -> None:
        frappe.db.set_value(DT_PROCESS, process, "enabled", 1 if enabled else 0)

    def list_in_scope_nodes(self, sheet: str, row_scope: str) -> list[str]:
        """Node ids that count as process 'rows' under ``row_scope``. Mirrors the
        in-memory double: ``all-nodes`` = every node; ``root-children`` (default) /
        ``depth`` = the direct children of a root (a node whose own parent is
        None)."""
        rows = frappe.get_all(
            DT_NODE, filters={"sheet": sheet}, fields=["name", "parent_tree_node"]
        )
        if row_scope == "all-nodes":
            return [r["name"] for r in rows]
        roots = {r["name"] for r in rows if not r.get("parent_tree_node")}
        return [r["name"] for r in rows if r.get("parent_tree_node") in roots]

    def _run_dict(self, doc) -> dict[str, Any]:
        return {
            "name": doc.name,
            "process": doc.arbor_process,
            "sheet": doc.sheet,
            "node": doc.node,
            "status": doc.status,
            "current_stage_idx": doc.current_stage_idx,
            "started_at": str(doc.started_at) if doc.started_at else None,
            "completed_at": str(doc.completed_at) if doc.completed_at else None,
            "stages": [
                {
                    "stage_idx": rs.stage_idx,
                    "column": rs.column,
                    "entered_at": str(rs.entered_at) if rs.entered_at else None,
                    "filled_at": str(rs.filled_at) if rs.filled_at else None,
                    "due_at": str(rs.due_at) if rs.due_at else None,
                    "breached": bool(rs.breached),
                    "breached_at": str(rs.breached_at) if rs.breached_at else None,
                    "notified_owner": rs.get("notified_owner") or "",
                }
                for rs in (doc.get("run_stages") or [])
            ],
        }

    def create_process_run(self, data: dict[str, Any]) -> str:
        """Create an Arbor Process Run (+ its per-stage ledger). The run's process
        link FIELD is ``arbor_process`` (NOT ``process``); the per-stage ledger is
        the ``run_stages`` child table."""
        doc = frappe.new_doc(DT_PROCESS_RUN)
        doc.arbor_process = data["process"]
        doc.sheet = data["sheet"]
        doc.node = data["node"]
        doc.status = data.get("status", "active")
        doc.current_stage_idx = data.get("current_stage_idx", 0)
        doc.started_at = data.get("started_at")
        doc.completed_at = data.get("completed_at")
        for st in data.get("stages") or []:
            doc.append("run_stages", self._run_stage_row(st))
        doc.insert(ignore_permissions=True)
        return doc.name

    @staticmethod
    def _resolve_due(due: Any) -> Any:
        """Resolve the pure machine's ``due_at`` into a real Datetime string.

        ``arbor.core.process.default_due_at`` returns a ``{base, add_seconds}``
        marker when ``entered_at`` is a (non-numeric) ISO string — because the pure
        module has no clock. The adapter DOES have one, so it resolves the marker
        to ``base + add_seconds`` here (Datetime column). A plain string/None passes
        through unchanged (idempotent on re-persist)."""
        if isinstance(due, dict) and "base" in due:
            return frappe.utils.add_to_date(
                due["base"], seconds=int(due.get("add_seconds") or 0)
            )
        return due

    def _run_stage_row(self, st: dict[str, Any]) -> dict[str, Any]:
        return {
            "stage_idx": st.get("stage_idx"),
            "column": st.get("column"),
            "entered_at": st.get("entered_at"),
            "filled_at": st.get("filled_at"),
            "due_at": self._resolve_due(st.get("due_at")),
            "breached": 1 if st.get("breached") else 0,
            "breached_at": st.get("breached_at"),
            "notified_owner": st.get("notified_owner") or "",
        }

    def get_process_run(self, process: str, node: str) -> Optional[dict[str, Any]]:
        name = frappe.db.get_value(
            DT_PROCESS_RUN, {"arbor_process": process, "node": node}, "name"
        )
        if not name:
            return None
        return self._run_dict(frappe.get_doc(DT_PROCESS_RUN, name))

    def update_process_run(self, run: str, patch: dict[str, Any]) -> None:
        doc = frappe.get_doc(DT_PROCESS_RUN, run)
        for k, v in (patch or {}).items():
            if k == "stages":
                doc.set("run_stages", [self._run_stage_row(st) for st in (v or [])])
            else:
                doc.set(k, v)
        doc.save(ignore_permissions=True)

    def list_process_runs(
        self, sheet: str, status: Optional[str] = None
    ) -> list[dict[str, Any]]:
        filters: dict[str, Any] = {"sheet": sheet}
        if status is not None:
            filters["status"] = status
        names = frappe.get_all(DT_PROCESS_RUN, filters=filters, pluck="name")
        return [self._run_dict(frappe.get_doc(DT_PROCESS_RUN, n)) for n in names]

    def list_active_runs_with_due(self, now: Any) -> list[dict[str, Any]]:
        """Active runs whose CURRENT stage has a ``due_at <= now`` and is not yet
        filled — the bounded SLA-sweep candidate set. Filtered in the DB to active
        runs, then narrowed to the current stage's ledger row (matching the
        in-memory double's semantics)."""
        names = frappe.get_all(
            DT_PROCESS_RUN, filters={"status": "active"}, pluck="name"
        )
        out: list[dict[str, Any]] = []
        for n in names:
            run = self._run_dict(frappe.get_doc(DT_PROCESS_RUN, n))
            cur_idx = run.get("current_stage_idx")
            for s in run.get("stages") or []:
                if (
                    s.get("stage_idx") == cur_idx
                    and s.get("due_at") is not None
                    and s.get("filled_at") is None
                ):
                    out.append(run)
                    break
        return out


class FrappeEventSink:
    """``EventSink`` that writes ``Tree Event`` rows. ``emit`` is the ONLY place
    a Tree Event is created (ARCHITECTURE §4.3 / DATA-MODEL §12); the
    notification + webhook dispatchers (other lanes) react to the resulting
    document. Returns the stored event with its assigned ``event_id`` (the
    Frappe doc ``name``) and ``timestamp``.
    """

    def emit(self, event: TreeEvent) -> TreeEvent:
        from dataclasses import replace

        actor_type = event.actor_type
        if isinstance(actor_type, ActorType):
            actor_type = actor_type.value

        doc = frappe.new_doc(DT_EVENT)
        doc.sheet = event.sheet
        doc.type = event.type
        doc.payload = event.payload or {}
        doc.actor = event.actor
        doc.actor_type = actor_type
        # Impersonation trace (Area 1): both NULL for a normal action, so a
        # non-impersonated event is byte-for-byte as before. Populated only when
        # the emitting Actor was under an "act as" overlay (real_user = the
        # authenticated admin, impersonated_as = the effective identity).
        doc.real_user = event.real_user
        doc.impersonated_as = event.impersonated_as
        doc.change_request = event.change_request
        doc.insert(ignore_permissions=True)  # append-only; not user-writable

        return replace(
            event,
            actor_type=actor_type,
            event_id=doc.name,
            timestamp=str(doc.creation),
        )
