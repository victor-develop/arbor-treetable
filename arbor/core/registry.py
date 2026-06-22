"""The capability registry — the single Python source of truth for everything
Arbor can do (ARCHITECTURE §4, CAPABILITIES.md).

All 26 capabilities are declared here as ``Capability`` records. Four consumers
read this ONE registry: Web ``executeAction``, auto-exposed REST methods, the
Tree Event stream (webhooks + notifications), and the LLM agent via
``get_llm_tools()`` (filtered by ``is_exposed_to_llm``).

ZERO frappe imports.
"""

from __future__ import annotations

from typing import Any

from . import handlers
from .types import (
    Axis,
    Capability,
    Operation,
    TargetKind,
    UnknownCapabilityError,
)

# ---------------------------------------------------------------------------
# Params schemas (JSON-schema style, CAPABILITIES.md). Kept inline so a
# capability's contract is co-located with its declaration.
# ---------------------------------------------------------------------------
_S_SNAPSHOT = {
    "type": "object",
    "required": ["sheet"],
    "properties": {"sheet": {"type": "string"}},
}
# --- explore (bounded LLM read API) schemas -------------------------------
_S_SHEET_OVERVIEW = {
    "type": "object",
    "required": ["sheet"],
    "properties": {"sheet": {"type": "string"}},
}
_S_LIST_CHILDREN = {
    "type": "object",
    "required": ["sheet"],
    "properties": {
        "sheet": {"type": "string"},
        "parent": {"type": ["string", "null"]},
        "cursor": {"type": ["string", "null"]},
        "limit": {"type": "number", "default": 50},
    },
}
_S_GET_SUBTREE = {
    "type": "object",
    "required": ["sheet", "node"],
    "properties": {
        "sheet": {"type": "string"},
        "node": {"type": "string"},
        "depth": {"type": "number", "default": 1},
        "cursor": {"type": ["string", "null"]},
        "limit": {"type": "number", "default": 50},
    },
}
_S_GET_NODE = {
    "type": "object",
    "required": ["sheet", "node"],
    "properties": {
        "sheet": {"type": "string"},
        "node": {"type": "string"},
    },
}
_S_SEARCH_NODES = {
    "type": "object",
    "required": ["sheet", "query"],
    "properties": {
        "sheet": {"type": "string"},
        "query": {"type": "string"},
        "column": {"type": ["string", "null"]},
        "cursor": {"type": ["string", "null"]},
        "limit": {"type": "number", "default": 50},
    },
}
_S_GET_CELLS = {
    "type": "object",
    "required": ["sheet", "nodes", "columns"],
    "properties": {
        "sheet": {"type": "string"},
        "nodes": {"type": "array", "items": {"type": "string"}},
        "columns": {"type": "array", "items": {"type": "string"}},
    },
}
_S_ADD_NODE = {
    "type": "object",
    "required": ["sheet", "parent"],
    "properties": {
        "sheet": {"type": "string"},
        "parent": {"type": ["string", "null"]},
        "after": {"type": ["string", "null"]},
        "values": {"type": "object"},
    },
}
_S_UPDATE_CELL = {
    "type": "object",
    "required": ["sheet", "node", "column", "value"],
    "properties": {
        "sheet": {"type": "string"},
        "node": {"type": "string"},
        "column": {"type": "string"},
        "value": {},
        # Feature 1 — optional optimistic-concurrency guard. Present -> the write
        # is rejected with VERSION_CONFLICT if the stored cell version moved;
        # absent -> blind overwrite (opt-in, preserves today's behavior).
        "base_version": {"type": "integer", "minimum": 0},
    },
}
_S_MOVE_NODE = {
    "type": "object",
    "required": ["sheet", "node", "new_parent"],
    "properties": {
        "sheet": {"type": "string"},
        "node": {"type": "string"},
        "new_parent": {"type": ["string", "null"]},
        "after": {"type": ["string", "null"]},
        # Feature 1 — optional vanished-anchor guard for concurrent moves.
        "expected_revision": {"type": ["string", "null"]},
    },
}
_S_DELETE_NODE = {
    "type": "object",
    "required": ["sheet", "node"],
    "properties": {
        "sheet": {"type": "string"},
        "node": {"type": "string"},
        "cascade": {"type": "boolean", "default": True},
    },
}
_S_ADD_COLUMN = {
    "type": "object",
    "required": ["sheet", "field", "label", "type"],
    "properties": {
        "sheet": {"type": "string"},
        "field": {"type": "string"},
        "label": {"type": "string"},
        "type": {
            "enum": [
                "text",
                "multiline-text",
                "number",
                "single-select-split",
                "multi-select-split",
            ]
        },
        "options": {"type": ["object", "null"]},
        "column_owner": {"type": "string"},
        "is_label": {"type": "boolean", "default": False},
    },
}
_S_UPDATE_COLUMN = {
    "type": "object",
    "required": ["sheet", "column"],
    "properties": {
        "sheet": {"type": "string"},
        "column": {"type": "string"},
        "patch": {"type": "object"},
    },
}
_S_SUGGEST = {
    "type": "object",
    "required": ["sheet", "target_kind", "operation", "payload"],
    "properties": {
        "sheet": {"type": "string"},
        "target_kind": {"enum": ["node-structure", "cell-value", "column-schema"]},
        "operation": {"enum": ["add", "update", "move", "delete"]},
        "payload": {"type": "object"},
    },
}
_S_SUGGEST_BATCH = {
    "type": "object",
    "required": ["sheet", "changes"],
    "properties": {
        "sheet": {"type": "string"},
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["action", "params"],
                "properties": {
                    "action": {"type": "string"},
                    "params": {"type": "object"},
                },
            },
        },
    },
}
_S_CR_DECISION = {
    "type": "object",
    "required": ["change_request"],
    "properties": {
        "change_request": {"type": "string"},
        "comment": {"type": "string"},
    },
}
_S_SUBSCRIBE = {
    "type": "object",
    "required": ["scope", "target", "event_types", "delivery"],
    "properties": {
        "subscriber": {"type": "string"},
        "scope": {"enum": ["sheet", "branch", "column"]},
        "target": {"type": "string"},
        "event_types": {"type": "array", "items": {"type": "string"}},
        "delivery": {"enum": ["in-app", "email", "webhook"]},
        "requires_ack": {"type": "boolean", "default": False},
    },
}
_S_UNSUBSCRIBE = {
    "type": "object",
    "required": ["subscription"],
    "properties": {"subscription": {"type": "string"}},
}
_S_ACK = {
    "type": "object",
    "required": ["notification"],
    "properties": {"notification": {"type": "string"}},
}
_S_DELEGATE = {
    "type": "object",
    "required": ["sheet", "branch_root", "grantee"],
    "properties": {
        "sheet": {"type": "string"},
        "branch_root": {"type": "string"},
        "grantee": {"type": "string"},
    },
}
_S_REVOKE = {
    "type": "object",
    "required": ["branch_grant"],
    "properties": {"branch_grant": {"type": "string"}},
}
_S_GRANT_COLUMN = {
    "type": "object",
    "required": ["sheet", "column"],
    "properties": {
        "sheet": {"type": "string"},
        "column": {"type": "string"},
        "column_owner": {"type": "string"},
        "editors": {"type": "array", "items": {"type": "string"}},
    },
}
_S_INTERNAL_RESET = {
    "type": "object",
    "required": ["sheet"],
    "properties": {"sheet": {"type": "string"}, "confirm": {"const": True}},
}


