"""Lightweight read views the standalone Repository returns.

Plain dataclasses duck-typed to the ``arbor.core.ports`` ``*View`` Protocols —
the standalone twin of the frappe adapter's ``_SheetView``/``_NodeView``/...
(``arbor/arbor/adapter/repository.py``) and of ``core.testing``'s ``_Sheet``/
``_Node``/... The repo mixins map ORM rows into these so the pure core never
touches a live SQLAlchemy object (no session lifetime surprises, no lazy
loads).

Shared by all repo mixins. APPEND-ONLY for parallel authorship: add a new
class if you need one; never edit an existing class.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SheetView:
    name: str
    structural_owner: str
    settings: dict[str, Any] = field(default_factory=dict)
    # The human display title (Sheet.title). The core falls back to ``name``
    # via ``getattr(sv, "title", "") or sv.name`` when unset.
    title: str = ""


@dataclass
class NodeView:
    name: str
    sheet: str
    parent: Optional[str]
    lft: int
    rgt: int
    idx: int = 0


@dataclass
class ColumnView:
    name: str
    sheet: str
    field: str
    column_owner: str
    editors: list[str] = field(default_factory=list)
    is_label: bool = False
    label: str = ""
    type: str = "text"
    options: Optional[Any] = None
    # Read-ACL (Feature 3, LEAN): {public, explicit-readers, owner-only}.
    read_level: str = "public"
    readers: list[str] = field(default_factory=list)


@dataclass
class BranchGrantView:
    name: str
    sheet: str
    branch_root: str
    grantee: str
    scope: str = "structure"
    active: bool = True
    granted_by: Optional[str] = None


@dataclass
class RoleView:
    name: str
    role: str
    label: str = ""
    applicable: bool = True
    active: bool = True


@dataclass
class RoleGrantView:
    name: str
    role: str
    grantee: str
    granted_by: str
    active: bool = True
    source: str = "admin-grant"
    granted_via: Optional[str] = None


@dataclass
class CommentView:
    """An Arbor Cell Comment (Area 2). A complete audit record: ``author`` is
    the EFFECTIVE actor; ``real_user`` / ``impersonated_as`` carry the
    impersonation trace; ``deleted`` is the soft-delete tombstone."""

    name: str
    sheet: str
    node: str
    column: str
    author: str
    body: str
    thread_root: Optional[str] = None
    parent_comment: Optional[str] = None
    mentions: list[str] = field(default_factory=list)
    resolved: bool = False
    resolved_by: Optional[str] = None
    resolved_at: Optional[Any] = None
    real_user: Optional[str] = None
    impersonated_as: Optional[str] = None
    deleted: bool = False


@dataclass
class ProcessRuleView:
    """One trigger->expectation rule of the process DAG. ``rule_key`` is the
    stable ledger ref an Expectation points back to; ``idx`` is 0-based
    presentation/canvas order only."""

    rule_key: str
    idx: int
    trigger_kind: str  # 'row' | 'column'
    trigger_op: str  # 'created' | 'updated' | 'created-or-updated'
    expected_columns: list[str] = field(default_factory=list)
    # The trigger SET (1+); ``trigger_column`` is a back-compat alias == [0].
    trigger_columns: list[str] = field(default_factory=list)
    trigger_join: str = "any"  # 'any' | 'all'
    trigger_column: Optional[str] = None
    within_seconds: int = 0
    notify_on_expect: bool = True
    label: Optional[str] = None


@dataclass
class ProcessView:
    name: str
    sheet: str
    title: str = ""
    enabled: bool = False
    row_scope: str = "root-children"
    sla_breach_notify: bool = True
    rules: list[ProcessRuleView] = field(default_factory=list)
