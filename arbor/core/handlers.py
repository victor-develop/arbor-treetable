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


# --- sheet bootstrap (self-service create) ----------------------------------
def create_sheet_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    """Create a new sheet through the ONE Repository port. The creator becomes the
    sheet's ``structural_owner`` (set inside the adapter from ``actor``). Emits NO
    Tree Event (emits=() — the closed 11-event set is preserved; the Tree Sheet row
    is the record). ``label_column`` / ``first_column`` are accepted aliases for the
    default LABEL column's text (mirroring the ``create_sheet`` REST shim's
    ``label``)."""
    label_column = params.get("label_column") or params.get("first_column")
    created = repo.create_sheet(
        actor,
        title=params["title"],
        name=params.get("name"),
        label_column=label_column,
    )
    sheet = created["sheet"] if isinstance(created, dict) else created
    return HandlerResult(
        event_payload={"op": "create-sheet", "sheet": sheet},
        data={"sheet": sheet},
    )


# --- meta — schema ----------------------------------------------------------
def normalize_select_options(options: Any) -> Any:
    """Coerce a loosely-shaped select ``options`` payload into the ONE canonical
    stored shape ``{"groups": [{"label": str, "options": [str, ...]}, ...]}``.

    The addColumn schema deliberately types ``options`` as a loose object, and
    LLM callers invent close-but-wrong shapes (``{"choices": [...]}`` /
    ``{"options": [...]}`` / a bare list). Storing those raw crashed every
    reader that assumed ``options.groups`` — so the WRITE path (this one
    handler, shared by both adapters) normalizes. Unknown shapes fall back to
    None (no options) rather than persisting junk."""
    if options is None:
        return None
    # Bare list of option strings.
    if isinstance(options, list):
        opts = [str(o) for o in options if isinstance(o, (str, int, float))]
        return {"groups": [{"label": "Options", "options": opts}]} if opts else None
    if not isinstance(options, dict):
        return None
    # Canonical (or nearly): keep only well-formed groups, cleaned.
    if isinstance(options.get("groups"), list):
        groups = []
        for g in options["groups"]:
            if not isinstance(g, dict) or not isinstance(g.get("options"), list):
                continue
            opts = [str(o) for o in g["options"] if isinstance(o, (str, int, float))]
            groups.append({"label": str(g.get("label") or "Options"), "options": opts})
        return {"groups": groups} if groups else None
    # Common LLM inventions: {"choices": [...]} / {"options": [...]} / {"values": [...]}.
    for key in ("choices", "options", "values"):
        if isinstance(options.get(key), list):
            return normalize_select_options(options[key])
    return None