# ---------------------------------------------------------------------------
# The 26 capabilities.
# ---------------------------------------------------------------------------
_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        id="getSheetSnapshot",
        name="Get sheet snapshot",
        params_schema=_S_SNAPSHOT,
        axis=Axis.NONE,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="reader_can_view_sheet",
        emits=(),
        handler=None,  # read path; executor short-circuits to the serializer
    ),
    # --- explore: bounded, navigable LLM read API ------------------------
    Capability(
        id="getSheetOverview",
        name="Get sheet overview",
        params_schema=_S_SHEET_OVERVIEW,
        axis=Axis.NONE,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="reader_can_view_sheet",
        emits=(),
        handler=None,  # read path; executor routes to explore.sheet_overview
    ),
    Capability(
        id="listChildren",
        name="List children of a node",
        params_schema=_S_LIST_CHILDREN,
        axis=Axis.NONE,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="reader_can_view_sheet",
        emits=(),
        handler=None,  # read path; executor routes to explore.list_children
    ),
    Capability(
        id="getSubtree",
        name="Get a bounded subtree window",
        params_schema=_S_GET_SUBTREE,
        axis=Axis.NONE,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="reader_can_view_sheet",
        emits=(),
        handler=None,  # read path; executor routes to explore.get_subtree
    ),
    Capability(
        id="getNode",
        name="Get one node with all cells",
        params_schema=_S_GET_NODE,
        axis=Axis.NONE,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="reader_can_view_sheet",
        emits=(),
        handler=None,  # read path; executor routes to explore.get_node
    ),
    Capability(
        id="searchNodes",
        name="Search nodes by substring",
        params_schema=_S_SEARCH_NODES,
        axis=Axis.NONE,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="reader_can_view_sheet",
        emits=(),
        handler=None,  # read path; executor routes to explore.search_nodes
    ),
    Capability(
        id="getCells",
        name="Get a sparse node x column cell matrix",
        params_schema=_S_GET_CELLS,
        axis=Axis.NONE,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="reader_can_view_sheet",
        emits=(),
        handler=None,  # read path; executor routes to explore.get_cells
    ),
    Capability(
        id="addNode",
        name="Add node",
        params_schema=_S_ADD_NODE,
        axis=Axis.STRUCTURE,
        target_kind=TargetKind.NODE_STRUCTURE,
        operation=Operation.ADD,
        is_exposed_to_llm=True,
        acl_rule="resolve_structural_approver(parent)",
        emits=("NODE_CREATED",),
        handler=handlers.add_node_handler,
    ),
    Capability(
        id="updateCell",
        name="Update cell value",
        params_schema=_S_UPDATE_CELL,
        axis=Axis.COLUMN,
        target_kind=TargetKind.CELL_VALUE,
        operation=Operation.UPDATE,
        is_exposed_to_llm=True,
        acl_rule="resolve_column_approvers(column)",
        emits=("NODE_VALUE_UPDATED",),
        handler=handlers.update_cell_handler,
    ),
    Capability(
        id="moveNode",
        name="Move node",
        params_schema=_S_MOVE_NODE,
        axis=Axis.STRUCTURE,
        target_kind=TargetKind.NODE_STRUCTURE,
        operation=Operation.MOVE,
        is_exposed_to_llm=True,
        acl_rule="resolve_structural_approver(src) AND (dest)",
        emits=("NODE_MOVED",),
        handler=handlers.move_node_handler,
    ),
    Capability(
        id="deleteNode",
        name="Delete node",
        params_schema=_S_DELETE_NODE,
        axis=Axis.STRUCTURE,
        target_kind=TargetKind.NODE_STRUCTURE,
        operation=Operation.DELETE,
        is_exposed_to_llm=True,
        acl_rule="resolve_structural_approver(node)",
        emits=("NODE_DELETED",),
        handler=handlers.delete_node_handler,
    ),
    Capability(
        id="addColumn",
        name="Add column",
        params_schema=_S_ADD_COLUMN,
        axis=Axis.META,
        target_kind=TargetKind.COLUMN_SCHEMA,
        operation=Operation.ADD,
        is_exposed_to_llm=True,
        acl_rule="sheet.structural_owner (column_creation policy)",
        emits=("COLUMN_CONFIG_UPDATED",),
        handler=handlers.add_column_handler,
    ),
    Capability(
        id="updateColumn",
        name="Update column",
        params_schema=_S_UPDATE_COLUMN,
        axis=Axis.META,
        target_kind=TargetKind.COLUMN_SCHEMA,
        operation=Operation.UPDATE,
        is_exposed_to_llm=True,
        acl_rule="resolve_column_approvers(column)",
        emits=("COLUMN_CONFIG_UPDATED",),
        handler=handlers.update_column_handler,
    ),
    Capability(
        id="deleteColumn",
        name="Delete column",
        params_schema=_S_UPDATE_COLUMN,
        axis=Axis.META,
        target_kind=TargetKind.COLUMN_SCHEMA,
        operation=Operation.DELETE,
        is_exposed_to_llm=True,
        acl_rule="resolve_column_approvers(column)",
        emits=("COLUMN_CONFIG_UPDATED",),
        handler=handlers.delete_column_handler,
    ),
    Capability(
        id="suggestChange",
        name="Suggest a change",
        params_schema=_S_SUGGEST,
        axis=Axis.NONE,
        target_kind=TargetKind.NONE,  # taken from payload at runtime
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="always_allowed",
        emits=("CHANGE_PROPOSED",),
        handler=None,  # handled by executor/change_request (explicit CR creation)
    ),
    Capability(
        id="suggestChanges",
        name="Suggest a batch of changes",
        params_schema=_S_SUGGEST_BATCH,
        axis=Axis.NONE,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="always_allowed",
        # One Change Request bundling N changes, reviewed/approved/applied
        # atomically; each change re-resolves to its own approver.
        emits=("CHANGE_PROPOSED",),
        handler=None,  # handled by executor/change_request (batch CR creation)
    ),
    Capability(
        id="approveChange",
        name="Approve a change request",
        params_schema=_S_CR_DECISION,
        axis=Axis.NONE,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="actor == cr.resolved_approver (or column editor)",
        emits=("CHANGE_APPROVED",),
        handler=None,  # change_request.approve
    ),
    Capability(
        id="rejectChange",
        name="Reject a change request",
        params_schema=_S_CR_DECISION,
        axis=Axis.NONE,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="actor == cr.resolved_approver (or column editor)",
        emits=("CHANGE_REJECTED",),
        handler=None,  # change_request.reject
    ),
    Capability(
        id="withdrawChange",
        name="Withdraw a change request",
        params_schema=_S_CR_DECISION,
        axis=Axis.NONE,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="actor == cr.requester",
        emits=("CHANGE_REJECTED",),  # status=withdrawn; payload.reason="withdrawn"
        handler=None,  # change_request.withdraw
    ),
    Capability(
        id="subscribe",
        name="Subscribe",
        params_schema=_S_SUBSCRIBE,
        axis=Axis.NONE,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="self_subscribe_or_admin",
        emits=("SUBSCRIPTION_CHANGED",),
        handler=None,  # executor.subscribe
    ),
    Capability(
        id="unsubscribe",
        name="Unsubscribe",
        params_schema=_S_UNSUBSCRIBE,
        axis=Axis.NONE,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="owner_of_subscription",
        emits=("SUBSCRIPTION_CHANGED",),
        handler=None,  # executor.unsubscribe
    ),
    Capability(
        id="acknowledge",
        name="Acknowledge a notification",
        params_schema=_S_ACK,
        axis=Axis.NONE,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="recipient_of_notification",
        emits=(),  # creates Acknowledgement row; no Tree Event
        handler=None,  # executor.acknowledge
    ),
    Capability(
        id="delegateBranch",
        name="Delegate a branch",
        params_schema=_S_DELEGATE,
        axis=Axis.STRUCTURE,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="resolve_structural_approver(branch_root)",
        emits=("DELEGATION_CHANGED",),
        handler=handlers.delegate_branch_handler,
    ),
    Capability(
        id="revokeDelegation",
        name="Revoke a delegation",
        params_schema=_S_REVOKE,
        axis=Axis.STRUCTURE,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="granted_by_or_ancestor_structural_owner",
        emits=("DELEGATION_CHANGED",),
        handler=handlers.revoke_delegation_handler,
    ),
    Capability(
        id="grantColumn",
        name="Grant column ownership",
        params_schema=_S_GRANT_COLUMN,
        axis=Axis.COLUMN,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=True,
        acl_rule="current_column_owner_or_sheet_structural_owner",
        emits=("COLUMN_CONFIG_UPDATED",),
        handler=handlers.grant_column_handler,
    ),
    Capability(
        id="internalReset",
        name="Internal reset (administrative)",
        params_schema=_S_INTERNAL_RESET,
        axis=Axis.NONE,
        target_kind=TargetKind.NONE,
        operation=Operation.NONE,
        is_exposed_to_llm=False,  # the ONLY hidden capability
        acl_rule="system_or_admin_only",
        emits=(),  # never on the append-only Tree Event stream
        handler=handlers.internal_reset_handler,
    ),
)

_REGISTRY: dict[str, Capability] = {c.id: c for c in _CAPABILITIES}


def all_capabilities() -> tuple[Capability, ...]:
    """Every registered capability, declaration order preserved."""
    return _CAPABILITIES


def get_capability(action_id: str) -> Capability:
    try:
        return _REGISTRY[action_id]
    except KeyError as exc:
        raise UnknownCapabilityError(f"No capability registered: {action_id!r}") from exc


def has_capability(action_id: str) -> bool:
    return action_id in _REGISTRY


def to_llm_tool(cap: Capability) -> dict[str, Any]:
    """Render one capability as a LiteLLM/Claude tool definition from its
    ``params_schema``."""
    return {
        "type": "function",
        "function": {
            "name": cap.id,
            "description": cap.name,
            "parameters": cap.params_schema,
        },
    }


def get_llm_tools() -> list[dict[str, Any]]:
    """The registry filtered to ``is_exposed_to_llm`` and rendered as tool defs
    (CAPABILITIES.md ``getLLMTools()`` contract). ``internalReset`` is excluded.
    """
    return [to_llm_tool(c) for c in _CAPABILITIES if c.is_exposed_to_llm]
