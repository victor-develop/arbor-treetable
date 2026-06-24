"""Ports (the ports-and-adapters seams).

The pure core depends ONLY on these Protocols. The Frappe app implements them
over the ORM + NestedSet (FrappeRepository, FrappeEventSink); the agent provider
implements LLMProvider over LiteLLM. Nothing here imports frappe.

A ``Protocol`` is structural: any object with these methods satisfies it, so the
in-memory test doubles in ``core.testing`` work without inheritance.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from .types import Actor, TreeEvent


# ---------------------------------------------------------------------------
# Lightweight read views the core operates on. The adapter returns these (or
# any duck-typed object exposing the same attributes) so the core never touches
# a frappe Document directly.
# ---------------------------------------------------------------------------
@runtime_checkable
class SheetView(Protocol):
    name: str
    structural_owner: str
    settings: dict[str, Any]


@runtime_checkable
class NodeView(Protocol):
    name: str
    sheet: str
    parent: Optional[str]
    lft: int
    rgt: int


@runtime_checkable
class ColumnView(Protocol):
    name: str
    sheet: str
    field: str
    column_owner: str
    editors: list[str]  # User names
    is_label: bool
    # Read-ACL (Feature 3, LEAN): one of {public, explicit-readers, owner-only}.
    # ``readers`` is the explicit-readers user allow-list (ignored at other levels).
    read_level: str
    readers: list[str]


@runtime_checkable
class BranchGrantView(Protocol):
    name: str
    sheet: str
    branch_root: str
    grantee: str
    scope: str
    active: bool


@runtime_checkable
class RoleView(Protocol):
    """An Arbor Role — a site-wide persona (PM/Developer/Marketing...). NOT
    sheet-scoped. ``applicable`` gates user self-application; ``active`` soft-
    retires the role (Feature: role management)."""

    name: str  # the Arbor Role docname (== the role key)
    role: str  # the role key, e.g. "pm"
    label: str
    applicable: bool
    active: bool


@runtime_checkable
class RoleGrantView(Protocol):
    """The held-role fact (analog of BranchGrant, role-scoped, site-wide). The
    SINGLE source of truth for who holds which role."""

    name: str
    role: str
    grantee: str
    granted_by: str
    active: bool
    source: str  # "admin-grant" | "application"


class Repository(Protocol):
    """The data seam. The adapter implements this over Frappe ORM + NestedSet;
    ``core.testing.InMemoryRepository`` implements it in pure Python.

    Reads return view objects; mutators return the new/affected id. The ACL
    resolver and capability handlers call ONLY these methods — never frappe.
    """

    # --- sheets / columns ---
    def get_sheet(self, sheet: str) -> SheetView: ...
    def get_column(self, sheet: str, column: str) -> ColumnView: ...
    def list_columns(self, sheet: str) -> list[ColumnView]: ...

    # --- nodes (NestedSet) ---
    def get_node(self, node: str) -> NodeView: ...
    def list_nodes(self, sheet: str) -> list[NodeView]: ...
    def count_nodes(self, sheet: str) -> int:
        """Total node count for ``sheet`` (the size guard + overview read on it).

        Purely derived from the node set; the in-memory double returns
        ``len(list_nodes(sheet))`` and the Frappe adapter issues a cheap
        ``COUNT(*)`` so the snapshot size guard never has to materialise rows.
        """
        ...
    def ancestors_self(self, node: str) -> list[NodeView]:
        """[node, parent, ..., root] — NEAREST FIRST (deepest ancestor first).

        NestedSet: ``WHERE sheet=? AND lft<=n.lft AND rgt>=n.rgt ORDER BY lft DESC``
        (DATA-MODEL §3). Includes the node itself.
        """
        ...

    def descendants(self, node: str) -> list[NodeView]:
        """Strict descendants of ``node`` (branch-subscription matching)."""
        ...

    # --- branch grants ---
    def find_active_branch_grant(
        self, sheet: str, branch_root: str, scope: str = "structure"
    ) -> Optional[BranchGrantView]: ...
    def get_branch_grant(self, branch_grant: str) -> Optional[BranchGrantView]: ...

    # --- cell values ---
    def get_value(self, node: str, column: str) -> Any: ...

    # --- mutators (the only writers; called from capability handlers) ---
    def create_node(
        self, sheet: str, parent: Optional[str], after: Optional[str] = None
    ) -> str: ...
    def set_value(self, sheet: str, node: str, column: str, value: Any) -> int:
        """Upsert cell, return the new version counter."""
        ...

    def move_node(
        self, node: str, new_parent: Optional[str], after: Optional[str] = None
    ) -> None: ...
    def delete_node(self, node: str, cascade: bool = True) -> list[str]:
        """Delete node (+ descendants if cascade); return deleted ids."""
        ...

    def create_column(self, sheet: str, spec: dict[str, Any]) -> str: ...
    def update_column(self, sheet: str, column: str, patch: dict[str, Any]) -> None: ...
    def delete_column(self, sheet: str, column: str) -> None: ...

    def create_branch_grant(
        self, sheet: str, branch_root: str, grantee: str, granted_by: str
    ) -> str: ...
    def deactivate_branch_grant(self, branch_grant: str) -> None: ...

    def set_column_authority(
        self,
        sheet: str,
        column: str,
        column_owner: Optional[str] = None,
        editors: Optional[list[str]] = None,
    ) -> None: ...

    # --- change requests ---
    def create_change_request(self, data: dict[str, Any]) -> str: ...
    def get_change_request(self, change_request: str) -> dict[str, Any]: ...
    def update_change_request(self, change_request: str, patch: dict[str, Any]) -> None: ...

    # --- subscriptions / notifications / acks ---
    def create_subscription(self, data: dict[str, Any]) -> str: ...
    def delete_subscription(self, subscription: str) -> None: ...
    def get_subscription(self, subscription: str) -> dict[str, Any]: ...
    def create_acknowledgement(self, notification: str, user: str) -> str: ...
    def get_notification(self, notification: str) -> dict[str, Any]: ...
    def create_notification(self, data: dict[str, Any]) -> str:
        """Create one in-app Notification row (direct recipient fan-out). Used by
        the sheet-less role flow, which cannot route through the sheet-scoped
        subscription matcher (Feature: role management)."""
        ...

    # --- roles / role grants / role applications (Feature: role management) ---
    def get_role(self, role: str) -> Optional["RoleView"]:
        """The Arbor Role by key, or None if it does not exist."""
        ...
    def list_active_role_grantees(self, role: str) -> list[str]:
        """Sorted User names with an ACTIVE grant of ``role`` — the ACL role->user
        expansion source AND the idempotency check for assign/approve."""
        ...
    def find_active_role_grant(self, role: str, grantee: str) -> Optional["RoleGrantView"]: ...
    def create_role_grant(
        self,
        role: str,
        grantee: str,
        granted_by: str,
        source: str = "admin-grant",
        granted_via: Optional[str] = None,
    ) -> str: ...
    def deactivate_role_grant(self, role_grant: str) -> None: ...
    def create_role_application(self, data: dict[str, Any]) -> str: ...
    def get_role_application(self, role_application: str) -> dict[str, Any]: ...
    def update_role_application(self, role_application: str, patch: dict[str, Any]) -> None: ...
    def find_open_role_application(self, role: str, requester: str) -> Optional[dict[str, Any]]:
        """A non-terminal (proposed) application by ``requester`` for ``role``, or
        None — the self-apply de-dupe guard."""
        ...
    def list_admins(self) -> list[str]:
        """User names holding the platform admin role (System Manager) — the
        recipients of a role-application-submitted notification."""
        ...


class EventSink(Protocol):
    """The event seam. ``emit`` is the ONLY way a Tree Event is recorded
    (ARCHITECTURE §4.3). The frappe sink writes a Tree Event row (and the
    dispatchers fan out from it); ``RecordingEventSink`` captures in memory.
    """

    def emit(self, event: TreeEvent) -> TreeEvent:
        """Persist the event, assigning ``event_id``/``timestamp``; return the
        stored event."""
        ...


class LLMProvider(Protocol):
    """The model seam for the Re-Act agent (ARCHITECTURE §8). LiteLLM implements
    this in the adapter; ``MockLLMProvider`` returns scripted frames so the loop
    is deterministic and offline.
    """

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Return one assistant turn:
        ``{"content": str|None, "tool_calls": [{"id","name","arguments"}, ...]}``
        Empty ``tool_calls`` means the loop terminates with ``content`` as the
        final answer.
        """
        ...
