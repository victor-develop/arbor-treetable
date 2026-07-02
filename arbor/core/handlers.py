"""Capability handlers — the ONLY sites where each mutation's logic lives.

Each handler has the signature ``handler(params, actor, repo) -> HandlerResult``
and operates exclusively through the Repository protocol (no frappe). The
executor (and CR replay on approval) call these; no surface re-implements them.

Control-only capabilities (CR lifecycle, subscribe/ack, snapshot) are NOT here:
they are handled directly by the executor / change_request module because they
do not produce an axis-gated data mutation in the same shape.
"""

from __future__ import annotations

from typing import Any

from .ports import Repository
from .types import Actor, EventType, HandlerResult


# --- Axis 1 — structure -----------------------------------------------------
def add_node_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    sheet = params["sheet"]
    parent = params.get("parent")
    after = params.get("after")
    node = repo.create_node(sheet=sheet, parent=parent, after=after)
    # optional initial values: {column_field: value}
    versions: dict[str, int] = {}
    for col_field, value in (params.get("values") or {}).items():
        column = repo.get_column(sheet, col_field)
        versions[col_field] = repo.set_value(sheet, node, column.name, value)
    return HandlerResult(
        event_payload={"node": node, "parent": parent, "values": params.get("values") or {}},
        data={"node": node, "versions": versions},
    )


def move_node_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    node = params["node"]
    new_parent = params.get("new_parent")
    after = params.get("after")
    # Feature 1 — optimistic concurrency for moves: thread the optional
    # expected_revision (the vanished-anchor guard) through to the adapter, which
    # raises StaleMoveError when the anchor sibling has moved/vanished. Omitted ->
    # today's unchecked behavior.
    expected_revision = params.get("expected_revision")
    old = repo.get_node(node)
    repo.move_node(node, new_parent, after=after, expected_revision=expected_revision)
    return HandlerResult(
        event_payload={"node": node, "old_parent": old.parent, "new_parent": new_parent},
        data={"node": node},
    )


def delete_node_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    node = params["node"]
    cascade = params.get("cascade", True)
    # Capture the ancestor-or-self chain BEFORE deletion so branch-scoped
    # subscription/webhook matching still works for a NODE_DELETED event: by
    # dispatch time the row (and its NestedSet range) is gone, so the matcher
    # matches a branch by membership in this chain rather than by live range
    # (which would be compared against an already-shrunk ancestor range).
    ancestor_ids = [v.name for v in repo.ancestors_self(node)]
    deleted = repo.delete_node(node, cascade=cascade)
    return HandlerResult(
        event_payload={
            "node": node,
            "deleted": deleted,
            "cascade": cascade,
            "ancestor_ids": ancestor_ids,
        },
        data={"deleted": deleted},
    )


# --- Axis 2 — column value --------------------------------------------------
def update_cell_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    sheet = params["sheet"]
    node = params["node"]
    column = repo.get_column(sheet, params["column"])
    old_value = repo.get_value(node, column.name)
    new_value = params["value"]
    # Feature 1 — optimistic concurrency: thread the optional base_version guard.
    # Present -> set_value enforces it (raising StaleVersionError on mismatch);
    # absent -> today's blind-overwrite, no-check behavior (opt-in).
    base_version = params.get("base_version")
    version = repo.set_value(
        sheet, node, column.name, new_value, expected_version=base_version
    )
    return HandlerResult(
        event_payload={
            "node": node,
            "column": column.name,
            "old_value": old_value,
            "new_value": new_value,
            "version": version,
        },
        data={"version": version},
    )


# --- meta — schema ----------------------------------------------------------
def add_column_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    sheet = params["sheet"]
    spec = {
        "field": params["field"],
        "label": params["label"],
        "type": params["type"],
        "options": params.get("options"),
        "column_owner": params.get("column_owner") or actor.user,
        "is_label": params.get("is_label", False),
    }
    column = repo.create_column(sheet, spec)
    return HandlerResult(
        event_payload={"op": "add", "column": column, "field": params["field"]},
        data={"column": column},
    )


def update_column_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    sheet = params["sheet"]
    column = repo.get_column(sheet, params["column"])
    repo.update_column(sheet, column.name, params.get("patch") or {})
    return HandlerResult(
        event_payload={"op": "update", "column": column.name, "patch": params.get("patch") or {}},
        data={"column": column.name},
    )


def delete_column_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    sheet = params["sheet"]
    column = repo.get_column(sheet, params["column"])
    repo.delete_column(sheet, column.name)
    return HandlerResult(
        event_payload={"op": "delete", "column": column.name},
        data={"column": column.name},
    )


