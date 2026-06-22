"""Arbor pure-domain core.

ZERO frappe imports. Importable and unit-testable with plain pytest (no bench).
This is the DRY hub: ONE registry, ONE executor, ONE ACL resolver, ONE event
shape, ONE snapshot serializer, ONE CR state machine, ONE agent loop.

The Frappe app (``arbor/`` outside this package) is the ADAPTER implementing the
Repository + EventSink + LLMProvider ports.
"""

from __future__ import annotations

from .acl import (
    resolve_authority,
    resolve_column_approvers,
    resolve_structural_approver,
)
from .agentloop import AgentResult, run_agent
from .backoff import (
    MAX_ATTEMPTS,
    RETRY_SCHEDULE_SECONDS,
    delay_for_attempt,
    is_exhausted,
    next_retry_offset,
)
from .change_request import (
    approve_change,
    create_change_request,
    reject_change,
    withdraw_change,
)
from .executor import execute_action
from .registry import (
    all_capabilities,
    get_capability,
    get_llm_tools,
    has_capability,
    to_llm_tool,
)
from .schema import validate_schema
from .security import compute_signature, verify_signature
from .snapshot import serialize_snapshot
from .types import (
    EVENT_TYPES,
    Actor,
    ActorType,
    Authority,
    Axis,
    Capability,
    CRStatus,
    EventType,
    HandlerResult,
    Operation,
    Outcome,
    TargetKind,
    TreeEvent,
)

__all__ = [
    # types
    "Actor",
    "ActorType",
    "Authority",
    "Axis",
    "Capability",
    "CRStatus",
    "EventType",
    "EVENT_TYPES",
    "HandlerResult",
    "Operation",
    "Outcome",
    "TargetKind",
    "TreeEvent",
    # registry
    "all_capabilities",
    "get_capability",
    "get_llm_tools",
    "has_capability",
    "to_llm_tool",
    # acl
    "resolve_authority",
    "resolve_column_approvers",
    "resolve_structural_approver",
    # executor
    "execute_action",
    # change request
    "approve_change",
    "create_change_request",
    "reject_change",
    "withdraw_change",
    # schema / security / backoff / snapshot / agent
    "validate_schema",
    "compute_signature",
    "verify_signature",
    "serialize_snapshot",
    "RETRY_SCHEDULE_SECONDS",
    "MAX_ATTEMPTS",
    "delay_for_attempt",
    "next_retry_offset",
    "is_exhausted",
    "run_agent",
    "AgentResult",
]
