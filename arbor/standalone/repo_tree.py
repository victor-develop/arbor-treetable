"""TreeRepoMixin — sheets / columns / nodes / cell values over SQLAlchemy.

The tree-table slice of the standalone ``Repository`` (``arbor.core.ports``),
mixed into the composing repository class alongside the governance / collab /
process mixins. Assumes ``self.session`` is a SQLAlchemy ``Session`` supplied
by the composer; mutators ``flush()`` (so ids and lft/rgt are visible within
the request) and leave commit/rollback to the caller.

Behavioral reference: ``core.testing.InMemoryRepository`` (the executable
contract) with the frappe adapter's concurrency guards layered on top:

- NestedSet lft/rgt are maintained by the SAME full-sheet DFS rebuild the
  in-memory double uses, with sibling order = insertion order (``creation``,
  then ``name`` as the tiebreak) — a moved node keeps its original insertion
  position among its new siblings, exactly as the dict-ordered double does.
- ``set_value`` bumps the per-cell version counter and enforces the optional
  ``expected_version`` guard (``StaleVersionError`` on mismatch, API-161).
- ``move_node`` raises ``CycleError`` for self/descendant targets (API-150)
  and ``StaleMoveError`` for a vanished positional anchor (API-160).
- ``create_sheet`` mirrors the createSheet capability: the CREATOR becomes
  ``structural_owner`` + owner of the default LABEL column.

Reads raise ``NotFoundError`` (a ``KeyError``) on a miss, so the core's
existing ``except KeyError`` guards behave as with the in-memory double and
the API seam can map 404.
"""

from __future__ import annotations

from typing import Any, Optional

import sqlalchemy as sa

from . import models as m
from .errors import ConflictError, CycleError, NotFoundError, StaleMoveError, StaleVersionError
from .views import ColumnView, NodeView, SheetView


