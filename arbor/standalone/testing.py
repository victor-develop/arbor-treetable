"""SQL-backed test double for the standalone adapter.

``SQLTestRepository`` is ``SQLRepository`` made drop-in substitutable for
``core.testing.InMemoryRepository`` in the bench-free core suite: it owns its
own throwaway sqlite in-memory engine (one per instance — the same isolation a
fresh ``InMemoryRepository()`` gives), adds the reference's seeding helpers
(``add_sheet`` … ``seed_value``), and exposes the reference's plain-dict table
attributes (``values`` / ``versions`` / ``notifications`` / ...) as read-only
properties materialized per access, key/value shapes identical.

Used by ``tests/standalone`` to re-run the whole tests/core suite against the
SQL adapter with zero changes to the core tests. Nothing here imports frappe.
"""

from __future__ import annotations

from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from . import models as m
from .repo_collab import CommentRowView, _comment_view
from .repository import SQLRepository
from .views import SheetView


class SQLTestRepository(SQLRepository):
    """``SQLRepository`` over a private in-memory sqlite engine, with the
    in-memory reference's seeding helpers and dict-view attributes bolted on."""

    def __init__(self, url: str = "sqlite://") -> None:
        # StaticPool: sqlite's :memory: db lives on ONE connection; reuse it so
        # every session/query sees the same database for this instance.
        engine = sa.create_engine(url, poolclass=StaticPool)
        m.Base.metadata.create_all(engine)
        super().__init__(Session(engine))
        self.engine = engine

    # --- seeding helpers (signature-parity with InMemoryRepository) ---------
    def add_sheet(self, name: str, structural_owner: str, settings: dict | None = None) -> str:
        self.session.add(
            m.Sheet(
                name=name,
                title=name,
                structural_owner=structural_owner,
                status="active",
                settings=dict(settings or {}),
            )
        )
        self.session.flush()
        return name

    def add_column(
        self,
        name: str,
        sheet: str,
        fieldname: str,
        column_owner: str,
        editors: list[str] | None = None,
        is_label: bool = False,
        type: str = "text",
        read_level: str = "public",
        readers: list[str] | None = None,
    ) -> str:
        self.session.add(
            m.Column(
                name=name,
                sheet=sheet,
                field=fieldname,
                column_owner=column_owner,
                editors=list(editors or []),
                is_label=is_label,
                type=type,
                read_level=read_level,
                readers=list(readers or []),
            )
        )
        self.session.flush()
        return name

    def add_node(self, name: str, sheet: str, parent: Optional[str]) -> str:
        self.session.add(m.Node(name=name, sheet=sheet, parent=parent))
        self.session.flush()
        self._rebuild_nested_set(sheet)
        return name

    def add_grant(
        self, name: str, sheet: str, branch_root: str, grantee: str, granted_by: str
    ) -> str:
        self.session.add(
            m.BranchGrant(
                name=name,
                sheet=sheet,
                branch_root=branch_root,
                grantee=grantee,
                granted_by=granted_by,
                scope="structure",
                active=True,
            )
        )
        self.session.flush()
        return name

    def seed_value(self, sheet: str, node: str, column: str, value: Any) -> None:
        # Reference semantics: write the value with version pinned to 1 (a
        # reseed OVERWRITES and re-pins, it does not bump).
        row = self._value_row(node, column)
        if row is not None:
            row.value = value
            row.version = 1
        else:
            self.session.add(
                m.NodeValue(sheet=sheet, node=node, column=column, value=value, version=1)
            )
        self.session.flush()

    def add_role(self, role: str, label: str = "", applicable: bool = True, active: bool = True) -> str:
        self.session.add(
            m.Role(name=role, role=role, label=label or role, applicable=applicable, active=active)
        )
        self.session.flush()
        return role

    def add_admin(self, user: str) -> None:
        row = self.session.get(m.User, user)
        if row is None:
            self.session.add(m.User(email=user, is_admin=True, enabled=True))
        else:
            row.is_admin = True
        self.session.flush()

    def add_role_grant(
        self, role: str, grantee: str, granted_by: str = "system", source: str = "admin-grant"
    ) -> str:
        return self.create_role_grant(role, grantee, granted_by, source=source)

    def add_notification(self, name: str, recipient: str, **extra: Any) -> str:
        row = m.Notification(name=name, recipient=recipient)
        leftovers: dict[str, Any] = {}
        for k, v in extra.items():
            if k in self._NOTIFICATION_FIELDS:
                setattr(row, k, v)
            else:
                leftovers[k] = v
        if leftovers:
            row.extra = leftovers
        self.session.add(row)
        self.session.flush()
        return name

    # --- dict views (the reference's plain-dict table attributes) -----------
    # Read-only, materialized per access; key/value shapes match the tests'
    # direct accesses (``repo.versions[(node, col)]``, ``repo.sheets == {}``,
    # ``repo.notifications.values()``, ...).
    @property
    def sheets(self) -> dict[str, SheetView]:
        return {
            r.name: self._sheet_view(r) for r in self.session.scalars(sa.select(m.Sheet)).all()
        }

    @property
    def values(self) -> dict[tuple[str, str], Any]:
        return {
            (r.node, r.column): r.value
            for r in self.session.scalars(sa.select(m.NodeValue)).all()
        }

    @property
    def versions(self) -> dict[tuple[str, str], int]:
        return {
            (r.node, r.column): int(r.version or 0)
            for r in self.session.scalars(sa.select(m.NodeValue)).all()
        }

    @property
    def notifications(self) -> dict[str, dict[str, Any]]:
        rows = self.session.scalars(
            sa.select(m.Notification).order_by(m.Notification.creation, m.Notification.name)
        ).all()
        return {r.name: self.get_notification(r.name) for r in rows}

    @property
    def change_requests(self) -> dict[str, dict[str, Any]]:
        rows = self.session.scalars(
            sa.select(m.ChangeRequest).order_by(m.ChangeRequest.creation, m.ChangeRequest.name)
        ).all()
        return {r.name: self.get_change_request(r.name) for r in rows}

    @property
    def comments(self) -> dict[str, CommentRowView]:
        rows = self.session.scalars(
            sa.select(m.CellComment).order_by(m.CellComment.creation, m.CellComment.name)
        ).all()
        return {r.name: _comment_view(r) for r in rows}

    @property
    def process_runs(self) -> dict[str, dict[str, Any]]:
        rows = self.session.scalars(
            sa.select(m.ProcessRun).order_by(m.ProcessRun.creation, m.ProcessRun.name)
        ).all()
        return {r.name: self._run_dict(r) for r in rows}
