"""Arbor pure-domain types.

ZERO frappe imports. These dataclasses/enums are the vocabulary shared by the
capability registry, the ACL resolver, the centralized executor, the CR state
machine, and the agent loop. They are deliberately framework-free so the whole
core layer is unit-testable with plain pytest (no bench).

See docs/ARCHITECTURE.md and docs/DATA-MODEL.md (the locked spec).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Event types — the CLOSED set (DATA-MODEL §12). The only record of "what
# happened"; written solely by the event emitter; consumed by webhooks +
# notifications. There are exactly 11.
# ---------------------------------------------------------------------------
class EventType(str, Enum):
    NODE_CREATED = "NODE_CREATED"
    NODE_DELETED = "NODE_DELETED"
    NODE_MOVED = "NODE_MOVED"
    NODE_VALUE_UPDATED = "NODE_VALUE_UPDATED"
    COLUMN_CONFIG_UPDATED = "COLUMN_CONFIG_UPDATED"
    CHANGE_PROPOSED = "CHANGE_PROPOSED"
    CHANGE_APPROVED = "CHANGE_APPROVED"
    CHANGE_REJECTED = "CHANGE_REJECTED"
    SUBSCRIPTION_CHANGED = "SUBSCRIPTION_CHANGED"
    DELEGATION_CHANGED = "DELEGATION_CHANGED"
    IMPORT_COMPLETED = "IMPORT_COMPLETED"


#: All 11 event-type constants, for membership checks / closed-set assertions.
EVENT_TYPES: tuple[str, ...] = tuple(e.value for e in EventType)


class Axis(str, Enum):
    """Which ownership axis (if any) gates a capability (ARCHITECTURE §2)."""

    STRUCTURE = "structure"  # Axis 1 — vertical, subtree, delegable
    COLUMN = "column"  # Axis 2 — horizontal, field-scoped
    META = "meta"  # schema ops (addColumn → sheet owner; up/del → column)
    NONE = "none"  # no axis gate (snapshot, CR lifecycle, subscribe, ack)


class TargetKind(str, Enum):
    """Change Request target_kind (DATA-MODEL §6)."""

    NODE_STRUCTURE = "node-structure"
    CELL_VALUE = "cell-value"
    COLUMN_SCHEMA = "column-schema"
    NONE = "none"


class Operation(str, Enum):
    """Change Request operation (DATA-MODEL §6)."""

    ADD = "add"
    UPDATE = "update"
    MOVE = "move"
    DELETE = "delete"
    NONE = "none"


class ActorType(str, Enum):
    """actor_type on a Tree Event (DATA-MODEL §12)."""

    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"


class CRStatus(str, Enum):
    """Change Request lifecycle states (ARCHITECTURE §5)."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True)
class Actor:
    """The acting identity. Humans, the agent, and API/external callers are
    indistinguishable to the executor except by these two fields (ARCHITECTURE
    §4.2). ``user`` is the Frappe User name; the agent uses its OWN user.
    """

    user: str
    actor_type: ActorType = ActorType.HUMAN
    # System/admin flag: set by the surface layer (which knows the platform's
    # roles). Gates administrative capabilities like ``internalReset`` that the
    # framework-free core cannot otherwise authorize.
    is_admin: bool = False

    def __str__(self) -> str:  # so ``actor in {user_name, ...}`` style reads cleanly
        return self.user


@dataclass(frozen=True)
class Capability:
    """A declarative capability record — the single source of truth for one
    action (ARCHITECTURE §4, CAPABILITIES.md). ``handler`` is the ONLY site of
    the mutation's logic; it operates against the Repository protocol only.
    """

    id: str
    name: str
    params_schema: dict[str, Any]
    axis: Axis
    target_kind: TargetKind
    operation: Operation
    is_exposed_to_llm: bool
    acl_rule: str  # human-readable name of the resolver branch that decides
    emits: tuple[str, ...]  # Tree Event type(s) emitted on success
    handler: Optional[Any] = None  # callable(params, actor, repo) -> HandlerResult

    @property
    def emits_primary(self) -> Optional[str]:
        return self.emits[0] if self.emits else None


@dataclass(frozen=True)
class Authority:
    """Result of the ACL resolver (ARCHITECTURE §2, PERMISSIONS §1)."""

    is_authorized: bool
    resolved_approver: Optional[str] = None
    co_approvers: tuple[str, ...] = ()


@dataclass(frozen=True)
class TreeEvent:
    """A serialized Tree Event (DATA-MODEL §12). The pure core produces this
    shape; the frappe EventSink persists it and assigns the real name/id.
    """

    sheet: str
    type: str
    payload: dict[str, Any]
    actor: str
    actor_type: ActorType
    change_request: Optional[str] = None
    event_id: Optional[str] = None
    timestamp: Optional[str] = None


@dataclass(frozen=True)
class HandlerResult:
    """What a capability handler returns: the event payload to emit plus any
    surface-facing result data (e.g. the new node id)."""

    event_payload: dict[str, Any]
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Outcome:
    """The result of ``execute_action`` (ARCHITECTURE §4.2). Exactly one of the
    two governance branches: ``executed`` (mutated + emitted) or ``suggested``
    (became a Change Request)."""

    kind: str  # "executed" | "suggested" | "read"
    event: Optional[TreeEvent] = None
    change_request: Optional[str] = None
    result: Optional[HandlerResult] = None
    data: dict[str, Any] = field(default_factory=dict)
    # On the "suggested" branch: who the Change Request routes to, plus any
    # moveNode co-approvers — surfaced so the UI can name the approver.
    resolved_approver: Optional[str] = None
    co_approvers: tuple[str, ...] = ()


class ArborError(Exception):
    """Base for all core-raised domain errors."""


class SchemaValidationError(ArborError):
    """params failed the capability's params_schema validation."""


class UnknownCapabilityError(ArborError):
    """No capability registered under the given id."""


class AuthorizationError(ArborError):
    """Actor not permitted to perform a non-mutating control action (e.g.
    approving a CR they don't own). Distinct from the suggest-routing path,
    which is NOT an error."""


class CRStateError(ArborError):
    """Illegal Change Request state transition."""


class StaleVersionError(ArborError):
    """Optimistic-concurrency: a cell's stored version != the caller's
    ``expected_version`` guard (Feature 1). The pure-core analog of the adapter's
    storage-level ``StaleVersionError``; carries ``current_version`` and
    ``current_value`` so a surface can build the VERSION_CONFLICT payload without
    a second read."""

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
