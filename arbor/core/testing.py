"""Pure in-memory test doubles for the core seams.

Used by the bench-free core tests AND by downstream lanes that want to exercise
``execute_action`` without a Frappe site. NestedSet semantics (lft/rgt) are
emulated so the ACL ancestor/descendant walks behave exactly as the adapter's.

Nothing here imports frappe.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Optional

from .ports import LLMProvider
from .types import StaleVersionError, TreeEvent


# ---------------------------------------------------------------------------
# View objects (duck-typed to the *View protocols in ports.py)
# ---------------------------------------------------------------------------
@dataclass
class _Sheet:
    name: str
    structural_owner: str
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Node:
    name: str
    sheet: str
    parent: Optional[str]
    lft: int = 0
    rgt: int = 0


@dataclass
class _Column:
    name: str
    sheet: str
    field: str
    column_owner: str
    editors: list[str] = field(default_factory=list)
    is_label: bool = False
    label: str = ""
    type: str = "text"
    options: Optional[dict] = None
    read_level: str = "public"
    readers: list[str] = field(default_factory=list)


@dataclass
class _Grant:
    name: str
    sheet: str
    branch_root: str
    grantee: str
    granted_by: str
    scope: str = "structure"
    active: bool = True


@dataclass
class _Role:
    name: str
    role: str
    label: str = ""
    applicable: bool = True
    active: bool = True


@dataclass
class _RoleGrant:
    name: str
    role: str
    grantee: str
    granted_by: str
    active: bool = True
    source: str = "admin-grant"
    granted_via: Optional[str] = None


@dataclass
class _ProcessStage:
    idx: int
    column: str
    sla_seconds: int = 0
    notify_on_enter: bool = True


@dataclass
class _Process:
    name: str
    sheet: str
    title: str = ""
    enabled: bool = False
    row_scope: str = "root-children"
    start_trigger: str = "node-created"
    sla_breach_notify: bool = True
    stages: list[_ProcessStage] = field(default_factory=list)


class InMemoryRepository:
    """A pure Python ``Repository`` with emulated NestedSet bookkeeping."""

    def __init__(self) -> None:
        self.sheets: dict[str, _Sheet] = {}
        self.nodes: dict[str, _Node] = {}
        self.columns: dict[str, _Column] = {}
        self.values: dict[tuple[str, str], Any] = {}
        self.versions: dict[tuple[str, str], int] = {}
        self.grants: dict[str, _Grant] = {}
        self.change_requests: dict[str, dict[str, Any]] = {}
        self.subscriptions: dict[str, dict[str, Any]] = {}
        self.notifications: dict[str, dict[str, Any]] = {}
        self.acknowledgements: dict[str, dict[str, Any]] = {}
        # roles (Feature: role management)
        self.roles: dict[str, _Role] = {}
        self.role_grants: dict[str, _RoleGrant] = {}
        self.role_applications: dict[str, dict[str, Any]] = {}
        self.admins: set[str] = set()
        # impersonation sessions (Area 1)
        self.impersonation_sessions: dict[str, dict[str, Any]] = {}
        # process / SLA (Area 3)
        self.processes: dict[str, _Process] = {}  # keyed by process name
        self.process_runs: dict[str, dict[str, Any]] = {}  # keyed by run name
        self._ids = itertools.count(1)

    def _id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._ids)}"

    # --- seeding helpers (used by the canonical fixture) ---
    def add_sheet(self, name: str, structural_owner: str, settings: dict | None = None) -> str:
        self.sheets[name] = _Sheet(name, structural_owner, settings or {})
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
        self.columns[name] = _Column(
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
        return name

    def add_node(self, name: str, sheet: str, parent: Optional[str]) -> str:
        self.nodes[name] = _Node(name=name, sheet=sheet, parent=parent)
        self._rebuild_nested_set(sheet)
        return name

    def add_grant(
        self, name: str, sheet: str, branch_root: str, grantee: str, granted_by: str
    ) -> str:
        self.grants[name] = _Grant(name, sheet, branch_root, grantee, granted_by)
        return name

    def seed_value(self, sheet: str, node: str, column: str, value: Any) -> None:
        self.values[(node, column)] = value
        self.versions[(node, column)] = 1

    def add_role(self, role: str, label: str = "", applicable: bool = True, active: bool = True) -> str:
        self.roles[role] = _Role(name=role, role=role, label=label or role, applicable=applicable, active=active)
        return role

    def add_admin(self, user: str) -> None:
        self.admins.add(user)

    def add_role_grant(self, role: str, grantee: str, granted_by: str = "system", source: str = "admin-grant") -> str:
        name = self._id("rgrant")
        self.role_grants[name] = _RoleGrant(name=name, role=role, grantee=grantee, granted_by=granted_by, source=source)
        return name

    # --- NestedSet emulation ---
    def _rebuild_nested_set(self, sheet: str) -> None:
        """Assign lft/rgt by a DFS over parent links (preorder)."""
        children: dict[Optional[str], list[str]] = {}
        for n in self.nodes.values():
            if n.sheet != sheet:
                continue
            children.setdefault(n.parent, []).append(n.name)
        counter = itertools.count(1)

        def visit(name: str) -> None:
            node = self.nodes[name]
            node.lft = next(counter)
            for child in children.get(name, []):
                visit(child)
            node.rgt = next(counter)

        for root in children.get(None, []):
            visit(root)

    # --- Repository protocol: reads ---
    def get_sheet(self, sheet: str) -> _Sheet:
        return self.sheets[sheet]

    def get_column(self, sheet: str, column: str) -> _Column:
        if column in self.columns:
            return self.columns[column]
        # allow lookup by field key within a sheet
        for c in self.columns.values():
            if c.sheet == sheet and c.field == column:
                return c
        raise KeyError(f"no column {column!r} in {sheet!r}")

    def get_column_by_name(self, column: str) -> _Column:
        return self.columns[column]

    def list_columns(self, sheet: str) -> list[_Column]:
        return [c for c in self.columns.values() if c.sheet == sheet]

    def get_node(self, node: str) -> _Node:
        return self.nodes[node]

    def list_nodes(self, sheet: str) -> list[_Node]:
        return [n for n in self.nodes.values() if n.sheet == sheet]

    def count_nodes(self, sheet: str) -> int:
        return sum(1 for n in self.nodes.values() if n.sheet == sheet)

    def ancestors_self(self, node: str) -> list[_Node]:
        n = self.nodes[node]
        anc = [
            m
            for m in self.nodes.values()
            if m.sheet == n.sheet and m.lft <= n.lft and m.rgt >= n.rgt
        ]
        anc.sort(key=lambda m: m.lft, reverse=True)  # nearest (deepest) first
        return anc

    def descendants(self, node: str) -> list[_Node]:
        n = self.nodes[node]
        return [
            m
            for m in self.nodes.values()
            if m.sheet == n.sheet and m.lft > n.lft and m.rgt < n.rgt
        ]

    def find_active_branch_grant(
        self, sheet: str, branch_root: str, scope: str = "structure"
    ) -> Optional[_Grant]:
        for g in self.grants.values():
            if (
                g.sheet == sheet
                and g.branch_root == branch_root
                and g.scope == scope
                and g.active
            ):
                return g
        return None

    def get_branch_grant(self, branch_grant: str) -> Optional[_Grant]:
        return self.grants.get(branch_grant)

    def get_value(self, node: str, column: str) -> Any:
        return self.values.get((node, column))

    # --- Repository protocol: mutators ---
    def create_node(self, sheet: str, parent: Optional[str], after: Optional[str] = None) -> str:
        name = self._id("node")
        self.nodes[name] = _Node(name=name, sheet=sheet, parent=parent)
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
        key = (node, column)
        current = self.versions.get(key, 0)
        if expected_version is not None and expected_version != current:
            raise StaleVersionError(
                f"stale version for {key!r}: expected {expected_version}, have {current}",
                current_version=current,
                current_value=self.values.get(key),
            )
        self.versions[key] = current + 1
        self.values[key] = value
        return self.versions[key]

    def move_node(
        self,
        node: str,
        new_parent: Optional[str],
        after: Optional[str] = None,
        expected_revision: Optional[Any] = None,
    ) -> None:
        n = self.nodes[node]
        n.parent = new_parent
        self._rebuild_nested_set(n.sheet)

    def delete_node(self, node: str, cascade: bool = True) -> list[str]:
        n = self.nodes[node]
        to_delete = [node]
        if cascade:
            to_delete += [d.name for d in self.descendants(node)]
        sheet = n.sheet
        for name in to_delete:
            self.nodes.pop(name, None)
            for key in [k for k in self.values if k[0] == name]:
                self.values.pop(key, None)
                self.versions.pop(key, None)
        self._rebuild_nested_set(sheet)
        return to_delete

    def create_column(self, sheet: str, spec: dict[str, Any]) -> str:
        name = self._id("col")
        self.columns[name] = _Column(
            name=name,
            sheet=sheet,
            field=spec["field"],
            column_owner=spec.get("column_owner", ""),
            editors=list(spec.get("editors") or []),
            is_label=spec.get("is_label", False),
            type=spec.get("type", "text"),
            options=spec.get("options"),
            read_level=spec.get("read_level", "public"),
            readers=list(spec.get("readers") or []),
        )
        return name

    def update_column(self, sheet: str, column: str, patch: dict[str, Any]) -> None:
        c = self.get_column(sheet, column)
        for k, v in patch.items():
            if hasattr(c, k):
                setattr(c, k, v)

    def delete_column(self, sheet: str, column: str) -> None:
        c = self.get_column(sheet, column)
        self.columns.pop(c.name, None)

    def create_branch_grant(
        self, sheet: str, branch_root: str, grantee: str, granted_by: str
    ) -> str:
        name = self._id("grant")
        self.grants[name] = _Grant(name, sheet, branch_root, grantee, granted_by)
        return name

    def deactivate_branch_grant(self, branch_grant: str) -> None:
        self.grants[branch_grant].active = False

    def set_column_authority(
        self,
        sheet: str,
        column: str,
        column_owner: Optional[str] = None,
        editors: Optional[list[str]] = None,
    ) -> None:
        c = self.get_column(sheet, column)
        if column_owner is not None:
            c.column_owner = column_owner
        if editors is not None:
            c.editors = list(editors)

    # --- change requests ---
    def create_change_request(self, data: dict[str, Any]) -> str:
        name = self._id("cr")
        self.change_requests[name] = {"name": name, **data}
        return name

    def get_change_request(self, change_request: str) -> dict[str, Any]:
        return self.change_requests[change_request]

    def update_change_request(self, change_request: str, patch: dict[str, Any]) -> None:
        self.change_requests[change_request].update(patch)

    # --- subscriptions / notifications / acks ---
    def create_subscription(self, data: dict[str, Any]) -> str:
        name = self._id("sub")
        self.subscriptions[name] = {"name": name, **data}
        return name

    def delete_subscription(self, subscription: str) -> None:
        self.subscriptions.pop(subscription, None)

    def get_subscription(self, subscription: str) -> dict[str, Any]:
        return self.subscriptions[subscription]

    def add_notification(self, name: str, recipient: str, **extra: Any) -> str:
        self.notifications[name] = {"name": name, "recipient": recipient, **extra}
        return name

    def create_notification(self, data: dict[str, Any]) -> str:
        name = self._id("notif")
        self.notifications[name] = {"name": name, **data}
        return name

    def get_notification(self, notification: str) -> dict[str, Any]:
        return self.notifications[notification]

    def create_acknowledgement(self, notification: str, user: str) -> str:
        name = self._id("ack")
        self.acknowledgements[name] = {
            "name": name,
            "notification": notification,
            "user": user,
        }
        return name

    # --- roles / role grants / role applications (Feature: role management) ---
    def get_role(self, role: str) -> Optional[_Role]:
        return self.roles.get(role)

    def list_active_role_grantees(self, role: str) -> list[str]:
        return sorted(
            g.grantee for g in self.role_grants.values() if g.role == role and g.active
        )

    def find_active_role_grant(self, role: str, grantee: str) -> Optional[_RoleGrant]:
        for g in self.role_grants.values():
            if g.role == role and g.grantee == grantee and g.active:
                return g
        return None

    def create_role_grant(
        self,
        role: str,
        grantee: str,
        granted_by: str,
        source: str = "admin-grant",
        granted_via: Optional[str] = None,
    ) -> str:
        name = self._id("rgrant")
        self.role_grants[name] = _RoleGrant(
            name=name, role=role, grantee=grantee, granted_by=granted_by,
            source=source, granted_via=granted_via,
        )
        return name

    def deactivate_role_grant(self, role_grant: str) -> None:
        self.role_grants[role_grant].active = False

    def create_role_application(self, data: dict[str, Any]) -> str:
        name = self._id("rapp")
        self.role_applications[name] = {"name": name, **data}
        return name

    def get_role_application(self, role_application: str) -> dict[str, Any]:
        return self.role_applications[role_application]

    def update_role_application(self, role_application: str, patch: dict[str, Any]) -> None:
        self.role_applications[role_application].update(patch)

    def find_open_role_application(self, role: str, requester: str) -> Optional[dict[str, Any]]:
        for app in self.role_applications.values():
            if app["role"] == role and app["requester"] == requester and app["status"] == "proposed":
                return app
        return None

    def list_admins(self) -> list[str]:
        return sorted(self.admins)

    # --- impersonation sessions (Area 1) ---
    def create_impersonation_session(
        self, real_user: str, impersonated_user: str, reason: Optional[str] = None
    ) -> str:
        # at most one active session per real_user: end any prior one first.
        self.end_impersonation(real_user)
        name = self._id("imp")
        self.impersonation_sessions[name] = {
            "name": name,
            "real_user": real_user,
            "impersonated_user": impersonated_user,
            "reason": reason,
            "active": True,
        }
        return name

    def get_active_impersonation(self, real_user: str) -> Optional[dict[str, Any]]:
        for s in self.impersonation_sessions.values():
            if s["real_user"] == real_user and s["active"]:
                return dict(s)
        return None

    def end_impersonation(self, real_user: str) -> None:
        for s in self.impersonation_sessions.values():
            if s["real_user"] == real_user and s["active"]:
                s["active"] = False

    # --- process / SLA (Area 3) ---
    def upsert_process(self, data: dict[str, Any]) -> str:
        sheet = data["sheet"]
        # replace any existing process for the sheet (one process per sheet).
        existing = self.get_process(sheet)
        name = existing.name if existing else self._id("proc")
        enabled = existing.enabled if existing else False
        self.processes[name] = _Process(
            name=name,
            sheet=sheet,
            title=data.get("title", ""),
            enabled=enabled,
            row_scope=data.get("row_scope", "root-children"),
            start_trigger=data.get("start_trigger", "node-created"),
            sla_breach_notify=data.get("sla_breach_notify", True),
            stages=[
                _ProcessStage(
                    idx=st.get("idx", i),
                    column=st["column"],
                    sla_seconds=int(st.get("sla_seconds") or 0),
                    notify_on_enter=st.get("notify_on_enter", True),
                )
                for i, st in enumerate(data.get("stages") or [])
            ],
        )
        return name

    def get_process(self, sheet: str) -> Optional[_Process]:
        for p in self.processes.values():
            if p.sheet == sheet:
                return p
        return None

    def set_process_enabled(self, process: str, enabled: bool) -> None:
        self.processes[process].enabled = enabled

    def list_in_scope_nodes(self, sheet: str, row_scope: str) -> list[str]:
        nodes = [n for n in self.nodes.values() if n.sheet == sheet]
        if row_scope == "all-nodes":
            return [n.name for n in nodes]
        # 'root-children' (default) + 'depth' (treated as root-children here):
        # direct children of a root (parent whose own parent is None).
        roots = {n.name for n in nodes if n.parent is None}
        return [n.name for n in nodes if n.parent in roots]

    def create_process_run(self, data: dict[str, Any]) -> str:
        name = self._id("run")
        self.process_runs[name] = {"name": name, **data}
        return name

    def get_process_run(self, process: str, node: str) -> Optional[dict[str, Any]]:
        for r in self.process_runs.values():
            if r["process"] == process and r["node"] == node:
                return r
        return None

    def update_process_run(self, run: str, patch: dict[str, Any]) -> None:
        self.process_runs[run].update(patch)

    def list_process_runs(
        self, sheet: str, status: Optional[str] = None
    ) -> list[dict[str, Any]]:
        out = [r for r in self.process_runs.values() if r["sheet"] == sheet]
        if status is not None:
            out = [r for r in out if r.get("status") == status]
        return out

    def list_active_runs_with_due(self, now: Any) -> list[dict[str, Any]]:
        out = []
        for r in self.process_runs.values():
            if r.get("status") != "active":
                continue
            stages = r.get("stages") or []
            cur_idx = r.get("current_stage_idx")
            for s in stages:
                if s.get("stage_idx") == cur_idx and s.get("due_at") is not None \
                        and s.get("filled_at") is None:
                    out.append(r)
                    break
        return out


class RecordingEventSink:
    """An ``EventSink`` that captures emitted events in order, assigning a
    monotonic ``event_id``."""

    def __init__(self) -> None:
        self.events: list[TreeEvent] = []
        self._seq = itertools.count(1)

    def emit(self, event: TreeEvent) -> TreeEvent:
        from dataclasses import replace

        stored = replace(event, event_id=f"evt-{next(self._seq)}", timestamp="t")
        self.events.append(stored)
        return stored

    # convenience for assertions
    def types(self) -> list[str]:
        return [e.type for e in self.events]

    def last(self) -> TreeEvent:
        return self.events[-1]


class MockLLMProvider:
    """A scripted ``LLMProvider``. Construct with a list of turns; each call to
    ``complete`` returns the next one. A turn is
    ``{"content": str|None, "tool_calls": [...]}``."""

    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self._turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages, tools):
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self._turns:
            return {"content": "done", "tool_calls": []}
        return self._turns.pop(0)
