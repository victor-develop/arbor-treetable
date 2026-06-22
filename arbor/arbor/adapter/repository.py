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
        doc.change_request = event.change_request
        doc.insert(ignore_permissions=True)  # append-only; not user-writable

        return replace(
            event,
            actor_type=actor_type,
            event_id=doc.name,
            timestamp=str(doc.creation),
        )