class TreeRepoMixin:
    """Sheets / columns / nodes / values slice of the standalone Repository."""

    # ---- ORM row fetch helpers (miss -> NotFoundError, KeyError-compatible) -
    def _sheet_row(self, sheet: str) -> m.Sheet:
        row = self.session.get(m.Sheet, sheet)
        if row is None:
            raise NotFoundError(f"No sheet {sheet!r}")
        return row

    def _node_row(self, node: str) -> m.Node:
        row = self.session.get(m.Node, node)
        if row is None:
            raise NotFoundError(f"No node {node!r}")
        return row

    def _column_row(self, sheet: str, column: str) -> m.Column:
        """Resolve by Column ``name`` first, else by ``(sheet, field)`` — the
        same two-step lookup as the frappe adapter and the in-memory double."""
        row = self.session.get(m.Column, column)
        if row is not None:
            return row
        row = self.session.scalars(
            sa.select(m.Column).where(m.Column.sheet == sheet, m.Column.field == column)
        ).first()
        if row is None:
            raise NotFoundError(f"No column {column!r} in sheet {sheet!r}")
        return row

    def _value_row(self, node: str, column: str) -> Optional[m.NodeValue]:
        return self.session.scalars(
            sa.select(m.NodeValue).where(m.NodeValue.node == node, m.NodeValue.column == column)
        ).first()

    # ---- view builders ------------------------------------------------------
    def _sheet_view(self, row: m.Sheet) -> SheetView:
        return SheetView(
            name=row.name,
            structural_owner=row.structural_owner,
            settings=dict(row.settings or {}),
            title=row.title or "",
        )

    def _node_view(self, row: m.Node) -> NodeView:
        return NodeView(
            name=row.name,
            sheet=row.sheet,
            parent=row.parent or None,
            lft=int(row.lft or 0),
            rgt=int(row.rgt or 0),
        )

    def _column_view(self, row: m.Column) -> ColumnView:
        return ColumnView(
            name=row.name,
            sheet=row.sheet,
            field=row.field,
            column_owner=row.column_owner or "",
            editors=list(row.editors or []),
            is_label=bool(row.is_label),
            label=row.label or row.field,
            type=row.type or "text",
            options=row.options,
            # Coalesce legacy rows (no read_level) to 'public'.
            read_level=row.read_level or "public",
            readers=list(row.readers or []),
        )

    # ---- NestedSet maintenance ----------------------------------------------
    def _rebuild_nested_set(self, sheet: str) -> None:
        """Assign lft/rgt by a DFS over parent links (preorder) — the identical
        algorithm to ``InMemoryRepository._rebuild_nested_set``, run over the
        sheet's rows in insertion order (``creation`` asc, ``name`` tiebreak;
        the SQL analog of the double's dict order). O(sheet) per mutation, same
        as the double — fine for the standalone lane's sheet sizes (the
        snapshot layer already guards >500 nodes)."""
        rows = list(self.session.scalars(sa.select(m.Node).where(m.Node.sheet == sheet)).all())
        rows.sort(key=lambda n: (n.creation, n.name))
        children: dict[Optional[str], list[m.Node]] = {}
        for n in rows:
            children.setdefault(n.parent or None, []).append(n)
        counter = iter(range(1, 2 * len(rows) + 1))

        def visit(node: m.Node) -> None:
            node.lft = next(counter)
            for child in children.get(node.name, []):
                visit(child)
            node.rgt = next(counter)

        for root in children.get(None, []):
            visit(root)
        self.session.flush()

    # ---- Repository protocol: sheets / columns ------------------------------
    def get_sheet(self, sheet: str) -> SheetView:
        return self._sheet_view(self._sheet_row(sheet))

    def create_sheet(
        self,
        actor: Any,
        title: str,
        name: Optional[str] = None,
        label_column: Optional[str] = None,
    ) -> dict[str, Any]:
        """Self-service sheet bootstrap (createSheet capability). The CREATOR
        (``actor.user``) becomes ``structural_owner`` and owns the default
        LABEL column (field ``title``, label text ``label_column`` or "Item"),
        so their very first snapshot already grants structure/column
        affordances. Unlike frappe there is no autoname+rename dance — the
        requested ``name`` simply becomes the PK. A duplicate ``name`` raises
        ``ConflictError`` (409); a blank ``title`` with no ``name`` is a
        ``ValueError`` (400)."""
        title = (title or "").strip() if isinstance(title, str) else ""
        req_name = (name or "").strip() if isinstance(name, str) else (str(name).strip() if name else "")
        if not title and not req_name:
            raise ValueError("Sheet title is required")
        if req_name and self.session.get(m.Sheet, req_name) is not None:
            raise ConflictError(f"Sheet {req_name} already exists")

        label_text = (label_column or "").strip() if isinstance(label_column, str) else ""
        sheet = req_name or m.new_id()
        self.session.add(
            m.Sheet(
                name=sheet,
                title=title or req_name,
                structural_owner=actor.user,
                status="active",
                settings={},
            )
        )
        self.session.add(
            m.Column(
                name=m.new_id(),
                sheet=sheet,
                field="title",
                label=label_text or "Item",
                type="text",
                is_label=True,
                editable=True,
                read_level="public",
                column_owner=actor.user,
                editors=[],
                readers=[],
                idx=0,
            )
        )
        self.session.flush()
        return {"sheet": sheet}

    def get_column(self, sheet: str, column: str) -> ColumnView:
        return self._column_view(self._column_row(sheet, column))

    def get_column_by_name(self, column: str) -> ColumnView:
        row = self.session.get(m.Column, column)
        if row is None:
            raise NotFoundError(f"No column {column!r}")
        return self._column_view(row)

    def list_columns(self, sheet: str) -> list[ColumnView]:
        rows = self.session.scalars(
            sa.select(m.Column)
            .where(m.Column.sheet == sheet)
            .order_by(m.Column.idx.asc(), m.Column.creation.asc(), m.Column.name.asc())
        ).all()
        return [self._column_view(r) for r in rows]

    # ---- Repository protocol: nodes (NestedSet) ------------------------------
    def get_node(self, node: str) -> NodeView:
        return self._node_view(self._node_row(node))

    def list_nodes(self, sheet: str) -> list[NodeView]:
        rows = self.session.scalars(
            sa.select(m.Node).where(m.Node.sheet == sheet).order_by(m.Node.lft.asc())
        ).all()
        return [self._node_view(r) for r in rows]

    def count_nodes(self, sheet: str) -> int:
        """Total node count for ``sheet`` — one ``COUNT(*)``, so the snapshot
        size guard never materializes rows."""
        return int(
            self.session.scalar(
                sa.select(sa.func.count()).select_from(m.Node).where(m.Node.sheet == sheet)
            )
            or 0
        )

    def ancestors_self(self, node: str) -> list[NodeView]:
        """[node, parent, ..., root] — nearest-first (DATA-MODEL §3 walk):
        ``WHERE sheet=? AND lft<=n.lft AND rgt>=n.rgt ORDER BY lft DESC``."""
        n = self._node_row(node)
        rows = self.session.scalars(
            sa.select(m.Node)
            .where(m.Node.sheet == n.sheet, m.Node.lft <= n.lft, m.Node.rgt >= n.rgt)
            .order_by(m.Node.lft.desc())  # nearest (deepest) ancestor first
        ).all()
        return [self._node_view(r) for r in rows]

    def descendants(self, node: str) -> list[NodeView]:
        """Strict descendants via the NestedSet range (DATA-MODEL §3), lft asc
        (shallow->deep preorder)."""
        n = self._node_row(node)
        rows = self.session.scalars(
            sa.select(m.Node)
            .where(m.Node.sheet == n.sheet, m.Node.lft > n.lft, m.Node.rgt < n.rgt)
            .order_by(m.Node.lft.asc())
        ).all()
        return [self._node_view(r) for r in rows]

    # ---- Repository protocol: mutators ---------------------------------------
    def create_node(self, sheet: str, parent: Optional[str], after: Optional[str] = None) -> str:
        """Insert a node and renumber the sheet's NestedSet. ``after`` is
        accepted for signature parity but does not reorder siblings (the
        in-memory reference appends new nodes last among siblings; insertion
        order == ``creation`` order here does the same)."""
        name = m.new_id()
        self.session.add(m.Node(name=name, sheet=sheet, parent=parent or None, creation=m.utcnow()))
        self.session.flush()
        self._rebuild_nested_set(sheet)
        return name

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
        and it does not match the stored counter, raise ``StaleVersionError``
        carrying the authoritative current version/value. A missing cell counts
        as version 0 (so ``expected_version=0`` allows the first write)."""
        row = self._value_row(node, column)
        if row is not None:
            if expected_version is not None and int(row.version or 0) != int(expected_version):
                raise StaleVersionError(
                    f"cell {node}/{column} is at version {row.version}, "
                    f"expected {expected_version}",
                    current_version=int(row.version or 0),
                    current_value=row.value,
                )
            row.value = value
            row.version = int(row.version or 0) + 1
            self.session.flush()
            return int(row.version)
        if expected_version is not None and int(expected_version) != 0:
            raise StaleVersionError(
                f"cell {node}/{column} does not exist; expected version {expected_version}",
                current_version=0,
                current_value=None,
            )
        self.session.add(
            m.NodeValue(name=m.new_id(), sheet=sheet, node=node, column=column, value=value, version=1)
        )
        self.session.flush()
        return 1

    def get_value(self, node: str, column: str) -> Any:
        row = self._value_row(node, column)
        return row.value if row is not None else None

    def get_value_version(self, node: str, column: str) -> Optional[int]:
        """The stored version counter for a cell, or None if the cell has never
        been written (the api layer's base_version read)."""
        row = self._value_row(node, column)
        return int(row.version or 0) if row is not None else None

    def move_node(
        self,
        node: str,
        new_parent: Optional[str],
        after: Optional[str] = None,
        expected_revision: Optional[Any] = None,
    ) -> None:
        """Re-parent a node and renumber. Raises ``CycleError`` when the move
        would put the node under itself/its own descendant (API-150) and
        ``StaleMoveError`` when the caller's positional anchor (``after`` +
        ``expected_revision``) no longer exists (API-160)."""
        n = self._node_row(node)
        if new_parent:
            # Reject moving a node under itself or its own descendant, using the
            # CURRENT lft/rgt interval (maintained by every prior rebuild).
            if new_parent == node:
                raise CycleError(f"cannot move {node} under itself")
            dest = self._node_row(new_parent)
            if int(n.lft or 0) <= int(dest.lft or 0) and int(dest.rgt or 0) <= int(n.rgt or 0):
                raise CycleError(f"cannot move {node} under its own descendant {new_parent}")
        if (
            after is not None
            and expected_revision is not None
            and self.session.get(m.Node, after) is None
        ):
            raise StaleMoveError(f"sibling {after!r} no longer exists; client revision is stale")
        n.parent = new_parent or None
        self.session.flush()
        self._rebuild_nested_set(n.sheet)

    def delete_node(self, node: str, cascade: bool = True) -> list[str]:
        """Delete a node (+ its subtree when ``cascade``), sweep the deleted
        nodes' cell values, renumber; return the deleted ids ([root, then
        descendants lft-asc], same order as both reference adapters)."""
        n = self._node_row(node)
        sheet = n.sheet
        deleted = [node]
        if cascade:
            deleted += [d.name for d in self.descendants(node)]
        self.session.execute(sa.delete(m.NodeValue).where(m.NodeValue.node.in_(deleted)))
        self.session.execute(sa.delete(m.Node).where(m.Node.name.in_(deleted)))
        self.session.flush()
        self._rebuild_nested_set(sheet)
        return deleted

    def create_column(self, sheet: str, spec: dict[str, Any]) -> str:
        """Insert a Tree Column from the addColumn spec; appended last in
        presentation order (idx = current max + 1, the frappe autoset analog)."""
        next_idx = self.session.scalar(
            sa.select(sa.func.coalesce(sa.func.max(m.Column.idx), 0)).where(m.Column.sheet == sheet)
        )
        name = m.new_id()
        self.session.add(
            m.Column(
                name=name,
                sheet=sheet,
                field=spec["field"],
                label=spec.get("label") or spec["field"],
                type=spec.get("type", "text"),
                options=spec.get("options"),
                column_owner=spec.get("column_owner") or "",
                editors=list(spec.get("editors") or []),
                is_label=bool(spec.get("is_label", False)),
                editable=True,
                read_level=spec.get("read_level") or "public",
                readers=list(spec.get("readers") or []),
                idx=int(next_idx or 0) + 1,
            )
        )
        self.session.flush()
        return name

    def update_column(self, sheet: str, column: str, patch: dict[str, Any]) -> None:
        """Patch column config. Scalar keys are allow-listed (same set the
        frappe adapter accepts); ``editors``/``readers`` REPLACE the JSON lists
        wholesale (reassigned, not mutated in place, so SQLAlchemy sees the
        change)."""
        row = self._column_row(sheet, column)
        patch = dict(patch or {})
        editors = patch.pop("editors", None)
        readers = patch.pop("readers", None)
        for k, v in patch.items():
            if k in {"label", "type", "options", "width", "editable", "is_label", "read_level"}:
                setattr(row, k, v)
        if editors is not None:
            row.editors = list(editors)
        if readers is not None:
            row.readers = list(readers)
        self.session.flush()

    def delete_column(self, sheet: str, column: str) -> None:
        """Delete a column and sweep its cell values (scoped to the column's
        id, mirroring the frappe adapter's value-first delete)."""
        row = self._column_row(sheet, column)
        self.session.execute(sa.delete(m.NodeValue).where(m.NodeValue.column == row.name))
        self.session.delete(row)
        self.session.flush()
