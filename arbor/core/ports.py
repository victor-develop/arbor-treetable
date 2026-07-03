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
    # Human display title (optional; adapters populate it, the in-memory double may
    # not). Readers use ``getattr(sv, "title", "") or sv.name`` so a missing title
    # falls back to the name — getSheetDefinition's sheet block reads it this way.
    title: str


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


@runtime_checkable
class CommentView(Protocol):
    """An Arbor Cell Comment — threaded, per-cell collaboration metadata keyed by
    the ``(sheet, node, column)`` cell (Area 2). Promoted to a capability audit
    record: ``author`` is the EFFECTIVE actor; ``real_user`` / ``impersonated_as``
    carry the impersonation trace (both None for a normal action); ``deleted`` is
    the soft-delete tombstone (row preserved for audit; ``list`` hides it)."""

    name: str
    sheet: str
    node: str
    column: str
    thread_root: Optional[str]
    parent_comment: Optional[str]
    author: str
    body: str
    resolved: bool
    real_user: Optional[str]
    impersonated_as: Optional[str]
    deleted: bool


@runtime_checkable
class ProcessRuleView(Protocol):
    """One trigger->expectation rule of an Arbor Process (process DAG). A rule
    reads "On <trigger>: expect (``expected_columns`` — an 'and' set sharing ONE
    window) filled within ``within_seconds``". ``trigger_kind`` is 'row' (a node
    create/update — the canvas START node) or 'column' (``trigger_column``
    create/update). Rules compose into a DAG because a column EXPECTED by one rule
    may be the ``trigger_column`` of another. Each expected column's responsible
    owner is resolved LIVE via ``acl.resolve_column_approvers`` (never stored), so
    re-grants reroute automatically.

    ``rule_key`` is the stable id an Expectation points back to (the 'rule_ref');
    ``idx`` is presentation/canvas order only and MUST NOT be used as the ledger
    reference.
    """

    rule_key: str
    idx: int
    trigger_kind: str  # 'row' | 'column'
    # The trigger SET for a column trigger (1+ columns). A single-column rule has
    # one entry. ``trigger_column`` is a back-compat alias == ``trigger_columns[0]``.
    trigger_columns: list[str]
    # 'any' (or a single trigger) fires when ANY named trigger column is updated
    # (today's behavior); 'all' (an AND-join / fan-in) fires ONCE when EVERY
    # trigger column is filled (the update that completes the set is the trigger).
    trigger_join: str  # 'any' | 'all' (default 'any')
    trigger_column: Optional[str]  # back-compat alias == trigger_columns[0]
    trigger_op: str  # 'created' | 'updated' | 'created-or-updated'
    expected_columns: list[str]
    within_seconds: int  # 0 => no SLA
    notify_on_expect: bool
    label: Optional[str]