def add_column_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    sheet = params["sheet"]
    spec = {
        "field": params["field"],
        "label": params["label"],
        "type": params["type"],
        "options": normalize_select_options(params.get("options")),
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
    patch = dict(params.get("patch") or {})
    if "options" in patch:
        # Same normalization as add: the ONE write path keeps stored options canonical.
        patch["options"] = normalize_select_options(patch["options"])
    repo.update_column(sheet, column.name, patch)
    return HandlerResult(
        event_payload={"op": "update", "column": column.name, "patch": patch},
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
    """Upsert the sheet's Arbor Process definition (+ its trigger->expectation
    rule DAG). Validates the rule set (self-loop / duplicate-edge / cycle) via the
    pure ``process_graph.validate_rules`` — a cyclic or self-looping set raises a
    ``ValidationError`` (400) BEFORE any write. Emits COLUMN_CONFIG_UPDATED with
    ``op='process-define'`` so the closed 11-event set is preserved (op-
    discriminated, like the role flow reuses DELEGATION_CHANGED).
    """
    from .process_graph import ValidationError, validate_rules

    sheet = params["sheet"]
    rules = []
    for i, r in enumerate(params.get("rules") or []):
        kind = r["trigger_kind"]
        # normalize the trigger SET: prefer trigger_columns, else the single
        # trigger_column alias. Persist both (trigger_column == the first entry)
        # so back-compat readers keep working.
        tcols = [c for c in (r.get("trigger_columns") or []) if c is not None]
        if not tcols and r.get("trigger_column"):
            tcols = [r["trigger_column"]]
        rules.append(
            {
                "rule_key": r.get("rule_key") or f"r{i}",
                "idx": i,
                "trigger_kind": kind,
                "trigger_columns": tcols,
                "trigger_join": r.get("trigger_join") or "any",
                "trigger_column": tcols[0] if tcols else None,
                "trigger_op": r.get("trigger_op") or ("created" if kind == "row" else "updated"),
                "expected_columns": list(r.get("expected_columns") or []),
                "within_seconds": int(r.get("within_seconds") or 0),
                "notify_on_expect": r.get("notify_on_expect", True),
                "label": r.get("label"),
            }
        )
    # Server-side DAG authority: reject a self-loop / duplicate-edge / cycle.
    verdict = validate_rules(rules)
    if not verdict.ok:
        raise ValidationError(verdict.errors)

    process = repo.upsert_process(
        {
            "sheet": sheet,
            "title": params.get("title") or "",
            "rules": rules,
            "row_scope": params.get("row_scope", "root-children"),
            "sla_breach_notify": params.get("sla_breach_notify", True),
        }
    )
    return HandlerResult(
        event_payload={"op": "process-define", "process": process, "sheet": sheet, "rules": rules},
        data={"process": process, "warnings": verdict.warnings},
    )


def enable_process_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    """Enable the sheet's process + backfill runs for existing in-scope nodes
    through the SAME NODE_CREATED start path the dispatch lane uses (so a node
    that already carries filled columns opens/satisfies/cascades exactly as a
    fresh row would). Emits COLUMN_CONFIG_UPDATED (op='process-enable')."""
    from . import process as process_module

    sheet = params["sheet"]
    process = repo.get_process(sheet)
    if process is None:
        raise ValueError(f"no process defined for sheet {sheet!r}")
    repo.set_process_enabled(process.name, True)
    # re-read so the enabled flag is live for the backfill start path.
    process = repo.get_process(sheet)
    now = params.get("now")
    backfilled = 0
    for node in repo.list_in_scope_nodes(sheet, process.row_scope):
        if repo.get_process_run(process.name, node) is not None:
            continue
        process_module.on_event(
            repo, process, {"type": "NODE_CREATED", "node": node}, now=now
        )
        backfilled += 1
    return HandlerResult(
        event_payload={"op": "process-enable", "process": process.name, "sheet": sheet},
        data={"process": process.name, "backfilled": backfilled},
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


# --- per-cell comments (Area 2, promoted to capabilities) -------------------
# Framework-free handlers over the new comment Repository ports. They emit NO
# Tree Event (the Arbor Cell Comment row is the audit record); the executor routes
# them like the control caps (always execute or deny). The handler passes the FULL
# Actor so the adapter can stamp author + real_user + impersonated_as (audit +
# impersonation trace).
def add_comment_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    name = repo.create_comment(
        actor,
        sheet=params["sheet"],
        node=params["node"],
        column=params["column"],
        body=params["body"],
        parent_comment=params.get("parent_comment"),
        mentions=params.get("mentions"),
    )
    comment = repo.get_comment(name)
    thread_root = getattr(comment, "thread_root", None) if comment else None
    return HandlerResult(
        event_payload={"op": "comment-add", "comment": name, "thread_root": thread_root},
        data={"comment": name, "thread_root": thread_root},
    )


def resolve_comment_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    root = repo.set_comment_resolved(actor, params["comment"], bool(params["resolved"]))
    return HandlerResult(
        event_payload={"op": "comment-resolve", "comment": root, "resolved": bool(params["resolved"])},
        data={"comment": root, "resolved": bool(params["resolved"])},
    )


def delete_comment_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    repo.soft_delete_comment(actor, params["comment"])
    return HandlerResult(
        event_payload={"op": "comment-delete", "comment": params["comment"]},
        data={"comment": params["comment"], "deleted": True},
    )


def internal_reset_handler(params: dict[str, Any], actor: Actor, repo: Repository) -> HandlerResult:
    # Administrative purge; NOT exposed to LLM and NOT on the Tree Event stream.
    # The handler exists for surface parity but the executor suppresses emission
    # (emits=()), so internalReset never lands on the append-only log.
    return HandlerResult(event_payload={"op": "internalReset", "sheet": params["sheet"]})
