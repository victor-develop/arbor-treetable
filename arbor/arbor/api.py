"""Arbor whitelisted REST surface — the API peer of Web (ARCHITECTURE §8/§11).

ONE whitelisted method per capability, plus a generic ``execute_action`` and
``get_sheet_snapshot``. Every method funnels into the single pure
``arbor.core.executor.execute_action`` with a frappe-derived ``Actor`` — so the
REST surface re-derives NOTHING: same registry, same ACL resolver, same
handlers, same Tree Event. This is the surface-parity guarantee (API-010/011).

Actor: the authenticated Frappe user. API/external callers are ``human``
actor_type (api.md API-003: "API callers are humans/external, never agent").
The server-side agent uses its own path with ``actor_type=agent``.

Error contracts (api.md):
- 401 — no/invalid auth: enforced by Frappe before these methods run; we also
  guard against ``Guest``.
- 403 — a CONTROL action denied (approve/reject/withdraw/ack by a non-authorized
  actor → core ``AuthorizationError``) maps to ``frappe.PermissionError``. NOTE:
  a denied *mutation* is NOT a 403 — it becomes a Change Request (``suggested``),
  which is a 200 success outcome.
- 404 — unknown capability, or a referenced sheet/node/column/CR that does not
  exist (``frappe.DoesNotExistError`` / core ``UnknownCapabilityError``).
- 409 — storage conflicts: stale move, cycle, stale cell version
  (``adapter.repository.ConflictError``).
- 400 — params schema validation failure (core ``SchemaValidationError``).
"""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe import _

from arbor.core import executor, registry
from arbor.core.acl import (
    can_read_column,
    resolve_column_approvers,
    resolve_structural_approver,
    visible_columns,
)
from arbor.core.explore import CellBudgetExceededError, SheetTooLargeError
from arbor.core.snapshot import serialize_snapshot
from arbor.core.types import (
    Actor,
    ActorType,
    AuthorizationError,
    CRStateError,
    Outcome,
    SchemaValidationError,
    UnknownCapabilityError,
)
try:  # adapter sibling module: ``arbor.adapter`` on a bench, ``arbor.arbor.adapter`` in-repo.
    from arbor.adapter.repository import (
        ConflictError,
        FrappeEventSink,
        FrappeRepository,
        StaleMoveError,
        StaleVersionError,
    )
except ModuleNotFoundError:  # pragma: no cover - dev-layout fallback
    from arbor.arbor.adapter.repository import (  # type: ignore
        ConflictError,
        FrappeEventSink,
        FrappeRepository,
        StaleMoveError,
        StaleVersionError,
    )


# ---------------------------------------------------------------------------
# Wiring helpers
# ---------------------------------------------------------------------------
def _actor() -> Actor:
    """The acting identity = the authenticated Frappe user (always ``human`` on
    the REST surface)."""
    user = frappe.session.user
    if not user or user == "Guest":
        # Defense in depth; Frappe normally rejects unauthenticated calls to a
        # whitelisted method (no allow_guest) with 403/401 before we get here.
        raise frappe.AuthenticationError(_("Authentication required"))
    is_admin = user == "Administrator" or "System Manager" in set(frappe.get_roles(user))
    return Actor(user=user, actor_type=ActorType.HUMAN, is_admin=is_admin)


def _repo() -> FrappeRepository:
    return FrappeRepository()


def _sink() -> FrappeEventSink:
    return FrappeEventSink()


# Public adapter façade consumed by the agent lane (arbor.agent.chat):
#   get_repository() -> Repository, get_event_sink() -> EventSink,
#   get_sheet_snapshot(sheet, actor) -> dict (shared serializer + ACL hints).
def get_repository() -> FrappeRepository:
    """Return a Frappe-backed ``Repository`` (the data seam)."""
    return _repo()


def get_event_sink() -> FrappeEventSink:
    """Return a Frappe-backed ``EventSink`` (the event seam)."""
    return _sink()


def _outcome_dict(outcome: Outcome) -> dict[str, Any]:
    """Serialize an ``Outcome`` into the stable REST envelope (api.md "Standard
    envelope")."""
    body: dict[str, Any] = {"kind": outcome.kind, "data": outcome.data or {}}
    if outcome.change_request:
        body["change_request"] = outcome.change_request
    if outcome.resolved_approver:
        body["resolved_approver"] = outcome.resolved_approver
    if outcome.co_approvers:
        body["co_approvers"] = list(outcome.co_approvers)
    if outcome.event is not None:
        body["event"] = {
            "event_id": outcome.event.event_id,
            "type": outcome.event.type,
            "sheet": outcome.event.sheet,
            "actor": outcome.event.actor,
            "actor_type": (
                outcome.event.actor_type.value
                if isinstance(outcome.event.actor_type, ActorType)
                else outcome.event.actor_type
            ),
            "change_request": outcome.event.change_request,
        }
    if outcome.result is not None:
        body["result"] = outcome.result.data
    return body