@runtime_checkable
class ProcessView(Protocol):
    """A per-sheet Arbor Process definition (process DAG). Exactly one ENABLED
    process per sheet (enforced in the controller + on enable). ``rules`` is the
    trigger->expectation rule SET (validated acyclic on define)."""

    name: str
    sheet: str
    title: str
    enabled: bool
    row_scope: str  # 'root-children' | 'all-nodes' | 'depth'
    sla_breach_notify: bool
    rules: list[ProcessRuleView]


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

    def create_sheet(
        self,
        actor: Actor,
        title: str,
        name: Optional[str] = None,
        label_column: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a brand-new sheet (the self-service bootstrap capability). The
        CREATOR (``actor.user``) becomes the sheet's ``structural_owner`` and the
        owner of the default LABEL column (so their very first snapshot grants
        can_add_column / can_change_structure). ``name`` is an optional stable id
        (else the adapter autonames); ``label_column`` is the LABEL text of the
        default first column (defaults to "Item"). Returns ``{"sheet": <id>}``.

        A duplicate ``name`` is a storage conflict; the adapter raises its
        ConflictError (surfaced by the API as 409)."""
        ...

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

    # --- impersonation sessions (Area 1) ------------------------------------
    def create_impersonation_session(
        self, real_user: str, impersonated_user: str, reason: Optional[str] = None
    ) -> str:
        """Persist (and activate) an "act as" overlay for ``real_user`` acting as
        ``impersonated_user``. At most one active session per real_user (the
        handler ends any prior one first). Returns the new session id."""
        ...

    def get_active_impersonation(self, real_user: str) -> Optional[dict[str, Any]]:
        """The active overlay row for ``real_user`` (``{name, real_user,
        impersonated_user, reason, active}``), or None."""
        ...

    def end_impersonation(self, real_user: str) -> None:
        """Deactivate any active overlay for ``real_user`` (idempotent)."""
        ...

    # --- per-cell comments (Area 2, promoted to capabilities) ---------------
    def get_comment(self, comment: str) -> Optional["CommentView"]:
        """The Arbor Cell Comment by id (INCLUDING soft-deleted tombstones, so the
        executor can still authorize a delete/resolve against a deleted row), or
        None if it does not exist."""
        ...

    def create_comment(
        self,
        actor: Actor,
        sheet: str,
        node: str,
        column: str,
        body: str,
        parent_comment: Optional[str] = None,
        mentions: Optional[list[str]] = None,
    ) -> str:
        """Insert a comment and return its id. The adapter stamps the FULL audit
        trace from ``actor``: ``author=actor.user`` +
        ``real_user=actor.real_user`` + ``impersonated_as=actor.impersonated_as``
        (so an impersonated post records the REAL principal). ``thread_root`` is
        derived from ``parent_comment`` (the controller's job)."""
        ...

    def set_comment_resolved(self, actor: Actor, comment: str, resolved: bool) -> str:
        """Resolve (``resolved=True``) or reopen (``resolved=False``) the comment's
        THREAD ROOT. Idempotent. Returns the affected root id."""
        ...

    def soft_delete_comment(self, actor: Actor, comment: str) -> None:
        """Soft-delete (tombstone) a comment: ``deleted=1`` + ``deleted_by`` +
        ``deleted_at``, row PRESERVED for audit. ``list`` filters it out."""
        ...

    # --- process / SLA (process DAG) ----------------------------------------
    def upsert_process(self, data: dict[str, Any]) -> str:
        """Create or replace the sheet's Arbor Process definition (+ rules);
        return its id. ``data`` = {sheet, title?, rules:[{rule_key, trigger_kind,
        trigger_column?, trigger_op, expected_columns:[...], within_seconds?,
        notify_on_expect?, label?}], row_scope?, sla_breach_notify?}."""
        ...

    def get_process(self, sheet: str) -> Optional["ProcessView"]:
        """The sheet's process definition (enabled or not), or None."""
        ...

    def set_process_enabled(self, process: str, enabled: bool) -> None:
        """Flip the process ``enabled`` flag (enable/disable capability)."""
        ...

    def list_in_scope_nodes(self, sheet: str, row_scope: str) -> list[str]:
        """Node ids that count as process 'rows' under ``row_scope`` (used by
        enableProcess backfill + on NODE_CREATED scope check)."""
        ...

    def create_process_run(self, data: dict[str, Any]) -> str:
        """Create an Arbor Process Run (+ its expectation ledger). ``data`` =
        {process, sheet, node, status, started_at, expectations:[{rule_key,
        expected_column, opened_at?, satisfied_at?, due_at?, ...}]}"""
        ...

    def get_process_run(self, process: str, node: str) -> Optional[dict[str, Any]]:
        """The run for (process, node), or None (unique per pair)."""
        ...

    def update_process_run(self, run: str, patch: dict[str, Any]) -> None:
        """Patch a run row (status/completed_at/expectations)."""
        ...

    def list_process_runs(
        self, sheet: str, status: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Runs for ``sheet`` (optionally filtered by status) — dashboard source."""
        ...

    def list_active_runs_with_due(self, now: str) -> list[dict[str, Any]]:
        """Active runs with an OPEN (unsatisfied) expectation whose due_at <=
        ``now`` — the bounded SLA-sweep candidate set."""
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