# --- ownership admin --------------------------------------------------------
def delegate_branch_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    sheet = params["sheet"]
    grant = repo.create_branch_grant(
        sheet=sheet,
        branch_root=params["branch_root"],
        grantee=params["grantee"],
        granted_by=actor.user,
    )
    return HandlerResult(
        event_payload={
            "op": "delegate",
            "branch_grant": grant,
            "branch_root": params["branch_root"],
            "grantee": params["grantee"],
        },
        data={"branch_grant": grant},
    )


def revoke_delegation_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    bg = params["branch_grant"]
    # revokeDelegation params carry only the grant id; resolve the sheet from the
    # grant so the emitted Tree Event is sheet-scoped like every other event.
    grant = repo.get_branch_grant(bg)
    sheet = grant.sheet if grant else None
    repo.deactivate_branch_grant(bg)
    return HandlerResult(
        event_payload={"op": "revoke", "branch_grant": bg, "sheet": sheet},
        data={"branch_grant": bg},
    )


def grant_column_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    sheet = params["sheet"]
    column = repo.get_column(sheet, params["column"])
    repo.set_column_authority(
        sheet,
        column.name,
        column_owner=params.get("column_owner"),
        editors=params.get("editors"),
    )
    return HandlerResult(
        event_payload={
            "op": "grant",
            "column": column.name,
            "column_owner": params.get("column_owner"),
            "editors": params.get("editors"),
        },
        data={"column": column.name},
    )


# --- process / SLA (Area 3) -------------------------------------------------
def define_process_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    """Upsert the sheet's Arbor Process definition (+ ordered stages). Emits
    COLUMN_CONFIG_UPDATED with ``op='process-define'`` so the closed 11-event set
    is preserved (op-discriminated, like the role flow reuses DELEGATION_CHANGED).
    """
    sheet = params["sheet"]
    stages = []
    for i, st in enumerate(params.get("stages") or []):
        stages.append(
            {
                "idx": i,
                "column": st["column"],
                "sla_seconds": int(st.get("sla_seconds") or 0),
                "notify_on_enter": st.get("notify_on_enter", True),
            }
        )
    process = repo.upsert_process(
        {
            "sheet": sheet,
            "title": params.get("title") or "",
            "stages": stages,
            "row_scope": params.get("row_scope", "root-children"),
            "start_trigger": params.get("start_trigger", "node-created"),
            "sla_breach_notify": params.get("sla_breach_notify", True),
        }
    )
    return HandlerResult(
        event_payload={"op": "process-define", "process": process, "sheet": sheet, "stages": stages},
        data={"process": process},
    )


def enable_process_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    """Enable the sheet's process + backfill active runs for existing in-scope
    nodes at stage 0. Emits COLUMN_CONFIG_UPDATED (op='process-enable')."""
    sheet = params["sheet"]
    process = repo.get_process(sheet)
    if process is None:
        raise ValueError(f"no process defined for sheet {sheet!r}")
    repo.set_process_enabled(process.name, True)
    return HandlerResult(
        event_payload={"op": "process-enable", "process": process.name, "sheet": sheet},
        data={"process": process.name},
    )


def disable_process_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    sheet = params["sheet"]
    process = repo.get_process(sheet)
    if process is None:
        raise ValueError(f"no process defined for sheet {sheet!r}")
    repo.set_process_enabled(process.name, False)
    return HandlerResult(
        event_payload={"op": "process-disable", "process": process.name, "sheet": sheet},
        data={"process": process.name},
    )


def start_process_run_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    """Manually start a run for a pre-existing row (manual start_trigger sheets).
    Delegates to the pure stage machine's start path so behavior matches the
    dispatch-lane NODE_CREATED consumer exactly."""
    from . import process as process_module

    sheet = params["sheet"]
    node = params["node"]
    process = repo.get_process(sheet)
    if process is None:
        raise ValueError(f"no process defined for sheet {sheet!r}")
    now = params.get("now")
    transitions = process_module.on_event(
        repo, process, {"type": "NODE_CREATED", "node": node}, now=now
    )
    return HandlerResult(
        event_payload={"op": "process-start-run", "process": process.name, "sheet": sheet, "node": node},
        data={"process": process.name, "node": node, "transitions": transitions},
    )


def internal_reset_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    # Administrative purge; NOT exposed to LLM and NOT on the Tree Event stream.
    # The handler exists for surface parity but the executor suppresses emission
    # (emits=()), so internalReset never lands on the append-only log.
    return HandlerResult(event_payload={"op": "internalReset", "sheet": params["sheet"]})