def _dispatch(action_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """The ONE funnel: every capability method routes here → core.execute_action.

    Translates core/adapter exceptions into Frappe's HTTP status conventions.
    """
    actor = _actor()
    repo = _repo()
    sink = _sink()
    try:
        outcome = executor.execute_action(action_id, params or {}, actor, repo, sink)
    except UnknownCapabilityError as exc:
        frappe.local.response["http_status_code"] = 404
        frappe.throw(str(exc), exc=frappe.DoesNotExistError)
    except SchemaValidationError as exc:
        frappe.local.response["http_status_code"] = 400
        frappe.throw(str(exc), exc=frappe.ValidationError)
    except AuthorizationError as exc:
        # Control-action denial (approve/reject/withdraw/unsubscribe/ack) — 403.
        # A denied mutation never reaches here (it becomes a CR / "suggested").
        raise frappe.PermissionError(str(exc)) from exc
    except CRStateError as exc:
        # Deciding a terminal CR, etc. — idempotency conflict (api.md API-140).
        frappe.local.response["http_status_code"] = 409
        frappe.throw(str(exc), exc=frappe.ValidationError)
    except frappe.LinkValidationError as exc:
        # A referenced node/column/sheet does not exist (Link validation) — 404.
        frappe.local.response["http_status_code"] = 404
        frappe.throw(str(exc), exc=frappe.DoesNotExistError)
    except (StaleVersionError, StaleMoveError) as exc:
        # Feature 1 — optimistic concurrency: a lost-update conflict is NOT a 4xx.
        # It is a structured HTTP-200 ``read`` Outcome the FE reads off
        # ``outcome.error`` (useSheet.ts) to raise a conflict banner with the
        # authoritative current state — no thrown status, no second read.
        return {
            "kind": "read",
            "error": "VERSION_CONFLICT",
            "data": {
                "node": (params or {}).get("node"),
                "column": (params or {}).get("column"),
                "current_version": getattr(exc, "current_version", 0),
                "current_value": getattr(exc, "current_value", None),
            },
        }
    except ConflictError as exc:
        # Remaining storage conflicts — CycleError / CRState-shaped (api.md J) — 409.
        frappe.local.response["http_status_code"] = 409
        frappe.throw(str(exc), exc=frappe.ValidationError)
    except SheetTooLargeError as exc:
        # Whole-sheet read over EXPLORE_THRESHOLD (only reachable via a generic
        # getSheetSnapshot dispatch) — 422 with the explore-tool hint. 4xx, never 500.
        frappe.local.response["http_status_code"] = 422
        frappe.local.response["sheet_too_large"] = {
            "count": exc.count,
            "threshold": exc.threshold,
            "explore_tools": list(exc.EXPLORE_TOOLS),
        }
        frappe.throw(str(exc), exc=frappe.ValidationError)
    except CellBudgetExceededError as exc:
        # getCells matrix over CELL_BUDGET — request too large; 422.
        frappe.local.response["http_status_code"] = 422
        frappe.throw(str(exc), exc=frappe.ValidationError)
    except ValueError as exc:
        # Bad keyset cursor or unknown node referenced by an explore read — 400.
        frappe.local.response["http_status_code"] = 400
        frappe.throw(str(exc), exc=frappe.ValidationError)
    return _outcome_dict(outcome)


# ---------------------------------------------------------------------------
# Generic dispatch + snapshot (ARCHITECTURE §8.1)
# ---------------------------------------------------------------------------
@frappe.whitelist()
def execute_action(action_id: str, params: Optional[dict] = None) -> dict[str, Any]:
    """Generic capability dispatch ≡ the named method (api.md API-011).

    ``POST /api/method/arbor.execute_action {action_id, params}``.
    """
    return _dispatch(action_id, _coerce(params))


@frappe.whitelist()
def get_sheet_snapshot(sheet: str, actor: Optional[Actor] = None) -> dict[str, Any]:
    """The shared read serializer (ARCHITECTURE §4.3). Computes per-actor ACL
    hints (edit-vs-suggest affordances) and feeds the ONE pure serializer; no
    Tree Event is emitted (read).

    ``GET /api/method/arbor.get_sheet_snapshot?sheet=…``. Unknown sheet → 404.

    ``actor`` is omitted on the REST surface (derived from the session); the
    agent lane passes its own ``Actor`` (``actor_type=agent``) so its read
    reflects agent affordances. Either way the SAME serializer runs.
    """
    if actor is None:
        actor = _actor()
    repo = _repo()

    if not frappe.db.exists("Tree Sheet", sheet):
        frappe.local.response["http_status_code"] = 404
        frappe.throw(_("No such sheet {0}").format(sheet), exc=frappe.DoesNotExistError)

    # Flow through the executor for parity (it short-circuits snapshot to a read
    # AND runs the >500 size guard). A sheet over EXPLORE_THRESHOLD raises the
    # typed SheetTooLargeError — surface it as a 4xx (422) carrying the count,
    # threshold, and the explore tools to use instead. NEVER an unhandled 500.
    try:
        executor.execute_action("getSheetSnapshot", {"sheet": sheet}, actor, repo, _sink())
    except SheetTooLargeError as exc:
        frappe.local.response["http_status_code"] = 422
        frappe.local.response["sheet_too_large"] = {
            "count": exc.count,
            "threshold": exc.threshold,
            "explore_tools": list(exc.EXPLORE_TOOLS),
        }
        frappe.throw(str(exc), exc=frappe.ValidationError)

    sheet_view = repo.get_sheet(sheet)
    # Read-ACL (Feature 3): filter to the columns this actor may read BEFORE
    # building values/hints. Because serialize_snapshot + the values loop iterate
    # the passed columns, a forbidden column drops from headers AND every node's
    # cells together — no cell can leak.
    columns = visible_columns(repo, sheet_view, actor, repo.list_columns(sheet))
    nodes = repo.list_nodes(sheet)

    # Per-cell values + parallel versions, keyed by (node, column). Feature 1:
    # the versions map seeds each FE cell's base_version, built from the SAME
    # Tree Node Value rows so a forbidden column never gets a version either.
    # Bulk-load every cell for the sheet in ONE query. A wide sheet has
    # nodes×columns cells; the previous per-cell get_value + get_value_version
    # was an N+1 round-trip storm (hundreds of queries → tens of seconds on a
    # 16-column sheet). Filter to the read-ACL-visible columns + present nodes so
    # nothing forbidden leaks (same guarantee as iterating the passed columns).
    visible_col_names = {c.name for c in columns}
    node_names = {n.name for n in nodes}
    values: dict[tuple[str, str], Any] = {}
    versions: dict[tuple[str, str], int] = {}
    for row in frappe.get_all(
        "Tree Node Value",
        filters={"sheet": sheet},
        fields=["node", "column", "value", "version"],
    ):
        if row.column not in visible_col_names or row.node not in node_names:
            continue
        key = (row.node, row.column)
        raw = row.value
        v = frappe.parse_json(raw) if isinstance(raw, str) and raw else raw
        if v is not None:
            values[key] = v
        if row.version:
            versions[key] = int(row.version)

    # Per-cell pending suggestions: open (proposed) Change Requests targeting a
    # cell, so the grid can light a marker that SURVIVES refresh and is visible
    # to EVERY viewer who can read the column (not just the suggester's session).
    # Built only for the already read-ACL-filtered columns → cannot leak.
    pending = _pending_cell_marks(sheet, repo, {c.name for c in columns})

    acl_hints = _acl_hints(actor, repo, sheet, columns, nodes)
    return serialize_snapshot(
        sheet_view, columns, nodes, values, acl_hints, versions=versions, pending=pending
    )


def _pending_cell_marks(
    sheet: str, repo: Any, visible_col_names: set[str]
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """``{(node, column): [{change_request, requester, value}]}`` from the
    sheet's OPEN (proposed) Change Requests that target a cell.

    Covers single-change CRs (whose ``payload`` carries node+column+value) and
    the items of a multi-change CR (each change's payload does). Only cells whose
    column is in ``visible_col_names`` (already read-ACL-filtered) are marked, so
    a pending marker can never reveal a column the viewer may not read. The
    proposed ``value`` is included because the column is readable by this viewer
    (and the value becomes its committed content on approval anyway)."""
    marks: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def _add(node: Any, column: Any, value: Any, cr_name: str, requester: Any) -> None:
        if not node or not column or column not in visible_col_names:
            return
        marks.setdefault((node, column), []).append(
            {"change_request": cr_name, "requester": requester, "value": value}
        )

    names = frappe.get_all(
        "Change Request",
        filters={"sheet": sheet, "status": "proposed"},
        order_by="creation asc",
        pluck="name",
    )
    for name in names:
        cr = repo.get_change_request(name)
        requester = cr.get("requester")
        items = cr.get("changes") or []
        if items:
            for it in items:
                p = it.get("payload") or {}
                _add(p.get("node"), p.get("column"), p.get("value"), name, requester)
        else:
            p = cr.get("payload") or {}
            _add(p.get("node"), p.get("column"), p.get("value"), name, requester)
    return marks


# ---------------------------------------------------------------------------
# Explore: the bounded, navigable LLM read API (ARCHITECTURE §8 / explore.py).
# Each is a thin whitelisted shim funnelling into the ONE _dispatch (so the
# explore reads enjoy the same surface-parity guarantee as every mutation and
# control op). The executor routes them to the pure arbor.core.explore functions
# and returns Outcome(kind="read", data=<result>). Size/budget violations raise
# typed errors that _dispatch maps to 422; bad cursors / unknown nodes -> 400.
# These let the agent / UI navigate a tree piece-by-piece instead of pulling a
# whole snapshot (which getSheetSnapshot refuses above EXPLORE_THRESHOLD).
# ---------------------------------------------------------------------------
@frappe.whitelist()
def sheet_overview(sheet):
    """Structural summary of a sheet — ALWAYS safe (no per-node cells), so it is
    the right first call on a large sheet. ``GET /api/method/arbor.sheet_overview``."""
    return _dispatch("getSheetOverview", {"sheet": sheet})


@frappe.whitelist()
def list_children(sheet, parent=None, cursor=None, limit=50):
    """One parent's direct children, keyset-paginated (``parent`` omitted = roots).
    Each node carries all its cells + its own child_count."""
    return _dispatch(
        "listChildren",
        {
            "sheet": sheet,
            "parent": parent,
            "cursor": cursor,
            "limit": frappe.utils.cint(limit) if limit is not None else 50,
        },
    )


@frappe.whitelist()
def get_subtree(sheet, node, depth=1, cursor=None, limit=50):
    """A bounded preorder window of ``node``'s subtree, to ``depth`` levels,
    node-budget capped (clipped with has_more + next_cursor when exceeded)."""
    return _dispatch(
        "getSubtree",
        {
            "sheet": sheet,
            "node": node,
            "depth": frappe.utils.cint(depth) if depth is not None else 1,
            "cursor": cursor,
            "limit": frappe.utils.cint(limit) if limit is not None else 50,
        },
    )


@frappe.whitelist()
def get_node(sheet, node):
    """One node with ALL its cells, child_count, and a root..node breadcrumb path."""
    return _dispatch("getNode", {"sheet": sheet, "node": node})


@frappe.whitelist()
def search_nodes(sheet, query, column=None, cursor=None, limit=50):
    """Case-insensitive substring search over the label (and the given column, or
    every value when ``column`` is omitted); keyset-paginated."""
    return _dispatch(
        "searchNodes",
        {
            "sheet": sheet,
            "query": query,
            "column": column,
            "cursor": cursor,
            "limit": frappe.utils.cint(limit) if limit is not None else 50,
        },
    )


@frappe.whitelist()
def get_cells(sheet, nodes, columns):
    """A sparse ``node x column`` value matrix. ``nodes``/``columns`` are JSON
    arrays. Rejects a matrix over the cell budget (422)."""
    return _dispatch(
        "getCells",
        {
            "sheet": sheet,
            "nodes": _coerce(nodes) or [],
            "columns": _coerce(columns) or [],
        },
    )


def _acl_hints(actor, repo, sheet, columns, nodes) -> dict[str, Any]:
    """Compute the edit/structure affordances the thin React shell renders from
    (ARCHITECTURE §2.3 can_edit_cell / can_change_structure). Reuses the ONE
    ACL resolver — no re-implementation."""
    can_edit_column = {
        c.name: actor.user in resolve_column_approvers(repo, sheet, c.name)
        for c in columns
    }
    can_change_structure = {
        n.name: actor.user == resolve_structural_approver(repo, sheet, n.name)
        for n in nodes
    }
    # Sheet-level affordances: add-column is the structural owner's (schema co-design),
    # and the viewer's own sheet-scoped subscription powers the subscribe control.
    can_add_column = actor.user == repo.get_sheet(sheet).structural_owner
    subscription = frappe.db.get_value(
        "Subscription",
        {"subscriber": actor.user, "scope": "sheet", "target": sheet},
        "name",
    )
    # Active branch delegations on this sheet. can_revoke gates the UI affordance
    # (the granter, or the sheet's structural owner as an ancestor, may revoke);
    # the server re-enforces authority on dispatch regardless.
    sheet_owner = repo.get_sheet(sheet).structural_owner
    grants = frappe.get_all(
        "Branch Grant",
        filters={"sheet": sheet, "active": 1},
        fields=["name", "branch_root", "grantee", "granted_by"],
        order_by="creation asc",
    )
    branch_grants = [
        {
            "name": g.name,
            "branch_root": g.branch_root,
            "grantee": g.grantee,
            "granted_by": g.granted_by,
            "can_revoke": g.granted_by == actor.user or actor.user == sheet_owner,
        }
        for g in grants
    ]
    return {
        "can_edit_column": can_edit_column,
        "can_change_structure": can_change_structure,
        "actor": actor.user,
        "can_add_column": can_add_column,
        # Platform-admin hint: the ONLY gate for the admin Roles panel. Follows the
        # "gate on a server hint, never re-derive ACL" rule (Feature: roles).
        "is_admin": bool(getattr(actor, "is_admin", False)),
        "subscribed": bool(subscription),
        "subscription": subscription,
        "branch_grants": branch_grants,
    }


# ---------------------------------------------------------------------------
# One whitelisted method per capability. Each is a thin shim that builds the
# capability params from named args and funnels into _dispatch. The capability
# id (registry key) is the camelCase name; the REST method is snake_case
# (ARCHITECTURE §8.1 shorthand `arbor.<method>`).
# ---------------------------------------------------------------------------
def _coerce(value: Any) -> Any:
    """Frappe delivers JSON bodies as strings on form-encoded calls; parse dicts
    /lists transparently so callers can pass real JSON."""
    if isinstance(value, str):
        try:
            return frappe.parse_json(value)
        except Exception:
            return value
    return value


@frappe.whitelist()
def add_node(sheet, parent=None, after=None, values=None):
    return _dispatch(
        "addNode",
        {"sheet": sheet, "parent": parent, "after": after, "values": _coerce(values) or {}},
    )


@frappe.whitelist()
def update_cell(sheet, node, column, value, base_version=None):
    params: dict[str, Any] = {
        "sheet": sheet, "node": node, "column": column, "value": _coerce(value),
    }
    # Feature 1 — optional optimistic-concurrency guard. Omitted -> blind write.
    if base_version is not None and base_version != "":
        params["base_version"] = frappe.utils.cint(base_version)
    return _dispatch("updateCell", params)


@frappe.whitelist()
def move_node(sheet, node, new_parent=None, after=None, expected_revision=None):
    params: dict[str, Any] = {
        "sheet": sheet, "node": node, "new_parent": new_parent, "after": after,
    }
    # Feature 1 — optional vanished-anchor guard for concurrent moves.
    if expected_revision is not None and expected_revision != "":
        params["expected_revision"] = expected_revision
    return _dispatch("moveNode", params)


@frappe.whitelist()
def delete_node(sheet, node, cascade=True):
    return _dispatch(
        "deleteNode",
        {"sheet": sheet, "node": node, "cascade": frappe.utils.cint(cascade) == 1
            if isinstance(cascade, (str, int)) else bool(cascade)},
    )


@frappe.whitelist()
def add_column(sheet, field, label, type, options=None, column_owner=None, is_label=False):
    return _dispatch(
        "addColumn",
        {
            "sheet": sheet,
            "field": field,
            "label": label,
            "type": type,
            "options": _coerce(options),
            "column_owner": column_owner,
            "is_label": frappe.utils.cint(is_label) == 1
            if isinstance(is_label, (str, int)) else bool(is_label),
        },
    )


@frappe.whitelist()
def update_column(sheet, column, patch=None):
    return _dispatch(
        "updateColumn", {"sheet": sheet, "column": column, "patch": _coerce(patch) or {}}
    )


@frappe.whitelist()
def delete_column(sheet, column):
    return _dispatch("deleteColumn", {"sheet": sheet, "column": column})


@frappe.whitelist()
def suggest_change(sheet, target_kind, operation, payload):
    return _dispatch(
        "suggestChange",
        {
            "sheet": sheet,
            "target_kind": target_kind,
            "operation": operation,
            "payload": _coerce(payload) or {},
        },
    )


@frappe.whitelist()
def suggest_changes(sheet, changes):
    """Propose a BATCH of changes as one Change Request (reviewed/applied
    atomically). ``changes`` = [{action, params}, ...]."""
    return _dispatch("suggestChanges", {"sheet": sheet, "changes": _coerce(changes) or []})


@frappe.whitelist()
def list_sheets():
    """The catalog of sheets for the home page (Sheet List): each ``{name,
    structural_owner, node_count}``. Read-only; emits no Tree Event.

    Node counts come from ONE grouped query over Tree Node (count per sheet), not
    N per-sheet COUNTs — so the list stays cheap even with thousands of sheets.
    The FE sorts by node_count desc (real sheets float above orphan empty test
    sheets) and offers a client-side text filter."""
    _actor()
    # One grouped count: {sheet_name: node_count}. Sheets with zero nodes simply
    # don't appear in this map and default to 0 below.
    counts = {
        row.sheet: row.node_count
        for row in frappe.get_all(
            "Tree Node",
            fields=["sheet", "count(name) as node_count"],
            group_by="sheet",
        )
        if row.sheet
    }
    out = []
    for s in frappe.get_all(
        "Tree Sheet",
        fields=["name", "structural_owner"],
        order_by="modified desc",
    ):
        out.append(
            {
                "name": s.name,
                "structural_owner": s.structural_owner,
                "node_count": int(counts.get(s.name, 0)),
            }
        )
    return out


@frappe.whitelist()
def list_change_requests(sheet, status="proposed"):
    """List the sheet's Change Requests (default: proposed) for the review inbox,
    each with its requester, resolved approver(s) and changes. Read-only."""
    actor = _actor()
    names = frappe.get_all(
        "Change Request",
        filters={"sheet": sheet, "status": status},
        order_by="creation asc",
        pluck="name",
    )
    repo = _repo()
    out = []
    for name in names:
        cr = repo.get_change_request(name)
        out.append(
            {
                "name": cr["name"],
                "requester": cr["requester"],
                "resolved_approver": cr.get("resolved_approver"),
                "status": cr["status"],
                "target_kind": cr.get("target_kind"),
                "operation": cr.get("operation"),
                "payload": cr.get("payload") or {},
                "changes": cr.get("changes") or [],
                "viewer_is_approver": _viewer_can_decide(cr, actor.user, repo),
            }
        )
    return out


def _viewer_can_decide(cr, user, repo):
    """Whether ``user`` may approve/reject at least one part of ``cr`` now."""
    from arbor.core.change_request import _column_editor_approvers, _reresolve_approver, _synthetic_item_cr

    items = cr.get("changes") or []
    if items:
        for it in items:
            syn = _synthetic_item_cr(cr, it)
            app, co = _reresolve_approver(syn, repo)
            if user in ({app} | set(co) | _column_editor_approvers(syn, repo)):
                return True
        return False
    app, co = _reresolve_approver(cr, repo)
    return user in ({app} | set(co) | _column_editor_approvers(cr, repo))


@frappe.whitelist()
def approve_change(change_request, comment=None):
    return _dispatch("approveChange", {"change_request": change_request, "comment": comment})


@frappe.whitelist()
def reject_change(change_request, comment=None):
    return _dispatch("rejectChange", {"change_request": change_request, "comment": comment})


@frappe.whitelist()
def withdraw_change(change_request, comment=None):
    return _dispatch("withdrawChange", {"change_request": change_request, "comment": comment})


@frappe.whitelist()
def subscribe(scope, target, event_types, delivery, subscriber=None, requires_ack=False):
    return _dispatch(
        "subscribe",
        {
            "scope": scope,
            "target": target,
            "event_types": _coerce(event_types) or [],
            "delivery": delivery,
            "subscriber": subscriber,
            "requires_ack": frappe.utils.cint(requires_ack) == 1
            if isinstance(requires_ack, (str, int)) else bool(requires_ack),
        },
    )


@frappe.whitelist()
def unsubscribe(subscription):
    return _dispatch("unsubscribe", {"subscription": subscription})


@frappe.whitelist()
def acknowledge(notification):
    return _dispatch("acknowledge", {"notification": notification})


# Human verbs for the in-app notification message (display only; the event type
# is the source of truth and is also returned for the UI to key on).
_NOTIF_VERB = {
    "NODE_CREATED": "added a node",
    "NODE_DELETED": "deleted a node",
    "NODE_MOVED": "moved a node",
    "NODE_VALUE_UPDATED": "updated a cell",
    "COLUMN_CONFIG_UPDATED": "changed a column",
    "CHANGE_PROPOSED": "proposed a change",
    "CHANGE_APPROVED": "approved a change",
    "CHANGE_REJECTED": "rejected a change",
    "SUBSCRIPTION_CHANGED": "changed a subscription",
    "DELEGATION_CHANGED": "changed a delegation",
    "IMPORT_COMPLETED": "completed an import",
}


@frappe.whitelist()
def list_notifications(sheet):
    """The viewer's in-app notifications for ``sheet`` (newest first) for the
    notification inbox. Each carries its event type, a human message, whether it
    requires acknowledgement, and whether the viewer has already acked. Read-only;
    emits no Tree Event."""
    actor = _actor()
    rows = frappe.get_all(
        "Notification",
        filters={"recipient": actor.user, "channel": "in-app"},
        fields=["name", "tree_event", "requires_ack"],
        order_by="creation desc",
    )
    out = []
    for r in rows:
        ev = (
            frappe.db.get_value(
                "Tree Event", r.tree_event, ["sheet", "type", "actor"], as_dict=True
            )
            if r.tree_event
            else None
        )
        if not ev or ev.sheet != sheet:
            continue
        acked = bool(
            frappe.db.exists("Acknowledgement", {"notification": r.name, "user": actor.user})
        )
        out.append(
            {
                "name": r.name,
                "event_type": ev.type,
                "message": f"{ev.actor} {_NOTIF_VERB.get(ev.type, ev.type)}",
                "requires_ack": bool(r.requires_ack),
                "acked": acked,
            }
        )
    return out


# Friendly PAST-TENSE verbs for the activity feed, keyed by EventType (the
# closed set of 11 — arbor.core.types.EventType). Mirrors the ``_NOTIF_VERB``
# idiom above but phrased for a change-history one-liner ("alice <verb> ..."). A
# node-/column-aware summary is built on top of these in ``list_activity``; an
# unmapped/unknown type falls back to the raw type string.
_ACTIVITY_VERB = {
    "NODE_CREATED": "added",
    "NODE_DELETED": "deleted",
    "NODE_MOVED": "moved",
    "NODE_VALUE_UPDATED": "updated",
    "COLUMN_CONFIG_UPDATED": "changed",
    "CHANGE_PROPOSED": "proposed a change",
    "CHANGE_APPROVED": "approved a change",
    "CHANGE_REJECTED": "rejected a change",
    "SUBSCRIPTION_CHANGED": "changed a subscription",
    "DELEGATION_CHANGED": "changed a delegation",
    "IMPORT_COMPLETED": "completed an import",
}


def _node_label(repo, sheet, label_col, node):
    """The human label of ``node`` (value of the sheet's label column), or the
    raw node id if it has no label. Labels are ALWAYS readable
    (``can_read_column`` short-circuits on ``is_label``), so no ACL gate here."""
    if not node:
        return None
    if label_col is not None:
        val = repo.get_value(node, label_col)
        if val:
            return val
    return node


def _readable_column_label(repo, sheet, actor, column):
    """``(label, readable)`` for ``column``: the column's display label when the
    viewer MAY read it (``arbor.core.acl.can_read_column``), else ``(None, False)``
    so the caller redacts the name. Never returns a cell VALUE — only the column
    schema label. A missing column resolves to ``(None, False)``."""
    if not column:
        return None, False
    try:
        col = repo.get_column(sheet, column)
    except Exception:
        return None, False
    if not can_read_column(repo, sheet, col, actor):
        return None, False
    return (getattr(col, "label", None) or col.field), True


def _activity_summary(ev_type, actor_name, payload, repo, sheet, actor, label_col):
    """Build the human one-liner for one Tree Event, resolving node/column LABELS
    via the repo and REDACTING any column the viewer cannot read (generic
    "a cell" / "a column" phrasing). NEVER includes a raw cell value."""
    payload = payload or {}
    verb = _ACTIVITY_VERB.get(ev_type, ev_type)

    if ev_type == "NODE_CREATED":
        label = _node_label(repo, sheet, label_col, payload.get("node"))
        return f"{actor_name} added {label}" if label else f"{actor_name} added a node"

    if ev_type == "NODE_DELETED":
        # The node row is gone by now; fall back to a generic phrasing.
        return f"{actor_name} deleted a node"

    if ev_type == "NODE_MOVED":
        label = _node_label(repo, sheet, label_col, payload.get("node"))
        return f"{actor_name} moved {label}" if label else f"{actor_name} moved a node"

    if ev_type == "NODE_VALUE_UPDATED":
        col_label, readable = _readable_column_label(
            repo, sheet, actor, payload.get("column")
        )
        node_label = _node_label(repo, sheet, label_col, payload.get("node"))
        if readable and node_label:
            return f"{actor_name} updated the {col_label} of {node_label}"
        if readable:
            return f"{actor_name} updated the {col_label}"
        if node_label:
            return f"{actor_name} updated a cell of {node_label}"
        return f"{actor_name} updated a cell"

    if ev_type == "COLUMN_CONFIG_UPDATED":
        col_label, readable = _readable_column_label(
            repo, sheet, actor, payload.get("column")
        )
        op = payload.get("op")
        action = {"add": "added", "delete": "deleted", "grant": "changed access to"}.get(
            op, "changed"
        )
        if readable:
            return f"{actor_name} {action} the {col_label} column"
        return f"{actor_name} {action} a column"

    # Axis-NONE / lifecycle events carry no node/column to redact — the verb IS
    # the whole sentence.
    return f"{actor_name} {verb}"


@frappe.whitelist()
def list_activity(sheet, limit=50):
    """The sheet's activity / change-history feed (newest first) — a READ SHIM
    (like ``list_change_requests`` / ``list_notifications``), NOT a registry
    capability, so it stays off the parity/registry surface.

    Returns one row per Tree Event on ``sheet`` (role/site-wide events have
    ``sheet=NULL`` and never appear here). Each row carries its event id, type
    (one of the 11 ``EventType`` values), actor + actor_type, ISO timestamp, the
    linked Change Request (or null), the resolved node LABEL / column LABEL when
    the viewer may see them, and a human one-liner ``summary``.

    Read-ACL: the feed is "what happened", never the data — it carries NO raw
    cell VALUES. A column the viewer cannot read (``arbor.core.acl.can_read_column``)
    is redacted: its name is dropped from both ``column`` and ``summary`` and the
    phrasing falls back to a generic "a cell" / "a column"."""
    actor = _actor()
    repo = _repo()
    label_col = next((c.name for c in repo.list_columns(sheet) if c.is_label), None)

    rows = frappe.get_all(
        "Tree Event",
        filters={"sheet": sheet},
        order_by="creation desc",
        limit_page_length=frappe.utils.cint(limit) if limit is not None else 50,
        fields=["name", "type", "actor", "actor_type", "change_request", "payload", "creation"],
    )
    out = []
    for r in rows:
        payload = r.payload
        if isinstance(payload, str):
            payload = frappe.parse_json(payload) if payload else {}
        payload = payload or {}

        node_id = payload.get("node")
        node_label = _node_label(repo, sheet, label_col, node_id) if node_id else None
        col_label, col_readable = _readable_column_label(
            repo, sheet, actor, payload.get("column")
        )
        out.append(
            {
                "event_id": r.name,
                "type": r.type,
                "actor": r.actor,
                "actor_type": r.actor_type,
                "timestamp": str(r.creation),
                "change_request": r.change_request or None,
                "node": node_label,
                "column": col_label if col_readable else None,
                "summary": _activity_summary(
                    r.type, r.actor, payload, repo, sheet, actor, label_col
                ),
            }
        )
    return out


@frappe.whitelist()
def delegate_branch(sheet, branch_root, grantee):
    return _dispatch(
        "delegateBranch", {"sheet": sheet, "branch_root": branch_root, "grantee": grantee}
    )


@frappe.whitelist()
def revoke_delegation(branch_grant):
    return _dispatch("revokeDelegation", {"branch_grant": branch_grant})


@frappe.whitelist()
def grant_column(sheet, column, column_owner=None, editors=None):
    return _dispatch(
        "grantColumn",
        {
            "sheet": sheet,
            "column": column,
            "column_owner": column_owner,
            "editors": _coerce(editors),
        },
    )


@frappe.whitelist()
def internal_reset(sheet, confirm=False):
    return _dispatch(
        "internalReset",
        {
            "sheet": sheet,
            "confirm": frappe.utils.cint(confirm) == 1
            if isinstance(confirm, (str, int)) else bool(confirm),
        },
    )


# ---------------------------------------------------------------------------
# Role management (Feature: roles). Mutations funnel through the SAME _dispatch
# (inheriting the error mapping: admin denial -> 403, terminal/duplicate -> 409,
# unknown role -> 404). Reads are thin whitelisted shims (like
# list_change_requests / list_notifications), not through the executor.
# ---------------------------------------------------------------------------
@frappe.whitelist()
def assign_role(role, grantee):
    return _dispatch("assignRole", {"role": role, "grantee": grantee})


@frappe.whitelist()
def revoke_role(role, grantee):
    return _dispatch("revokeRole", {"role": role, "grantee": grantee})


@frappe.whitelist()
def apply_for_role(role, justification=None):
    return _dispatch("applyForRole", {"role": role, "justification": justification})


@frappe.whitelist()
def approve_role_application(role_application, comment=None):
    return _dispatch(
        "approveRoleApplication", {"role_application": role_application, "comment": comment}
    )


@frappe.whitelist()
def reject_role_application(role_application, comment=None):
    return _dispatch(
        "rejectRoleApplication", {"role_application": role_application, "comment": comment}
    )


@frappe.whitelist()
def withdraw_role_application(role_application, comment=None):
    return _dispatch(
        "withdrawRoleApplication", {"role_application": role_application, "comment": comment}
    )


@frappe.whitelist()
def list_roles():
    """The role catalog with per-viewer flags. Feeds BOTH the admin assign picker
    and the user 'request a role' picker (which filters to applicable && active &&
    !viewer_holds && !viewer_has_open_application — enforced server-side too)."""
    actor = _actor()
    repo = _repo()
    held = set(
        frappe.get_all(
            "Arbor Role Grant", filters={"grantee": actor.user, "active": 1}, pluck="role"
        )
    )
    open_apps = set(
        frappe.get_all(
            "Arbor Role Application",
            filters={"requester": actor.user, "status": "proposed"},
            pluck="role",
        )
    )
    out = []
    for r in frappe.get_all(
        "Arbor Role",
        fields=["name", "role", "label", "description", "applicable", "active"],
        order_by="label asc",
    ):
        out.append(
            {
                "role": r.role,
                "label": r.label,
                "description": r.description,
                "applicable": bool(r.applicable),
                "active": bool(r.active),
                "viewer_holds": r.role in held,
                "viewer_has_open_application": r.role in open_apps,
            }
        )
    return out


@frappe.whitelist()
def list_role_grants(role=None, grantee=None):
    """Active role grants (optionally filtered). ``can_revoke`` gates the admin UI
    affordance; the server re-enforces admin on dispatch regardless."""
    actor = _actor()
    filters: dict[str, Any] = {"active": 1}
    if role:
        filters["role"] = role
    if grantee:
        filters["grantee"] = grantee
    rows = frappe.get_all(
        "Arbor Role Grant",
        filters=filters,
        fields=["name", "role", "grantee", "granted_by", "source"],
        order_by="creation asc",
    )
    return [
        {
            "name": g.name,
            "role": g.role,
            "grantee": g.grantee,
            "granted_by": g.granted_by,
            "source": g.source,
            "can_revoke": bool(actor.is_admin),
        }
        for g in rows
    ]


@frappe.whitelist()
def list_role_applications(status="proposed", requester=None):
    """Role applications for the admin inbox (``status=proposed``) or a user's own
    applications (``requester=<user>``). ``viewer_is_approver`` mirrors admin."""
    actor = _actor()
    filters: dict[str, Any] = {}
    if status:
        filters["status"] = status
    if requester:
        filters["requester"] = requester
    rows = frappe.get_all(
        "Arbor Role Application",
        filters=filters,
        fields=["name", "role", "requester", "status", "justification", "decided_by"],
        order_by="creation desc",
    )
    return [
        {
            "name": a.name,
            "role": a.role,
            "requester": a.requester,
            "status": a.status,
            "justification": a.justification,
            "decided_by": a.decided_by,
            "viewer_is_approver": bool(actor.is_admin),
        }
        for a in rows
    ]
