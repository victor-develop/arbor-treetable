"""SQLAlchemy 2.0 Declarative models for the standalone adapter.

One class per Frappe doctype the ``Repository`` port touches (arbor/arbor/
doctype/*/), translated with three systematic rules:

1. **PK** — every row keeps a frappe-style string ``name`` (docname analog),
   defaulted to a short url-safe random id, so core code that passes ids around
   as opaque strings is unchanged.
2. **Child tables → JSON** — frappe child doctypes that are pure lists
   (Tree Column Editor/Reader, CR approvals/changes) become ``sa.JSON`` list
   columns; the two process child tables (rules, run expectations) keep real
   tables because rules are individually addressed by ``rule_key`` and the SLA
   sweep filters expectations in SQL.
3. **Links stay plain strings** — no FK constraints, matching frappe Link
   semantics (referential checks live in the controllers/handlers, and cascade
   deletes like ``delete_node`` stay a simple id-list sweep).

Portability: ``sa.JSON`` + ``sa.Boolean`` + explicit VARCHAR lengths work on
both sqlite (tests) and MySQL (deploy). Select-style fields are VARCHARs; the
allowed value sets are documented inline and enforced by the core, exactly as
frappe never enforced them at the SQL layer either.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: DATETIME with microseconds on BOTH backends. Plain MySQL DATETIME truncates
#: to whole seconds, which would collapse the ``creation`` insertion-order key
#: (NestedSet sibling order, column/comment ordering) for burst inserts; sqlite
#: keeps microseconds natively.
DateTimeUS = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def new_id() -> str:
    """A short random url-safe id (~11 chars) — the frappe hash-autoname analog."""
    return secrets.token_urlsafe(8)


_last_now: Optional[datetime] = None


def utcnow() -> datetime:
    """Naive UTC now (MySQL DATETIME has no tz; frappe stores naive too).

    Strictly monotonic within the process: burst inserts can tie on the OS
    clock's resolution, and ``creation`` doubles as the insertion-order key
    (NestedSet sibling order, list ordering) — a tie would let the random
    ``name`` tiebreak scramble insertion order. Ties are nudged forward 1µs."""
    global _last_now
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if _last_now is not None and now <= _last_now:
        now = _last_now + timedelta(microseconds=1)
    _last_now = now
    return now


class Base(DeclarativeBase):
    pass


class NamedRow:
    """The shared frappe-document skeleton: string PK + creation/modified
    bookkeeping. ``creation`` doubles as the stable insertion-order key (the
    frappe adapter orders columns/notifications/events by it)."""

    name: Mapped[str] = mapped_column(sa.String(140), primary_key=True, default=new_id)
    creation: Mapped[datetime] = mapped_column(DateTimeUS, default=utcnow)
    modified: Mapped[datetime] = mapped_column(DateTimeUS, default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# Users. Frappe supplied the User doctype + the System Manager role for free;
# standalone keeps the minimum the Repository needs: identity (email), display
# name, and the platform-admin gate behind ``list_admins``.
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(sa.String(140), primary_key=True)
    full_name: Mapped[str] = mapped_column(sa.String(140), default="")
    # The System Manager analog — ``list_admins`` returns enabled users with
    # this flag (role-application recipients + the admin capability gate).
    is_admin: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    creation: Mapped[datetime] = mapped_column(DateTimeUS, default=utcnow)


# ---------------------------------------------------------------------------
# Sheets / nodes / columns / values — the tree-table core (DATA-MODEL §2-§4).
# ---------------------------------------------------------------------------
class Sheet(NamedRow, Base):
    __tablename__ = "sheets"

    title: Mapped[str] = mapped_column(sa.String(255))
    description: Mapped[Optional[str]] = mapped_column(sa.Text, default=None)
    structural_owner: Mapped[str] = mapped_column(sa.String(140))  # User email
    status: Mapped[str] = mapped_column(sa.String(20), default="draft")  # draft|active|archived
    settings: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)


class Node(NamedRow, Base):
    """Tree Node with materialised NestedSet bounds. ``lft``/``rgt`` are owned
    by the repository's rebuild (same DFS as InMemoryRepository); ancestor and
    descendant walks are the two interval queries from DATA-MODEL §3."""

    __tablename__ = "nodes"
    __table_args__ = (sa.Index("ix_nodes_sheet_lft", "sheet", "lft"),)

    sheet: Mapped[str] = mapped_column(sa.String(140))
    parent: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)  # None = root
    lft: Mapped[int] = mapped_column(sa.Integer, default=0)
    rgt: Mapped[int] = mapped_column(sa.Integer, default=0)


class Column(NamedRow, Base):
    """Tree Column — the horizontal ownership axis. ``editors``/``readers``
    (frappe child tables of bare User links) collapse to JSON lists of emails."""

    __tablename__ = "columns"
    __table_args__ = (sa.Index("ix_columns_sheet", "sheet"),)

    sheet: Mapped[str] = mapped_column(sa.String(140))
    field: Mapped[str] = mapped_column(sa.String(140))  # stable key within the sheet
    label: Mapped[str] = mapped_column(sa.String(255), default="")
    # text|multiline-text|number|single-select-split|multi-select-split
    type: Mapped[str] = mapped_column(sa.String(40), default="text")
    options: Mapped[Optional[Any]] = mapped_column(sa.JSON, default=None)
    width: Mapped[int] = mapped_column(sa.Integer, default=150)
    editable: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    is_label: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    idx: Mapped[int] = mapped_column(sa.Integer, default=0)  # presentation order
    # Axis 2 authority
    column_owner: Mapped[str] = mapped_column(sa.String(140), default="")
    editors: Mapped[list[str]] = mapped_column(sa.JSON, default=list)
    # Read-ACL (Feature 3): public|explicit-readers|owner-only
    read_level: Mapped[str] = mapped_column(sa.String(20), default="public")
    readers: Mapped[list[str]] = mapped_column(sa.JSON, default=list)


class NodeValue(Base):
    """One cell. ``version`` is the optimistic-concurrency counter ``set_value``
    bumps (StaleVersionError on mismatch, same as InMemoryRepository)."""

    __tablename__ = "node_values"
    __table_args__ = (sa.UniqueConstraint("node", "column", name="uq_node_values_node_column"),)

    name: Mapped[str] = mapped_column(sa.String(140), primary_key=True, default=new_id)
    sheet: Mapped[str] = mapped_column(sa.String(140))
    node: Mapped[str] = mapped_column(sa.String(140))
    column: Mapped[str] = mapped_column(sa.String(140))
    value: Mapped[Optional[Any]] = mapped_column(sa.JSON, default=None)
    version: Mapped[int] = mapped_column(sa.Integer, default=1)
    modified: Mapped[datetime] = mapped_column(DateTimeUS, default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# Delegation + change requests (Axis 1 grants, mutate-or-suggest ledger).
# ---------------------------------------------------------------------------
class BranchGrant(NamedRow, Base):
    __tablename__ = "branch_grants"
    __table_args__ = (sa.Index("ix_branch_grants_sheet_root", "sheet", "branch_root"),)

    sheet: Mapped[str] = mapped_column(sa.String(140))
    branch_root: Mapped[str] = mapped_column(sa.String(140))  # Node name
    grantee: Mapped[str] = mapped_column(sa.String(140))  # User email
    scope: Mapped[str] = mapped_column(sa.String(20), default="structure")
    granted_by: Mapped[str] = mapped_column(sa.String(140))
    active: Mapped[bool] = mapped_column(sa.Boolean, default=True)


class ChangeRequest(NamedRow, Base):
    """Change Request (DATA-MODEL §6). The frappe child tables — approvals
    (user/real_user/approved_at rows) and ``changes`` (the batch-CR item list)
    — are JSON arrays of dicts with the same keys; the core already builds and
    reads them as plain dicts through the Repository."""

    __tablename__ = "change_requests"

    sheet: Mapped[str] = mapped_column(sa.String(140))
    target_kind: Mapped[str] = mapped_column(sa.String(20))  # node-structure|cell-value|column-schema|batch
    operation: Mapped[str] = mapped_column(sa.String(20))  # add|update|move|delete|multi
    status: Mapped[str] = mapped_column(sa.String(20), default="proposed")  # proposed|approved|rejected|withdrawn
    requester: Mapped[str] = mapped_column(sa.String(140))
    real_requester: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    resolved_approver: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    approvals: Mapped[list[dict[str, Any]]] = mapped_column(sa.JSON, default=list)
    payload: Mapped[Optional[Any]] = mapped_column(sa.JSON, default=None)
    changes: Mapped[list[dict[str, Any]]] = mapped_column(sa.JSON, default=list)
    decided_by: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    real_decider: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTimeUS, default=None)
    resulting_event: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)


# ---------------------------------------------------------------------------
# Events + fan-out (subscriptions, notifications, acks) — DATA-MODEL §12.
# ---------------------------------------------------------------------------
class TreeEventRow(Base):
    """The append-only event ledger — the ONLY record of "what happened".
    Rows are inserted by the standalone EventSink and never updated/deleted."""

    __tablename__ = "tree_events"
    __table_args__ = (sa.Index("ix_tree_events_sheet_creation", "sheet", "creation"),)

    name: Mapped[str] = mapped_column(sa.String(140), primary_key=True, default=new_id)
    creation: Mapped[datetime] = mapped_column(DateTimeUS, default=utcnow)
    sheet: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    type: Mapped[str] = mapped_column(sa.String(40))  # one of types.EVENT_TYPES
    actor: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    actor_type: Mapped[str] = mapped_column(sa.String(10), default="human")  # human|agent|system
    real_user: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    impersonated_as: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    change_request: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    payload: Mapped[Optional[Any]] = mapped_column(sa.JSON, default=None)


class Subscription(NamedRow, Base):
    __tablename__ = "subscriptions"

    subscriber: Mapped[str] = mapped_column(sa.String(140))
    subscriber_kind: Mapped[str] = mapped_column(sa.String(10), default="user")  # user|external
    scope: Mapped[str] = mapped_column(sa.String(10), default="sheet")  # sheet|branch|column
    # The scope target id (Sheet/Node/Column name). Frappe modelled this as a
    # Dynamic Link + target_doctype pair; the doctype half is dropped because
    # ``scope`` already determines the target's kind.
    target: Mapped[str] = mapped_column(sa.String(140))
    event_types: Mapped[Optional[list[str]]] = mapped_column(sa.JSON, default=None)  # None = all
    delivery: Mapped[str] = mapped_column(sa.String(10), default="in-app")  # in-app|email|webhook
    requires_ack: Mapped[bool] = mapped_column(sa.Boolean, default=False)


class Notification(NamedRow, Base):
    __tablename__ = "notifications"
    __table_args__ = (sa.Index("ix_notifications_recipient", "recipient"),)

    source: Mapped[str] = mapped_column(sa.String(20), default="tree_event")  # tree_event|comment|process|sla
    tree_event: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    comment: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    change_request: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    recipient: Mapped[str] = mapped_column(sa.String(140))
    channel: Mapped[str] = mapped_column(sa.String(10), default="in-app")  # in-app|email|webhook
    requires_ack: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTimeUS, default=None)
    # Non-schema context keys the core attaches at fan-out time (``op``/``role``/
    # ``node``/... — InMemoryRepository keeps the whole dict, and readers pull
    # these back out of the notification itself, not the linked event).
    extra: Mapped[Optional[dict[str, Any]]] = mapped_column(sa.JSON, default=None)


class Acknowledgement(NamedRow, Base):
    __tablename__ = "acknowledgements"

    notification: Mapped[str] = mapped_column(sa.String(140))
    user: Mapped[str] = mapped_column(sa.String(140))
    acked_at: Mapped[Optional[datetime]] = mapped_column(DateTimeUS, default=utcnow)


# ---------------------------------------------------------------------------
# Roles (Feature: role management) — site-wide personas + the grant ledger.
# ---------------------------------------------------------------------------
class Role(Base):
    """Arbor Role. Frappe autonames by ``field:role``, so the PK IS the role
    key (no random id here)."""

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(sa.String(140), primary_key=True)  # == role key
    role: Mapped[str] = mapped_column(sa.String(140), unique=True)
    label: Mapped[str] = mapped_column(sa.String(255), default="")
    description: Mapped[Optional[str]] = mapped_column(sa.Text, default=None)
    applicable: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    creation: Mapped[datetime] = mapped_column(DateTimeUS, default=utcnow)


class RoleGrant(NamedRow, Base):
    __tablename__ = "role_grants"
    __table_args__ = (sa.Index("ix_role_grants_role", "role"),)

    role: Mapped[str] = mapped_column(sa.String(140))
    grantee: Mapped[str] = mapped_column(sa.String(140))
    granted_by: Mapped[str] = mapped_column(sa.String(140))
    source: Mapped[str] = mapped_column(sa.String(20), default="admin-grant")  # admin-grant|application
    granted_via: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    active: Mapped[bool] = mapped_column(sa.Boolean, default=True)


class RoleApplication(NamedRow, Base):
    __tablename__ = "role_applications"

    role: Mapped[str] = mapped_column(sa.String(140))
    requester: Mapped[str] = mapped_column(sa.String(140))
    status: Mapped[str] = mapped_column(sa.String(20), default="proposed")  # proposed|approved|rejected|withdrawn
    justification: Mapped[Optional[str]] = mapped_column(sa.Text, default=None)
    decided_by: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTimeUS, default=None)
    resulting_grant: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    decided_event: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)


# ---------------------------------------------------------------------------
# Impersonation sessions (Area 1) — the "act as" overlay.
# ---------------------------------------------------------------------------
class ImpersonationSession(NamedRow, Base):
    __tablename__ = "impersonation_sessions"

    real_user: Mapped[str] = mapped_column(sa.String(140))
    impersonated_user: Mapped[str] = mapped_column(sa.String(140))
    active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTimeUS, default=utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTimeUS, default=None)
    reason: Mapped[Optional[str]] = mapped_column(sa.Text, default=None)


# ---------------------------------------------------------------------------
# Per-cell comments (Area 2) — a complete capability audit record.
# ---------------------------------------------------------------------------
class CellComment(NamedRow, Base):
    """``author`` is the EFFECTIVE actor; ``real_user``/``impersonated_as``
    carry the impersonation trace (both None for a normal action); ``deleted``
    is the soft-delete tombstone (row preserved for audit; list reads hide it)."""

    __tablename__ = "cell_comments"
    __table_args__ = (sa.Index("ix_cell_comments_cell", "sheet", "node", "column"),)

    sheet: Mapped[str] = mapped_column(sa.String(140))
    node: Mapped[str] = mapped_column(sa.String(140))
    column: Mapped[str] = mapped_column(sa.String(140))
    thread_root: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    parent_comment: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    author: Mapped[str] = mapped_column(sa.String(140))
    body: Mapped[str] = mapped_column(sa.Text)
    mentions: Mapped[list[str]] = mapped_column(sa.JSON, default=list)
    resolved: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    resolved_by: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTimeUS, default=None)
    real_user: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    impersonated_as: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    deleted: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    deleted_by: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTimeUS, default=None)


# ---------------------------------------------------------------------------
# Process / SLA (Area 3) — definition, rules, runs, expectation ledger.
# ---------------------------------------------------------------------------
class Process(NamedRow, Base):
    __tablename__ = "processes"

    sheet: Mapped[str] = mapped_column(sa.String(140))
    title: Mapped[str] = mapped_column(sa.String(255), default="")
    enabled: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    row_scope: Mapped[str] = mapped_column(sa.String(20), default="root-children")  # root-children|all-nodes|depth
    sla_breach_notify: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_by: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    settings: Mapped[Optional[dict[str, Any]]] = mapped_column(sa.JSON, default=None)


class ProcessRule(Base):
    """Child rows of a Process (frappe child table kept as a real table so
    rules stay addressable by ``rule_key``). ``idx`` is presentation/canvas
    order only; ``rule_key`` is the stable ledger ref."""

    __tablename__ = "process_rules"
    __table_args__ = (sa.Index("ix_process_rules_process", "process"),)

    name: Mapped[str] = mapped_column(sa.String(140), primary_key=True, default=new_id)
    process: Mapped[str] = mapped_column(sa.String(140))  # parent Process name
    rule_key: Mapped[str] = mapped_column(sa.String(140))
    idx: Mapped[int] = mapped_column(sa.Integer, default=0)
    label: Mapped[Optional[str]] = mapped_column(sa.String(255), default=None)
    trigger_kind: Mapped[str] = mapped_column(sa.String(10), default="column")  # row|column
    trigger_column: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)  # alias == trigger_columns[0]
    trigger_columns: Mapped[list[str]] = mapped_column(sa.JSON, default=list)
    trigger_join: Mapped[str] = mapped_column(sa.String(10), default="any")  # any|all
    trigger_op: Mapped[str] = mapped_column(sa.String(20), default="updated")  # created|updated|created-or-updated
    expected_columns: Mapped[list[str]] = mapped_column(sa.JSON, default=list)
    within_seconds: Mapped[int] = mapped_column(sa.Integer, default=0)  # 0 => no SLA
    notify_on_expect: Mapped[bool] = mapped_column(sa.Boolean, default=True)


class ProcessRun(NamedRow, Base):
    __tablename__ = "process_runs"
    __table_args__ = (sa.Index("ix_process_runs_sheet", "sheet"),)

    process: Mapped[str] = mapped_column(sa.String(140))
    sheet: Mapped[str] = mapped_column(sa.String(140))
    node: Mapped[str] = mapped_column(sa.String(140))
    status: Mapped[str] = mapped_column(sa.String(20), default="active")  # active|completed|abandoned
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTimeUS, default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTimeUS, default=None)
    # The pure machine's timestamps VERBATIM ({started_at, completed_at}). The
    # machine has no clock — it hands the repo whatever ``now`` it was given
    # (numeric epochs in tests, ISO strings in the api layer) and expects the
    # same value back; the typed columns above are the coerced query keys.
    raw: Mapped[Optional[dict[str, Any]]] = mapped_column(sa.JSON, default=None)


class ProcessRunExpectation(Base):
    """The per-run expectation ledger (one row per rule_key x expected_column).
    The SLA sweep's candidate query filters on (satisfied_at IS NULL, breached=0,
    due_at <= now), so those live as real columns, not JSON."""

    __tablename__ = "process_run_expectations"
    __table_args__ = (sa.Index("ix_pre_run", "run"),)

    name: Mapped[str] = mapped_column(sa.String(140), primary_key=True, default=new_id)
    run: Mapped[str] = mapped_column(sa.String(140))  # parent ProcessRun name
    rule_key: Mapped[str] = mapped_column(sa.String(140))
    expected_column: Mapped[str] = mapped_column(sa.String(140))
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTimeUS, default=None)
    satisfied_at: Mapped[Optional[datetime]] = mapped_column(DateTimeUS, default=None)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTimeUS, default=None)
    breached: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    breached_at: Mapped[Optional[datetime]] = mapped_column(DateTimeUS, default=None)
    notified_owner: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    # ``open_due`` is precomputed on write (due_at set, unsatisfied, un-breached
    # — from the RAW values) so the sweep's candidate query stays one indexable
    # predicate; ``raw`` is the machine's expectation dict VERBATIM (clockless
    # timestamps round-trip untouched, exactly as InMemoryRepository stores them).
    open_due: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    raw: Mapped[Optional[dict[str, Any]]] = mapped_column(sa.JSON, default=None)


# ---------------------------------------------------------------------------
# Webhooks — endpoints + the delivery/retry ledger.
# ---------------------------------------------------------------------------
class WebhookEndpoint(NamedRow, Base):
    __tablename__ = "webhook_endpoints"

    label: Mapped[str] = mapped_column(sa.String(255), default="")
    url: Mapped[str] = mapped_column(sa.String(1024))
    # The HMAC signing secret. Frappe stored it as a Password field; here it is
    # an opaque server-side column — never returned by any API read.
    secret: Mapped[Optional[str]] = mapped_column(sa.String(255), default=None)
    active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    sheet: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    owner_user: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    scope: Mapped[str] = mapped_column(sa.String(10), default="sheet")  # sheet|branch|column
    target: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    event_types: Mapped[Optional[list[str]]] = mapped_column(sa.JSON, default=None)  # None = all
    notification_sources: Mapped[Optional[str]] = mapped_column(sa.Text, default=None)


class WebhookDelivery(NamedRow, Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (sa.Index("ix_webhook_deliveries_status", "status"),)

    endpoint: Mapped[str] = mapped_column(sa.String(140))
    source: Mapped[str] = mapped_column(sa.String(20), default="tree_event")  # tree_event|comment|process|sla|change_request
    tree_event: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    notification: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    event_id: Mapped[Optional[str]] = mapped_column(sa.String(140), default=None)
    status: Mapped[str] = mapped_column(sa.String(20), default="pending")  # pending|delivered|failed|exhausted
    attempts: Mapped[int] = mapped_column(sa.Integer, default=0)
    signature: Mapped[Optional[str]] = mapped_column(sa.String(255), default=None)
    body: Mapped[Optional[str]] = mapped_column(sa.Text, default=None)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTimeUS, default=None)
    last_response: Mapped[Optional[str]] = mapped_column(sa.Text, default=None)


# ---------------------------------------------------------------------------
# Agent tokens — the external-agent down-scope PAT (core/agent_scope.py).
# ---------------------------------------------------------------------------
class AgentToken(NamedRow, Base):
    """Only the salted HASH of the secret is stored (``token_hash``); the raw
    token is shown once at mint time and never persisted (agent_scope's
    token-secret math)."""

    __tablename__ = "agent_tokens"

    label: Mapped[str] = mapped_column(sa.String(255), default="")
    user: Mapped[str] = mapped_column(sa.String(140))
    mode: Mapped[str] = mapped_column(sa.String(10), default="write")  # read|write
    # Newline/comma-separated sheet allow-list; empty/None = every sheet the
    # user can reach (mirrors the doctype's Small Text field).
    sheets: Mapped[Optional[str]] = mapped_column(sa.Text, default=None)
    token_hash: Mapped[str] = mapped_column(sa.String(255), unique=True)
    expires_on: Mapped[Optional[datetime]] = mapped_column(DateTimeUS, default=None)
    revoked: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTimeUS, default=None)
