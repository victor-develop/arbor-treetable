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

import base64
import ipaddress
import re
import secrets as _secrets
import socket
from typing import Any, Optional
from urllib.parse import urlsplit

import frappe
from frappe import _

from arbor.core import executor, registry
from arbor.core.agent_scope import (
    AgentScope,
    ScopeError,
    authorize_scope,
    generate_token,
    hash_token,
)
from arbor.core.skill import render_skill_md
from arbor.core.acl import (
    can_read_column,
    resolve_column_approvers,
    resolve_structural_approver,
    visible_columns,
)
from arbor.core.explore import (
    CellBudgetExceededError,
    SheetTooLargeError,
    process_rule_views,
    readable_column_label as _readable_column_label,
)
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
def _is_admin_user(user: str) -> bool:
    """Platform-admin (System Manager) signal for a user — the ONLY admin gate the
    framework-free core cannot compute. Administrator is always admin."""
    return user == "Administrator" or "System Manager" in set(frappe.get_roles(user))


def _actor(repo: Optional[FrappeRepository] = None) -> Actor:
    """The acting identity — the authenticated Frappe user, PLUS a traceable
    impersonation overlay (Area 1).

    Ordering is load-bearing (impersonation-comments-process.md aclImplications):
      1. read ``frappe.session.user`` = the REAL, authenticated principal — we
         NEVER call ``frappe.set_user`` to "become" someone, so the framework
         boundary keeps telling the truth;
      2. compute ``real_is_admin`` from the REAL user's roles;
      3. look up the active Arbor Impersonation Session for the real user;
      4. if present AND real_is_admin -> build Actor(user=impersonated,
         real_user=real, impersonated_as=impersonated, is_admin recomputed from the
         IMPERSONATED user so the admin genuinely experiences that user's
         affordances); if present but the real user is NO LONGER admin -> force-end
         the overlay (fail-safe: you cannot keep a foreign identity by losing
         admin) and act as the real user; else a normal, non-impersonated Actor.

    begin/end authority is gated on ``real_is_admin`` computed HERE (before the
    overlay is applied), so an impersonated non-admin identity can never start a
    nested impersonation or escalate.
    """
    user = frappe.session.user
    if not user or user == "Guest":
        # Defense in depth; Frappe normally rejects unauthenticated calls to a
        # whitelisted method (no allow_guest) with 403/401 before we get here.
        raise frappe.AuthenticationError(_("Authentication required"))

    real_user = user
    real_is_admin = _is_admin_user(real_user)

    repo = repo or _repo()
    session = repo.get_active_impersonation(real_user)
    if session:
        impersonated = session["impersonated_user"]
        if real_is_admin and impersonated and impersonated != real_user:
            # Effective identity = the impersonated user; ACL/executor run against
            # it. is_admin is recomputed from the impersonated user (so an admin
            # acting as a plain user sees exactly that user's affordances).
            return Actor(
                user=impersonated,
                actor_type=ActorType.HUMAN,
                is_admin=_is_admin_user(impersonated),
                real_user=real_user,
                impersonated_as=impersonated,
            )
        # Fail-safe: an overlay persisted for a user who is no longer admin (grant
        # revoked mid-session) must NOT grant lingering foreign identity. Force-end
        # it and act as the real user.
        if not real_is_admin:
            repo.end_impersonation(real_user)

    return Actor(user=real_user, actor_type=ActorType.HUMAN, is_admin=real_is_admin)


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


def _actor_real(repo: Optional[FrappeRepository] = None) -> Actor:
    """The REAL, authenticated principal WITHOUT any impersonation overlay — with
    ``is_admin`` computed from the real user's roles (Area 1).

    The begin/end impersonation control caps are gated on the REAL user's admin
    (impersonation-comments-process.md aclImplications: "computed before the
    overlay is applied"). ``_actor()`` returns the EFFECTIVE (possibly impersonated,
    non-admin) identity, which would wrongly block ``endImpersonation`` while an
    overlay is live; so those two shims dispatch AS the real user instead. This is
    the ONLY place the overlay is bypassed, and it can never escalate: it grants
    admin only when the real session user genuinely holds System Manager."""
    user = frappe.session.user
    if not user or user == "Guest":
        raise frappe.AuthenticationError(_("Authentication required"))
    return Actor(user=user, actor_type=ActorType.HUMAN, is_admin=_is_admin_user(user))


def _token_secret() -> Optional[str]:
    """The site key that keys the Agent-Token HMAC (defense if the DB leaks).

    Prefers the site ``encryption_key``; falls back to a conf ``secret``. When
    neither exists the hash degrades to a plain SHA-256 of the (already high-
    entropy) token — still safe, just not DB-leak-resistant."""
    try:
        return frappe.local.conf.get("encryption_key") or frappe.conf.get("secret")
    except Exception:  # pragma: no cover - defensive
        return None


def _agent_scope_from_request() -> Optional[AgentScope]:
    """Resolve an ``X-Arbor-Agent-Token`` header to its :class:`AgentScope`.

    Returns ``None`` when the header is absent (the first-party web app and the
    internal agent never send it — they authenticate by session). A header that
    is present but invalid / revoked / expired / not owned by the authenticated
    user is a hard 401/403; it never silently degrades to full access.
    """
    # NB: ``frappe.request`` is a werkzeug LocalProxy — it is never None (accessing
    # it out of a request context raises instead). Guard on ``frappe.local.request``,
    # which getattr resolves to None when unbound. Without this, dispatch OUTSIDE an
    # HTTP request (background jobs, scheduler, bench, the in-process agent lane)
    # would raise RuntimeError("object is not bound") on the header read.
    if getattr(frappe.local, "request", None) is None:
        return None
    token = frappe.get_request_header("X-Arbor-Agent-Token")
    if not token:
        return None

    token_hash = hash_token(token, secret=_token_secret())
    name = frappe.db.get_value("Arbor Agent Token", {"token_hash": token_hash}, "name")
    if not name:
        frappe.local.response["http_status_code"] = 401
        frappe.throw(_("Invalid Arbor Agent Token"), exc=frappe.AuthenticationError)

    doc = frappe.get_doc("Arbor Agent Token", name)
    if not doc.is_live():
        frappe.local.response["http_status_code"] = 401
        frappe.throw(_("Arbor Agent Token is revoked or expired"), exc=frappe.AuthenticationError)
    # The token may only ADD scope to its OWN user — never let A's key + B's token
    # act as B. The API key (Frappe-native) is the identity; the token is a ceiling.
    if doc.user != frappe.session.user:
        frappe.local.response["http_status_code"] = 403
        frappe.throw(_("Agent token does not belong to the authenticated user"), exc=frappe.PermissionError)

    # Best-effort last-used stamp; never block the call on it.
    try:
        frappe.db.set_value(
            "Arbor Agent Token", name, "last_used_at", frappe.utils.now_datetime(), update_modified=False
        )
    except Exception:  # pragma: no cover - defensive
        pass
    return doc.to_scope()


def _enforce_agent_scope(action_id: str, params: dict[str, Any]) -> None:
    """Apply any Arbor Agent Token scope to ``action_id``/``params``, else no-op.

    Called at EVERY capability entrypoint — both the generic ``_dispatch`` and the
    bespoke ``get_sheet_snapshot`` (which reaches the executor directly and would
    otherwise sidestep the sheet restriction). A scope violation is a hard 403;
    it never degrades to a Change Request.
    """
    scope = _agent_scope_from_request()
    if scope is None:
        return
    try:
        authorize_scope(scope, action_id, params or {})
    except ScopeError as exc:
        frappe.local.response["http_status_code"] = 403
        raise frappe.PermissionError(str(exc)) from exc


def _dispatch(
    action_id: str, params: dict[str, Any], actor: Optional[Actor] = None
) -> dict[str, Any]:
    """The ONE funnel: every capability method routes here → core.execute_action.

    Translates core/adapter exceptions into Frappe's HTTP status conventions.
    ``actor`` defaults to the effective identity (``_actor()``, which applies any
    impersonation overlay); the begin/end impersonation shims pass the REAL-user
    actor so their admin gate is the real principal's, never the impersonated one.
    """
    repo = _repo()
    if actor is None:
        actor = _actor(repo)
    sink = _sink()

    # External-agent scope gate (two-tier auth): when the request carries an Arbor
    # Agent Token, its mode/sheets constrain the call BEFORE the executor. A scope
    # violation is a hard 403 (like a denied control action) — it NEVER degrades
    # to a Change Request. Absent header → no-op (first-party / internal path).
    _enforce_agent_scope(action_id, params)
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


# ---------------------------------------------------------------------------
# External LLM agent: the crawlable contract + scoped access tokens
# ("two-tier auth" — internal chat uses the session; external agents use a
# Frappe API key + an optional Arbor Agent Token down-scope).
# ---------------------------------------------------------------------------
@frappe.whitelist(allow_guest=True)
def skill_md() -> None:
    """``GET /api/method/arbor.skill_md`` → the generated ``skill.md`` contract.

    Public (allow_guest): the doc describes the API SHAPE + capability catalog
    only, never any tenant data, so the crawl never needs a credential — the
    secret is spent only on real API calls. Rendered from the registry, so it is
    always in sync. Returned as raw ``text/markdown``.
    """
    md = render_skill_md(frappe.utils.get_url())
    frappe.local.response["type"] = "download"
    frappe.local.response["filename"] = "skill.md"
    frappe.local.response["filecontent"] = md
    frappe.local.response["content_type"] = "text/markdown; charset=utf-8"


def _bootstrap_prompt(token: str, mode: str, sheets: Optional[list]) -> str:
    """The ready-to-paste prompt handing an external agent everything it needs."""
    base = frappe.utils.get_url().rstrip("/")
    scope_note = (
        "You have READ-ONLY access."
        if mode == "read"
        else "You may read and write; a write you aren't authorized for becomes a Change Request (not an error)."
    )
    sheet_note = f" You are limited to these sheets: {', '.join(sheets)}." if sheets else ""
    return (
        f"You can operate my Arbor data through its HTTP API. "
        f"First read the contract: {base}/api/method/arbor.skill_md\n"
        f"API base: {base}/api/method/arbor.\n"
        f"On every request send header  X-Arbor-Agent-Token: {token}\n"
        f"plus the Frappe API key I gave you as  Authorization: token <key>:<secret>\n"
        f"{scope_note}{sheet_note}\n"
        f"Begin by calling arbor.list_sheets, then follow the discovery flow in skill.md."
    )


@frappe.whitelist()
def issue_agent_token(
    label: Optional[str] = None,
    mode: str = "write",
    sheets: Optional[Any] = None,
    ttl_days: int = 30,
) -> dict[str, Any]:
    """Mint an Arbor Agent Token for the CURRENT user and return it exactly once.

    ``mode`` ∈ {read, write}; ``sheets`` optional list restricting the token to
    those sheet ids; ``ttl_days`` sets the expiry. The plaintext secret is
    returned ONCE (never stored — only its keyed hash is) alongside a
    ready-to-paste ``bootstrap_prompt``.
    """
    actor = _actor_real()  # issue for the REAL principal; ignore any impersonation
    if mode not in ("read", "write"):
        frappe.local.response["http_status_code"] = 400
        frappe.throw(_("mode must be 'read' or 'write'"), exc=frappe.ValidationError)

    sheets_list: Optional[list] = None
    coerced = _coerce(sheets) if sheets else None
    if isinstance(coerced, str):
        sheets_list = [coerced]
    elif isinstance(coerced, list) and coerced:
        sheets_list = [str(s) for s in coerced]

    plaintext = generate_token()
    expires_on = (
        frappe.utils.add_days(frappe.utils.now_datetime(), int(ttl_days)) if ttl_days else None
    )
    doc = frappe.get_doc(
        {
            "doctype": "Arbor Agent Token",
            "label": label or "external agent",
            "user": actor.user,
            "mode": mode,
            "sheets": frappe.as_json(sheets_list) if sheets_list else None,
            "token_hash": hash_token(plaintext, secret=_token_secret()),
            "expires_on": expires_on,
        }
    )
    doc.insert(ignore_permissions=True)
    return {
        "token_id": doc.name,
        "token": plaintext,  # shown ONCE; not recoverable
        "mode": mode,
        "sheets": sheets_list,
        "expires_on": str(expires_on) if expires_on else None,
        "bootstrap_prompt": _bootstrap_prompt(plaintext, mode, sheets_list),
    }


@frappe.whitelist()
def revoke_agent_token(token_id: str) -> dict[str, Any]:
    """Revoke one Arbor Agent Token (the issuer or an admin). Immediate kill."""
    doc = frappe.get_doc("Arbor Agent Token", token_id)
    if doc.user != _actor_real().user and not _is_admin_user(frappe.session.user):
        raise frappe.PermissionError(_("not your token"))
    doc.revoked = 1
    doc.save(ignore_permissions=True)
    return {"token_id": token_id, "revoked": True}


@frappe.whitelist()
def list_agent_tokens() -> list[dict[str, Any]]:
    """List the CURRENT user's Agent Tokens (metadata only — never the hash)."""
    user = _actor_real().user
    return frappe.get_all(
        "Arbor Agent Token",
        filters={"user": user},
        fields=["name", "label", "mode", "sheets", "expires_on", "revoked", "last_used_at"],
        order_by="creation desc",
    )


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
    repo = _repo()
    if actor is None:
        actor = _actor(repo)

    # This endpoint reaches the executor directly (not via _dispatch), so apply
    # the agent-token scope here too — otherwise a sheet-scoped token could read
    # a sheet outside its scope through the bulk snapshot.
    _enforce_agent_scope("getSheetSnapshot", {"sheet": sheet})

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

    # Per-cell comment SUMMARY (Area 2): {(node, column): {open, resolved,
    # unresolved}} built ONLY over the read-ACL-visible columns (same guarantee as
    # pending marks / the values loop) so a comment on a forbidden column never
    # leaks into another viewer's grid. ONE grouped query over Arbor Cell Comment,
    # never N+1 per cell.
    comments = _cell_comment_marks(sheet, {c.name for c in columns}, node_names)

    acl_hints = _acl_hints(actor, repo, sheet, columns, nodes)
    snap = serialize_snapshot(
        sheet_view, columns, nodes, values, acl_hints, versions=versions, pending=pending
    )
    # Fold the per-cell comment summary onto each node under a sparse ``comments``
    # map (mirrors how ``pending`` is threaded); nodes with no comments carry none.
    if comments:
        by_node: dict[str, dict[str, dict[str, int]]] = {}
        for (node_name, col_name), summary in comments.items():
            by_node.setdefault(node_name, {})[col_name] = summary
        for n in snap.get("nodes", []):
            cmap = by_node.get(n.get("name"))
            if cmap:
                n["comments"] = cmap
    # Impersonation viewer block (Area 1): the pure serializer's ``viewer`` is
    # framework-free and carries only the shared affordances; overlay the
    # "act as" hints here (api.py owns the impersonation surface) so the banner +
    # "stop impersonating" control render off snapshot hints with NO ACL
    # re-derivation. effective_user == actor.user (the identity the grid renders
    # for); real_user is the authenticated admin when impersonating (else null).
    snap["viewer"]["impersonating"] = acl_hints.get("impersonating", False)
    snap["viewer"]["real_user"] = acl_hints.get("real_user")
    snap["viewer"]["effective_user"] = acl_hints.get("effective_user", actor.user)
    return snap


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


def _cell_comment_marks(
    sheet: str, visible_col_names: set[str], node_names: set[str]
) -> dict[tuple[str, str], dict[str, int]]:
    """``{(node, column): {open, resolved, unresolved}}`` — the per-cell comment
    summary the grid renders a glyph off (Area 2).

    Counts THREAD ROOTS only (a cell hosts one badge per thread, not per reply):
    ``open``/``unresolved`` = roots with ``resolved=0``, ``resolved`` = roots with
    ``resolved=1`` (a deleted-but-tombstoned root still counts — it is still a
    thread). Built ONLY over the read-ACL-visible columns + present nodes, so a
    comment on a forbidden column can never leak (same guarantee as
    ``_pending_cell_marks`` and the values loop). ONE grouped query, never N+1.
    """
    marks: dict[tuple[str, str], dict[str, int]] = {}
    if not visible_col_names or not node_names:
        return marks
    # Thread ROOTS only: a root has thread_root NULL/empty. frappe stores an unset
    # Link as NULL; ["is", "not set"] matches it portably.
    rows = frappe.get_all(
        "Arbor Cell Comment",
        filters={"sheet": sheet, "thread_root": ["is", "not set"]},
        fields=["node", "column", "resolved"],
    )
    for r in rows:
        if r.column not in visible_col_names or r.node not in node_names:
            continue
        key = (r.node, r.column)
        summary = marks.setdefault(key, {"open": 0, "resolved": 0, "unresolved": 0})
        if r.resolved:
            summary["resolved"] += 1
        else:
            summary["open"] += 1
            summary["unresolved"] += 1
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
def get_sheet_definition(sheet):
    """The sheet's DEFINITION (schema + governance) — a CHEAP read with NO row or
    cell data. Powers the unified Sheet Settings panel AND the workspace agent's
    "configure an existing sheet" flow off the SAME capability.

    ``GET /api/method/arbor.get_sheet_definition?sheet=…`` →
    ``{kind:"read", data:{sheet:{name,title,structural_owner,label_column,settings},
    columns:[{name,field,label,type,column_owner,editors,is_label,options?,can_edit}],
    process:{enabled,row_scope,rules:[ProcessRuleView...]}|null}}``. Columns are
    read-ACL FILTERED (a column the viewer cannot read is omitted); the process
    rule labels/keys are redacted per unreadable column, and expected-column owners
    are LIVE-resolved. Routed through the ONE executor (surface parity) — never
    reads a Tree Node Value. Unlike ``get_process`` this is a registry capability,
    so it is reachable by the agent as ``getSheetDefinition`` too."""
    return _dispatch("getSheetDefinition", {"sheet": sheet})


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
        # Impersonation viewer block (Area 1): powers the persistent "acting as"
        # banner + the "stop impersonating" control off snapshot hints, with NO ACL
        # re-derivation. effective_user == actor.user (the identity the grid is
        # rendered for); real_user is the authenticated admin when impersonating
        # (else null); impersonating flips the banner.
        "impersonating": bool(getattr(actor, "is_impersonated", False)),
        "real_user": actor.real_user if getattr(actor, "is_impersonated", False) else None,
        "effective_user": actor.user,
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


# Process management (Feature: process). Thin named shims for the LLM-exposed
# process capabilities so the registry→REST reachability contract holds; the
# dashboard/inbox read shims + the dispatch consumer land in the process wave.
@frappe.whitelist()
def define_process(sheet, rules, title=None, row_scope=None):
    """Upsert the sheet's process DAG from a rule set (process DAG).

    ``rules`` = [{rule_key?, trigger_kind, trigger_column?, trigger_op,
    expected_columns:[...], within_seconds?, notify_on_expect?, label?}]. Funnels
    into the ONE executor (registry capability ``defineProcess``); a cyclic /
    self-looping / structurally-invalid set raises a 400 via the pure
    ``process_graph.validate_rules``."""
    params: dict[str, Any] = {"sheet": sheet, "rules": _coerce(rules) or [], "title": title}
    if row_scope is not None:
        params["row_scope"] = row_scope
    return _dispatch("defineProcess", params)


@frappe.whitelist()
def enable_process(sheet):
    return _dispatch("enableProcess", {"sheet": sheet})


@frappe.whitelist()
def disable_process(sheet):
    return _dispatch("disableProcess", {"sheet": sheet})


@frappe.whitelist()
def start_process_run(sheet, node):
    return _dispatch("startProcessRun", {"sheet": sheet, "node": node})


# Process READ shims (NOT registry capabilities — like list_change_requests /
# list_activity): the definition, the kanban/flow dashboard aggregate, and the
# per-stage run drill-down. Read-ACL: a stage column the viewer cannot read
# (arbor.core.acl.can_read_column) has its LABEL redacted (structural stage
# position/counts are always safe); run rows never carry cell VALUES.
@frappe.whitelist()
def get_process(sheet):
    """The sheet's Arbor Process definition (enabled or not), or ``None``.

    ``GET /api/method/arbor.get_process?sheet=…`` →
    ``{name, sheet, title, enabled, row_scope, sla_breach_notify,
    rules:[ProcessRuleView]}`` where each rule carries its trigger + expected
    LABELS (redacted null for a column the viewer cannot read; never the field key),
    the LIVE-resolved ``expected_owners`` per expected column, and the raw
    ``expected_columns`` (redacted to ``None`` per element when unreadable)."""
    actor = _actor()
    repo = _repo()
    process = repo.get_process(sheet)
    if process is None:
        return None
    return _process_view_dict(repo, actor, process)


def _process_view_dict(repo, actor, process):
    # The per-rule ProcessRuleView shape (read-ACL redaction + owner resolution) is
    # ONE builder in the pure core, reused by getSheetDefinition; here we just wrap
    # it with the process-level metadata.
    return {
        "name": process.name,
        "sheet": process.sheet,
        "title": process.title,
        "enabled": bool(process.enabled),
        "row_scope": process.row_scope,
        "sla_breach_notify": bool(process.sla_breach_notify),
        "rules": process_rule_views(repo, process.sheet, actor, process),
    }


@frappe.whitelist()
def process_dashboard(sheet):
    """The flow metrics for the sheet's process DAG (process DAG).

    ``GET /api/method/arbor.process_dashboard?sheet=…`` →
    ``{edges:[{rule_key, from_kind, from_column, from_label, to_column, to_label,
    within_seconds, pending_count, breached_count, satisfied_count,
    avg_open_to_satisfy_seconds}], total_active, total_completed, throughput}`` —
    the pure ``arbor.core.process.dashboard_aggregate`` over every run of the
    sheet. Returns ``None`` when no process is defined. Each edge's trigger/expected
    column LABELS are redacted (null) when the viewer cannot read the column (and
    the column key is nulled with it); the structural counts are always safe. A row
    (START) trigger carries ``from_column=None`` and ``from_label=None``."""
    from arbor.core import process as _process
    actor = _actor()
    repo = _repo()
    process = repo.get_process(sheet)
    if process is None:
        return None
    runs = repo.list_process_runs(sheet)
    agg = _process.dashboard_aggregate(process, runs)
    for edge in agg.get("edges", []):
        # from_column is None for a row (START) trigger — its label stays None.
        if edge.get("from_column"):
            from_label, from_readable = _readable_column_label(
                repo, sheet, actor, edge.get("from_column")
            )
            edge["from_label"] = from_label
            if not from_readable:
                edge["from_column"] = None
        else:
            edge["from_label"] = None
        to_label, to_readable = _readable_column_label(
            repo, sheet, actor, edge.get("to_column")
        )
        edge["to_label"] = to_label
        if not to_readable:
            edge["to_column"] = None
    return agg


@frappe.whitelist()
def list_process_runs(sheet, rule_key=None, column=None, status=None):
    """The sheet's process runs (optionally filtered) — the flow/edge drill-down.

    ``GET /api/method/arbor.list_process_runs?sheet=…&status=active`` →
    ``[{name, process, sheet, node, node_label, status, started_at, completed_at,
    expectations:[{rule_key, expected_column, to_label, opened_at, satisfied_at,
    due_at, breached}]}]``. Optional ``rule_key`` / ``column`` narrow to runs that
    carry an expectation on that rule / expected column (the edge drill-down).
    Carries node/column LABELS (redacted when unreadable), NEVER a cell VALUE."""
    actor = _actor()
    repo = _repo()
    label_col = next((c.name for c in repo.list_columns(sheet) if c.is_label), None)
    out = []
    for run in repo.list_process_runs(sheet, status=status):
        exps = run.get("expectations") or []
        # Edge drill-down filters: keep the run iff it carries a matching expectation.
        if rule_key is not None and not any(e.get("rule_key") == rule_key for e in exps):
            continue
        if column is not None and not any(e.get("expected_column") == column for e in exps):
            continue
        expectations = []
        for e in exps:
            label, readable = _readable_column_label(
                repo, sheet, actor, e.get("expected_column")
            )
            expectations.append(
                {
                    "rule_key": e.get("rule_key"),
                    "expected_column": e.get("expected_column") if readable else None,
                    "to_label": label,
                    "opened_at": e.get("opened_at"),
                    "satisfied_at": e.get("satisfied_at"),
                    "due_at": e.get("due_at"),
                    "breached": bool(e.get("breached")),
                }
            )
        out.append(
            {
                "name": run.get("name"),
                "process": run.get("process"),
                "sheet": run.get("sheet"),
                "node": run.get("node"),
                "node_label": _node_label(repo, sheet, label_col, run.get("node")),
                "status": run.get("status"),
                "started_at": run.get("started_at"),
                "completed_at": run.get("completed_at"),
                "expectations": expectations,
            }
        )
    return out


@frappe.whitelist()
def inbox():
    """The viewer's in-app notifications ACROSS ALL sheets — the per-user Inbox
    page (Area 3). Generalizes ``list_notifications`` (which is sheet-scoped) by
    dropping the sheet filter and resolving each notification's context + a deep
    link.

    Self-scoped to ``_actor().user`` (like ``list_cell_drafts``); a user only ever
    sees their OWN notifications, and ``acknowledge`` already enforces
    ``recipient == actor``.

    ``GET /api/method/arbor.inbox`` → ``[{name, source, event_type, message,
    sheet, node, requires_ack, acked}]`` newest first. tree_event rows resolve
    their {sheet, type, actor} from the Tree Event; comment rows from the linked
    Arbor Cell Comment; process/sla rows resolve their actionable {sheet, node,
    stage} from the viewer's LIVE-owned Process Run stages (the Notification schema
    carries no process link, so context is derived from the runs the viewer is a
    responsible owner of — exactly the work an inbox surfaces)."""
    actor = _actor()
    repo = _repo()
    rows = frappe.get_all(
        "Notification",
        filters={"recipient": actor.user, "channel": "in-app"},
        fields=["name", "source", "tree_event", "comment", "requires_ack", "creation"],
        order_by="creation desc",
    )
    # Resolve the viewer's live process work ONCE (context for process/sla rows),
    # newest run first so successive process notifications map to distinct runs.
    process_ctx = _inbox_process_context(repo, actor)
    proc_cursor = 0
    out = []
    for r in rows:
        source = r.source or "tree_event"
        if source in ("process", "sla"):
            ctx = process_ctx[proc_cursor] if proc_cursor < len(process_ctx) else None
            proc_cursor += 1
            row = _inbox_process_row(r, source, ctx, actor)
        elif source == "comment":
            row = _inbox_comment_row(r, repo, actor)
        else:
            row = _inbox_tree_event_row(r, actor)
        if row is not None:
            out.append(row)
    return out


def _inbox_tree_event_row(r, actor):
    ev = (
        frappe.db.get_value(
            "Tree Event", r.tree_event, ["sheet", "type", "actor", "payload"], as_dict=True
        )
        if r.tree_event
        else None
    )
    if not ev:
        return None
    acked = bool(
        frappe.db.exists("Acknowledgement", {"notification": r.name, "user": actor.user})
    )
    payload = ev.payload
    if isinstance(payload, str):
        payload = frappe.parse_json(payload) if payload else {}
    return {
        "name": r.name,
        "source": "tree_event",
        "event_type": ev.type,
        "message": f"{ev.actor} {_NOTIF_VERB.get(ev.type, ev.type)}",
        "sheet": ev.sheet,
        "node": (payload or {}).get("node"),
        "requires_ack": bool(r.requires_ack),
        "acked": acked,
    }


def _inbox_comment_row(r, repo, actor):
    if not r.comment:
        return None
    c = frappe.db.get_value(
        "Arbor Cell Comment", r.comment, ["sheet", "node", "author"], as_dict=True
    )
    if not c:
        return None
    acked = bool(
        frappe.db.exists("Acknowledgement", {"notification": r.name, "user": actor.user})
    )
    return {
        "name": r.name,
        "source": "comment",
        "event_type": "COMMENT_ADDED",
        "message": f"{c.author} commented on a cell",
        "sheet": c.sheet,
        "node": c.node,
        "requires_ack": bool(r.requires_ack),
        "acked": acked,
    }


def _inbox_process_row(r, source, ctx, actor):
    acked = bool(
        frappe.db.exists("Acknowledgement", {"notification": r.name, "user": actor.user})
    )
    if source == "sla":
        event_type = "PROCESS_SLA_DUE"
        verb = "a process stage is overdue"
    else:
        event_type = "PROCESS_STAGE_ASSIGNED"
        verb = "a process stage is waiting on you"
    return {
        "name": r.name,
        "source": source,
        "event_type": event_type,
        "message": verb,
        "sheet": ctx.get("sheet") if ctx else None,
        "node": ctx.get("node") if ctx else None,
        "requires_ack": bool(r.requires_ack),
        "acked": acked,
    }


def _inbox_process_context(repo, actor):
    """The viewer's process work across all sheets: for every run (active OR
    completed) that NOTIFIED the viewer as an expected-column owner, a
    ``{sheet, node, rule_key, column}`` deep-link context. Newest run first
    (mirrors the notification ordering) so successive process notifications map to
    distinct runs.

    Derived from the runs (not a Notification link) because the Notification
    schema carries no process reference. A recipient is matched via an expectation's
    ``notified_owner`` ledger (who was notified at open time) UNION the LIVE
    ``resolve_column_approvers`` of any OPEN (unsatisfied, un-breached) expectation's
    expected column — so both a live assignment and an already-completed run whose
    expectation notified the viewer resolve to a deep link."""
    ctx = []
    run_names = frappe.get_all(
        "Arbor Process Run",
        fields=["name"],
        order_by="creation desc",
    )
    for rn in run_names:
        run = frappe.get_doc("Arbor Process Run", rn["name"])
        matched = None
        for e in run.get("expectations") or []:
            notified = (e.get("notified_owner") or "").split(",") if e.get("notified_owner") else []
            live_owner = (
                run.status == "active"
                and not e.get("satisfied_at")
                and not e.get("breached")
                and actor.user in resolve_column_approvers(repo, run.sheet, e.expected_column)
            )
            if actor.user in notified or live_owner:
                matched = {"rule_key": e.rule_key, "column": e.expected_column}
                break
        if matched is not None:
            ctx.append(
                {"sheet": run.sheet, "node": run.node, **matched}
            )
    return ctx


# Impersonation ("act as") — traceable, admin-gated overlay (Area 1). Thin shims
# funnel through the SAME _dispatch → executor as every capability, so the admin
# gate (_ADMIN_IMPERSONATION_CAPS), the AuthorizationError→403 mapping, and
# surface parity all hold. begin/end emit NO Tree Event — the Arbor Impersonation
# Session row IS the audit record. Authority is the REAL user's admin: _actor()
# computes it BEFORE applying any overlay, so an impersonated non-admin can never
# begin/end.
@frappe.whitelist()
def begin_impersonation(impersonated_user, reason=None):
    """Start acting as ``impersonated_user`` (admin only → 403 otherwise).

    ``POST /api/method/arbor.begin_impersonation {impersonated_user, reason?}`` →
    ``{kind:'executed', data:{impersonating:<user>, session:<id>}}``.
    """
    return _dispatch(
        "beginImpersonation",
        {"impersonated_user": impersonated_user, "reason": reason},
        actor=_actor_real(),
    )


@frappe.whitelist()
def end_impersonation():
    """Stop the active "act as" overlay for the real (authenticated) user.

    ``POST /api/method/arbor.end_impersonation`` →
    ``{kind:'executed', data:{impersonating:null}}``. Idempotent."""
    return _dispatch("endImpersonation", {}, actor=_actor_real())


@frappe.whitelist()
def create_sheet(name=None, title=None, label="Item"):
    """Create a brand-new sheet — now the registry ``createSheet`` CAPABILITY,
    dispatched through the ONE ``execute_action`` (surface parity for free) rather
    than a standalone frappe mutation. The prior direct-write logic now lives in
    ``FrappeRepository.create_sheet`` (the Repository port the createSheet handler
    calls).

    ``POST /api/method/arbor.create_sheet {name?, title?, label?}`` ->
    ``{"sheet": <created sheet name>}`` (the created id is also under the standard
    ``data`` envelope; the top-level ``sheet`` key is kept for FE/back-compat).

    Any authenticated non-Guest user may create a sheet (gated in the executor's
    createSheet control path — permissive "authenticated", NOT admin-only); the
    creator becomes its ``structural_owner`` and owns the default LABEL Tree Column
    (``is_label``, ``type=text``) so the new sheet is immediately usable.

    A duplicate ``name`` is a storage conflict -> HTTP 409 (adapter ConflictError);
    a blank name AND title is a bad request -> 400 (core ValueError). The legacy
    ``label`` arg maps to the capability's ``label_column`` (the default label
    column's text); ``name``/``title`` are both optional (title falls back to name)."""
    name = (name or "").strip() if isinstance(name, str) else (str(name).strip() if name else "")
    title = (title or "").strip() if isinstance(title, str) else None
    body = _dispatch(
        "createSheet",
        {
            "title": title or name,
            "name": name or None,
            "label_column": label,
        },
    )
    # Keep the historical top-level {"sheet": ...} shape for the SheetList FE + the
    # existing bench test, alongside the standard executor envelope.
    body["sheet"] = (body.get("data") or {}).get("sheet")
    return body


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
        fields=["name", "source", "tree_event", "comment", "requires_ack"],
        order_by="creation desc",
    )
    out = []
    for r in rows:
        # Branch on ``source`` to resolve the owning sheet. Comment notifications
        # carry ``tree_event=NULL`` (a comment is not a Tree Event), so they must
        # resolve their sheet from the linked Arbor Cell Comment instead of the
        # tree_event join — a miss here would silently hide the comment inbox.
        source = r.source or "tree_event"
        if source == "comment":
            row = _comment_notification_row(r, sheet, actor)
        else:
            row = _tree_event_notification_row(r, sheet)
        if row is not None:
            out.append(row)
    return out


def _tree_event_notification_row(r, sheet):
    """Render a ``source='tree_event'`` Notification for ``sheet``, or None when it
    belongs to another sheet / has no resolvable event."""
    ev = (
        frappe.db.get_value(
            "Tree Event", r.tree_event, ["sheet", "type", "actor"], as_dict=True
        )
        if r.tree_event
        else None
    )
    if not ev or ev.sheet != sheet:
        return None
    acked = bool(
        frappe.db.exists("Acknowledgement", {"notification": r.name, "user": frappe.session.user})
    )
    return {
        "name": r.name,
        "event_type": ev.type,
        "message": f"{ev.actor} {_NOTIF_VERB.get(ev.type, ev.type)}",
        "requires_ack": bool(r.requires_ack),
        "acked": acked,
    }


def _comment_notification_row(r, sheet, actor):
    """Render a ``source='comment'`` Notification for ``sheet`` (Area 2), or None
    when the comment is gone / belongs to another sheet.

    The event_type is a DISPLAY-ONLY string ``COMMENT_ADDED`` — NOT an EventType —
    so the existing NotificationItem renders it in the ONE inbox without touching
    the closed 11-type set."""
    if not r.comment:
        return None
    c = frappe.db.get_value(
        "Arbor Cell Comment", r.comment, ["sheet", "author"], as_dict=True
    )
    if not c or c.sheet != sheet:
        return None
    acked = bool(
        frappe.db.exists("Acknowledgement", {"notification": r.name, "user": actor.user})
    )
    return {
        "name": r.name,
        "event_type": "COMMENT_ADDED",
        "message": f"{c.author} commented on a cell",
        "requires_ack": bool(r.requires_ack),
        "acked": acked,
    }


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


# _readable_column_label is the pure-core arbor.core.explore.readable_column_label,
# imported (aliased) at the top of this module — one implementation, reused by
# process_dashboard / _activity_summary / the sheet snapshot column filter.


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


# --- activity-feed keyset cursor codec -------------------------------------
# The feed paginates newest-first on (creation DESC, name DESC) — name is the
# stable tiebreak for two events at the same creation timestamp. ``before`` is an
# OPAQUE token: base64 of "creation_iso|name". The frontend treats it verbatim
# (passes a prior response's ``next_cursor`` back to get the strictly-older page);
# it never parses it. A malformed token raises ``ValueError`` (a bad cursor is a
# client error, mirroring the explore cursor idiom).
def _encode_activity_cursor(creation: Any, name: str) -> str:
    raw = f"{creation}|{name}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_activity_cursor(cursor: Optional[str]) -> Optional[tuple[str, str]]:
    if cursor is None or cursor == "":
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        creation, name = raw.split("|", 1)
        return creation, name
    except Exception as exc:  # noqa: BLE001 — normalise to a typed ValueError
        raise ValueError(f"malformed cursor: {cursor!r}") from exc


@frappe.whitelist()
def list_activity(sheet, limit=50, before=None, type=None, actor=None):
    """The sheet's activity / change-history feed (newest first) — a READ SHIM
    (like ``list_change_requests`` / ``list_notifications``), NOT a registry
    capability, so it stays off the parity/registry surface.

    Returns ``{"events": [...], "next_cursor": str|None}``. Each event row carries
    its event id, type (one of the 11 ``EventType`` values), actor + actor_type,
    ISO timestamp, the linked Change Request (or null), the resolved node LABEL /
    column LABEL when the viewer may see them, and a human one-liner ``summary``.
    Role/site-wide events have ``sheet=NULL`` and never appear here.

    Pagination — KEYSET on (creation DESC, name DESC tiebreak). ``before`` is an
    OPAQUE cursor taken from a prior response's ``next_cursor``; passing it returns
    the page of events STRICTLY OLDER than that boundary
    (``creation < c OR (creation = c AND name < n)``). ``next_cursor`` is null when
    no older events remain (so the UI hides "Load older"). We fetch ``limit + 1``
    rows: if the extra row exists, ``next_cursor`` is built from the ``limit``-th
    row's (creation, name) and only the first ``limit`` are returned.

    Filters — optional ``type`` (one of the 11 ``EventType`` values) and ``actor``
    (a User id), AND-combined with the sheet scope.

    Read-ACL: the feed is "what happened", never the data — it carries NO raw
    cell VALUES. A column the viewer cannot read (``arbor.core.acl.can_read_column``)
    is redacted: its name is dropped from both ``column`` and ``summary`` and the
    phrasing falls back to a generic "a cell" / "a column"."""
    viewer = _actor()
    repo = _repo()
    label_col = next((c.name for c in repo.list_columns(sheet) if c.is_label), None)

    limit = frappe.utils.cint(limit) if limit is not None else 50
    boundary = _decode_activity_cursor(before)

    # Keyset WHERE: sheet scope + optional type/actor + the strictly-older boundary.
    # frappe.get_all cannot express the (creation < c OR (creation = c AND name < n))
    # OR cleanly, so build a parameterized frappe.db.sql. Newest-first ordering is
    # (creation DESC, name DESC); we fetch limit+1 to decide next_cursor.
    conditions = ["sheet = %(sheet)s"]
    values: dict[str, Any] = {"sheet": sheet, "lim": limit + 1}
    if type:
        conditions.append("type = %(type)s")
        values["type"] = type
    if actor:
        conditions.append("actor = %(actor)s")
        values["actor"] = actor
    if boundary is not None:
        c_creation, c_name = boundary
        conditions.append(
            "(creation < %(c_creation)s OR (creation = %(c_creation)s AND name < %(c_name)s))"
        )
        values["c_creation"] = c_creation
        values["c_name"] = c_name

    rows = frappe.db.sql(
        """
        SELECT name, type, actor, actor_type, change_request, payload, creation
        FROM `tabTree Event`
        WHERE {where}
        ORDER BY creation DESC, name DESC
        LIMIT %(lim)s
        """.format(where=" AND ".join(conditions)),
        values,
        as_dict=True,
    )

    # limit+1 sentinel -> there is an older page; the boundary for it is the
    # limit-th row (the last one we actually return).
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = (
        _encode_activity_cursor(page[-1].creation, page[-1].name)
        if has_more and page
        else None
    )

    out = []
    for r in page:
        payload = r.payload
        if isinstance(payload, str):
            payload = frappe.parse_json(payload) if payload else {}
        payload = payload or {}

        node_id = payload.get("node")
        node_label = _node_label(repo, sheet, label_col, node_id) if node_id else None
        col_label, col_readable = _readable_column_label(
            repo, sheet, viewer, payload.get("column")
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
                    r.type, r.actor, payload, repo, sheet, viewer, label_col
                ),
            }
        )
    return {"events": out, "next_cursor": next_cursor}


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


# ---------------------------------------------------------------------------
# Personal CELL DRAFT box (Feature: cell drafts) — server-persisted staging for
# cell edits BEFORE they become a Change Request. Per USER, private: every method
# is scoped to ``_actor().user`` so a user only ever sees / edits their OWN
# drafts. These are UI-staging endpoints, NOT registry capabilities — drafts are
# not a governed capability; only the eventual ``submit_cell_drafts`` routes
# through the executor (``suggestChanges`` → ONE multi-change CR).
# ---------------------------------------------------------------------------
def _find_cell_draft(user: str, sheet, node, column) -> Optional[str]:
    """The actor's existing draft name for a cell, or ``None`` — the upsert key is
    (user, sheet, node, column)."""
    return frappe.db.get_value(
        "Arbor Cell Draft",
        {"user": user, "sheet": sheet, "node": node, "column": column},
        "name",
    )


@frappe.whitelist()
def save_cell_draft(sheet, node, column, value, base_version=None):
    """Upsert the actor's draft for a single cell.

    Finds the actor's existing draft by (user, sheet, node, column); if present it
    updates ``value`` / ``base_version`` in place, otherwise it creates a new one.
    So two saves on the same cell collapse to ONE draft holding the latest value.
    Returns ``{"name": <draft name>}``.
    """
    actor = _actor()  # raises on Guest
    value = _coerce(value)
    bv = frappe.utils.cint(base_version) if base_version not in (None, "") else None

    existing = _find_cell_draft(actor.user, sheet, node, column)
    if existing:
        doc = frappe.get_doc("Arbor Cell Draft", existing)
        doc.value = frappe.as_json(value)
        doc.base_version = bv
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.new_doc("Arbor Cell Draft")
        doc.user = actor.user
        doc.sheet = sheet
        doc.node = node
        doc.column = column
        doc.value = frappe.as_json(value)
        doc.base_version = bv
        doc.insert(ignore_permissions=True)
    return {"name": doc.name}


@frappe.whitelist()
def list_cell_drafts(sheet):
    """The actor's drafts for one sheet: ``[{name, node, column, value,
    base_version}]``. Scoped to ``user=actor.user`` (never another user's)."""
    actor = _actor()
    rows = frappe.get_all(
        "Arbor Cell Draft",
        filters={"user": actor.user, "sheet": sheet},
        fields=["name", "node", "column", "value", "base_version"],
        order_by="creation asc",
    )
    return [
        {
            "name": r.name,
            "node": r.node,
            "column": r.column,
            "value": _coerce(r.value),
            "base_version": r.base_version,
        }
        for r in rows
    ]


@frappe.whitelist()
def discard_cell_draft(sheet, node, column):
    """Delete the actor's draft for one cell. No-op (still ``{"ok": True}``) if the
    actor has no draft there."""
    actor = _actor()
    existing = _find_cell_draft(actor.user, sheet, node, column)
    if existing:
        frappe.delete_doc("Arbor Cell Draft", existing, ignore_permissions=True)
    return {"ok": True}


@frappe.whitelist()
def discard_cell_drafts(sheet):
    """Delete ALL the actor's drafts for one sheet. Returns ``{"discarded": N}``."""
    actor = _actor()
    names = frappe.get_all(
        "Arbor Cell Draft",
        filters={"user": actor.user, "sheet": sheet},
        pluck="name",
    )
    for name in names:
        frappe.delete_doc("Arbor Cell Draft", name, ignore_permissions=True)
    return {"discarded": len(names)}


@frappe.whitelist()
def submit_cell_drafts(sheet):
    """Promote ALL the actor's drafts for ``sheet`` into ONE multi-change Change
    Request, then delete the submitted drafts.

    Builds ``changes=[{action:"updateCell", params:{sheet, node, column, value,
    base_version}} ...]`` from the drafts and dispatches via the SAME
    ``executor.execute_action("suggestChanges", ...)`` funnel the REST surface
    uses — so each item re-resolves to its own approver (a batch can span owners)
    and nothing is re-derived. On success the submitted drafts are deleted and the
    standard Outcome envelope is returned. With no drafts it is a no-op returning
    ``{"kind": "read", "data": {}}`` (no CR created).
    """
    actor = _actor()
    rows = frappe.get_all(
        "Arbor Cell Draft",
        filters={"user": actor.user, "sheet": sheet},
        fields=["name", "node", "column", "value", "base_version"],
        order_by="creation asc",
    )
    if not rows:
        return {"kind": "read", "data": {}}

    changes = []
    for r in rows:
        params: dict[str, Any] = {
            "sheet": sheet,
            "node": r.node,
            "column": r.column,
            "value": _coerce(r.value),
        }
        if r.base_version not in (None, ""):
            params["base_version"] = frappe.utils.cint(r.base_version)
        changes.append({"action": "updateCell", "params": params})

    outcome = executor.execute_action(
        "suggestChanges",
        {"sheet": sheet, "changes": changes},
        actor,
        _repo(),
        _sink(),
    )

    # The drafts have been promoted to a CR — clear them so the box is empty again.
    for r in rows:
        frappe.delete_doc("Arbor Cell Draft", r.name, ignore_permissions=True)

    return _outcome_dict(outcome)


# ---------------------------------------------------------------------------
# Per-cell COMMENTS (Feature: comments drawer, Area 2). The comment MUTATIONS
# (add/resolve/delete) are now REGISTRY CAPABILITIES routed through the ONE
# executor (addComment/resolveComment/deleteComment) — these shims are thin,
# dispatching through ``execute_action`` exactly like ``update_cell`` instead of
# doing inline ACL + writes. Governance is enforced UNIFORMLY in the executor via
# the acl comment authority resolver:
#   read/post  -> can_read_column         (you may discuss any cell you can read)
#   resolve    -> resolve_column_approvers (column owner + editors settle threads)
#   delete     -> author OR column approver (self-moderation + owner moderation)
# The comment doctype is a complete AUDIT record (author + real_user/
# impersonated_as trace) and delete is a SOFT-delete tombstone (row preserved).
# The Notification fan-out on add (source='comment', tree_event=NULL) to the
# column owner/editors + read-ACL-filtered @mentions, minus the author, stays in
# THIS api layer, AFTER a successful dispatch. ``list_cell_comments`` stays a
# non-capability read shim (like ``list_notifications``) and filters tombstones.
# ---------------------------------------------------------------------------
_MENTION_RE = re.compile(r"(?<![\w.])@([A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|[A-Za-z0-9._-]+)")


def _extract_mentions(body: str) -> list[str]:
    """Parse ``@token`` mentions from a comment body into candidate User ids,
    de-duplicated and order-preserving. Pure string work — the read-ACL filter +
    User-existence check happen in the caller. Matches either an ``@email`` or a
    bare ``@handle``; a token mid-word (``a@b``) is NOT a mention."""
    if not body:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _MENTION_RE.finditer(body):
        tok = m.group(1)
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _comment_col(comment_doc) -> Any:
    """Load the Tree Column view for a comment's cell (raises if the column is
    gone — a 404-shaped condition the shim maps)."""
    repo = _repo()
    return repo.get_column(comment_doc.sheet, comment_doc.column)


def _require_readable_cell(repo, sheet, node, column, actor):
    """Assert the cell (sheet,node,column) exists and ``actor`` may READ its column;
    raise the Frappe error the REST layer maps to 404 / 403. Returns the ColumnView."""
    if not frappe.db.exists("Tree Sheet", sheet):
        frappe.local.response["http_status_code"] = 404
        frappe.throw(_("No such sheet {0}").format(sheet), exc=frappe.DoesNotExistError)
    if not frappe.db.exists("Tree Node", node) or frappe.db.get_value("Tree Node", node, "sheet") != sheet:
        frappe.local.response["http_status_code"] = 404
        frappe.throw(_("No such node {0}").format(node), exc=frappe.DoesNotExistError)
    try:
        col = repo.get_column(sheet, column)
    except Exception:
        frappe.local.response["http_status_code"] = 404
        frappe.throw(_("No such column {0}").format(column), exc=frappe.DoesNotExistError)
    if not can_read_column(repo, sheet, col, actor):
        # A cell the viewer cannot read → 403 (never leak its existence/content).
        raise frappe.PermissionError(_("You do not have access to this column"))
    return col


def _can_resolve_comment(repo, sheet, column, actor) -> bool:
    """Resolve/reopen authority — the column approvers (owner + editors), plus an
    explicit admin honor (admins may moderate)."""
    if getattr(actor, "is_admin", False):
        return True
    return actor.user in resolve_column_approvers(repo, sheet, column)


@frappe.whitelist()
def add_cell_comment(sheet, node, column, body, parent_comment=None):
    """Post a comment on the cell ``(sheet, node, column)`` (or a reply when
    ``parent_comment`` is given).

    THIN: dispatches the ``addComment`` capability through ``execute_action`` (the
    ONE executor authorizes ``can_read_column`` and writes through the adapter,
    stamping the author + impersonation trace). 400 on an empty body; 404 on an
    unknown sheet/node/column/parent. Parses @mentions and drops any who cannot
    read the column, passing the surviving set into the dispatch, then fans out a
    Notification (source='comment', tree_event=NULL) to the column owner/editors +
    surviving mentions, minus the author, AFTER the successful dispatch. Returns
    ``{name, thread_root, mentions}``.
    """
    actor = _actor()
    repo = _repo()
    # Cheap up-front existence/readability checks so we surface the documented
    # 400/404 shapes; the executor re-authorizes can_read_column as the hard gate.
    _require_readable_cell(repo, sheet, node, column, actor)

    body = (body or "").strip() if isinstance(body, str) else ""
    if not body:
        frappe.local.response["http_status_code"] = 400
        frappe.throw(_("Comment body must not be empty"), exc=frappe.ValidationError)

    if parent_comment and not frappe.db.exists("Arbor Cell Comment", parent_comment):
        frappe.local.response["http_status_code"] = 404
        frappe.throw(_("No such comment {0}").format(parent_comment), exc=frappe.DoesNotExistError)

    # @mentions: resolve to existing Users who can STILL read the column (a mention
    # of a non-reader is silently dropped — never signal an owner-only cell to
    # someone who can't read it).
    col = repo.get_column(sheet, column)
    mentions: list[str] = []
    for tok in _extract_mentions(body):
        if not frappe.db.exists("User", tok):
            continue
        mentioned_actor = Actor(user=tok, actor_type=ActorType.HUMAN, is_admin=_is_admin_user(tok))
        if can_read_column(repo, sheet, col, mentioned_actor):
            mentions.append(tok)

    # Route through the ONE executor: addComment writes + stamps the audit trace.
    outcome = _dispatch(
        "addComment",
        {
            "sheet": sheet,
            "node": node,
            "column": column,
            "body": body,
            "parent_comment": parent_comment or None,
            "mentions": mentions,
        },
        actor=actor,
    )
    comment_name = (outcome.get("data") or {}).get("comment")
    thread_root = (outcome.get("data") or {}).get("thread_root")

    # Fan out FYI notifications directly (a comment is NOT a Tree Event, so it does
    # NOT go through the Tree-Event→subscription dispatcher). Recipients = column
    # owner + editors + surviving mentions, minus the author. Each is read-gated by
    # construction (approvers can read; mentions were filtered above). This is a
    # frappe write, so it stays in the api layer (never the pure core).
    recipients = set(resolve_column_approvers(repo, sheet, column)) | set(mentions)
    recipients.discard(actor.user)
    for recipient in sorted(recipients):
        # Idempotent per (comment, recipient) — the same comment never double-notifies.
        if frappe.db.exists(
            "Notification",
            {"comment": comment_name, "recipient": recipient, "channel": "in-app"},
        ):
            continue
        n = frappe.new_doc("Notification")
        n.source = "comment"
        n.comment = comment_name
        n.tree_event = None
        n.recipient = recipient
        n.channel = "in-app"
        n.requires_ack = 0
        n.insert(ignore_permissions=True)

    return {"name": comment_name, "thread_root": thread_root, "mentions": mentions}


@frappe.whitelist()
def list_cell_comments(sheet, node, column):
    """The comment thread(s) for a cell, oldest-first (grouped by ``thread_root``
    client-side). AUTHZ: ``can_read_column`` else 403.

    Each row carries author/body/mentions/resolved state + the display-only
    ``can_resolve`` (actor is a column approver) and ``can_delete`` (actor is the
    author OR a column approver) hints. The server re-enforces both on
    resolve/delete — the hints never gate on their own."""
    actor = _actor()
    repo = _repo()
    _require_readable_cell(repo, sheet, node, column, actor)

    can_resolve = _can_resolve_comment(repo, sheet, column, actor)
    # Exclude soft-deleted tombstones (delete is now a SOFT delete: the row is
    # preserved for audit but never shown in the drawer).
    rows = frappe.get_all(
        "Arbor Cell Comment",
        filters={"sheet": sheet, "node": node, "column": column, "deleted": 0},
        fields=[
            "name", "thread_root", "parent_comment", "author", "body", "mentions",
            "resolved", "resolved_by", "resolved_at", "creation",
        ],
        order_by="creation asc",
    )
    out = []
    for r in rows:
        out.append(
            {
                "name": r.name,
                "thread_root": r.thread_root,
                "parent_comment": r.parent_comment,
                "author": r.author,
                "body": r.body,
                "mentions": _coerce(r.mentions) or [],
                "resolved": bool(r.resolved),
                "resolved_by": r.resolved_by,
                "resolved_at": str(r.resolved_at) if r.resolved_at else None,
                "timestamp": str(r.creation),
                "can_resolve": can_resolve,
                "can_delete": (actor.user == r.author) or can_resolve,
            }
        )
    return out


@frappe.whitelist()
def resolve_cell_comment(comment, resolved=True):
    """Mark a thread resolved (or reopen with ``resolved=False``) on its ROOT.

    THIN: dispatches ``resolveComment`` through ``execute_action`` (the executor
    authorizes ``resolve_column_approvers`` against the comment's column — 403 when
    denied, 404 for an unknown comment — and writes through the adapter).
    Idempotent (re-resolving / re-opening is a no-op success). Resolving a REPLY
    resolves its whole thread (the adapter carries the resolved flag on the root)."""
    actor = _actor()
    want = (
        frappe.utils.cint(resolved) == 1
        if isinstance(resolved, (str, int)) else bool(resolved)
    )
    outcome = _dispatch("resolveComment", {"comment": comment, "resolved": want}, actor=actor)
    data = outcome.get("data") or {}
    return {"name": data.get("comment"), "resolved": bool(data.get("resolved"))}


@frappe.whitelist()
def delete_cell_comment(comment):
    """Delete a comment. THIN: dispatches ``deleteComment`` through
    ``execute_action`` (the executor authorizes author-OR-column-approver — 403
    when denied, 404 for an unknown comment).

    Delete is now a SOFT delete (tombstone: ``deleted=1`` + ``deleted_by`` +
    ``deleted_at``, the row PRESERVED for audit). ``list_cell_comments`` filters
    tombstones out, so replies never surface an orphaned/deleted parent."""
    actor = _actor()
    _dispatch("deleteComment", {"comment": comment}, actor=actor)
    return {"ok": True, "tombstoned": True}


# ---------------------------------------------------------------------------
# Notification WEBHOOK registration surface (Feature: webhooks, Area 3, WS-A3c).
# A sheet-admin-gated management surface over the EXISTING Webhook Endpoint
# doctype + the ONE delivery engine — NOT registry capabilities and NOT Tree
# Events (like the comment / draft shims). These shims:
#   * gate every write on structural-owner-of-the-sheet OR platform admin (403
#     otherwise) — the SAME structural gate the process meta-caps use, so a
#     sheet's owner manages its own webhooks and an admin manages any;
#   * validate the URL + block SSRF to loopback / link-local / private / metadata
#     ranges (deny-by-default; only public http(s)) on register AND update;
#   * store the signing ``secret`` WRITE-ONCE — generated server-side at register,
#     returned ONCE in the register response, and NEVER echoed by a list/read (the
#     Password field is not selected). Update never rotates it (no secret in the
#     patch surface); rotation is a deliberate future op.
# The agent NEVER reaches these (they are not in getLLMTools + not capabilities).
# ---------------------------------------------------------------------------

#: The NON-tree-event notification sources an endpoint may subscribe to — mirrors
#: the Webhook Endpoint controller's closed set (``tree_event`` rides event_types).
_WEBHOOK_NOTIFICATION_SOURCES = ("comment", "process", "sla", "change_request")

#: Extra hostnames that resolve to a cloud metadata / link-local service and MUST
#: be refused even if DNS would map them to a routable-looking address.
_SSRF_DENY_HOSTNAMES = frozenset({"metadata", "metadata.google.internal"})


class WebhookURLError(Exception):
    """A webhook URL failed validation (bad scheme, unparseable, or resolves to a
    blocked SSRF target). Surfaced by the shims as a 400."""


def _ip_is_blocked(ip: "ipaddress._BaseAddress") -> bool:
    """Deny-by-default SSRF classifier: block loopback, link-local (incl. the
    169.254.169.254 metadata address), private, reserved, multicast, and
    unspecified ranges; also block the IPv4-mapped/compat and 6to4/Teredo IPv6
    embeddings whose embedded v4 is itself blocked. Only a genuinely public,
    globally-routable address passes."""
    if (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return True
    # Unwrap IPv6 forms that embed a v4 address (::ffff:169.254.169.254, 6to4,
    # Teredo) so an attacker can't smuggle a blocked v4 through a v6 literal.
    embedded = getattr(ip, "ipv4_mapped", None) or getattr(ip, "sixtofour", None)
    if embedded is not None and _ip_is_blocked(embedded):
        return True
    return False


def _validate_webhook_url(url: str) -> str:
    """Return ``url`` if it is a public http(s) endpoint; else raise
    :class:`WebhookURLError`.

    Rejects: a non-http(s) scheme (``file:``/``gopher:``/…); a missing host; an IP
    literal in a blocked range; a hostname on the metadata deny-list; and a
    hostname whose DNS resolution yields ANY blocked address (all A/AAAA records
    are checked so a split-horizon name with one private record is refused). DNS
    that fails to resolve is refused too (can't prove it's public)."""
    if not isinstance(url, str) or not url.strip():
        raise WebhookURLError(_("Webhook URL is required"))
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise WebhookURLError(_("Webhook URL must be http(s), not {0}").format(parts.scheme or "(none)"))
    host = parts.hostname
    if not host:
        raise WebhookURLError(_("Webhook URL has no host"))
    if host.lower() in _SSRF_DENY_HOSTNAMES:
        raise WebhookURLError(_("Webhook URL host {0} is not allowed").format(host))

    # An IP literal is checked directly (no DNS); a hostname is resolved and EVERY
    # returned address must be public.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _ip_is_blocked(literal):
            raise WebhookURLError(_("Webhook URL resolves to a blocked address {0}").format(host))
        return url.strip()

    try:
        infos = socket.getaddrinfo(host, parts.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise WebhookURLError(_("Webhook URL host {0} does not resolve").format(host)) from exc
    if not infos:
        raise WebhookURLError(_("Webhook URL host {0} does not resolve").format(host))
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _ip_is_blocked(ip):
            raise WebhookURLError(
                _("Webhook URL host {0} resolves to a blocked address {1}").format(host, addr)
            )
    return url.strip()


def _coerce_sources(sources: Any) -> list[str]:
    """Normalize the ``notification_sources`` argument (JSON string / list / None)
    into a validated list; a source outside the closed set is a 400 (matches the
    controller's guard so the surface fails fast before insert)."""
    parsed = _coerce(sources)
    if parsed is None:
        return []
    if not isinstance(parsed, (list, tuple)):
        frappe.local.response["http_status_code"] = 400
        frappe.throw(_("notification_sources must be a list"), exc=frappe.ValidationError)
    out: list[str] = []
    for s in parsed:
        if s not in _WEBHOOK_NOTIFICATION_SOURCES:
            frappe.local.response["http_status_code"] = 400
            frappe.throw(
                _("Unknown notification source {0}; the closed set is {1}").format(
                    s, ", ".join(_WEBHOOK_NOTIFICATION_SOURCES)
                ),
                exc=frappe.ValidationError,
            )
        if s not in out:
            out.append(s)
    return out


def _require_webhook_admin(repo, sheet: Optional[str], actor: Actor) -> None:
    """Assert ``actor`` may manage webhooks for ``sheet`` — the sheet's structural
    owner OR a platform admin. A non-owner, non-admin caller is a 403 (never a CR:
    webhook registration is an out-of-band admin surface, not a governed cell
    mutation). A missing sheet is a 404."""
    if getattr(actor, "is_admin", False):
        return
    if not sheet:
        # A global (sheet-less) endpoint can only be managed by a platform admin.
        raise frappe.PermissionError(_("Only an admin may manage global webhooks"))
    if not frappe.db.exists("Tree Sheet", sheet):
        frappe.local.response["http_status_code"] = 404
        frappe.throw(_("No such sheet {0}").format(sheet), exc=frappe.DoesNotExistError)
    owner = repo.get_sheet(sheet).structural_owner
    if actor.user != owner:
        raise frappe.PermissionError(
            _("Only the sheet's structural owner or an admin may manage its webhooks")
        )


def _endpoint_out(doc) -> dict[str, Any]:
    """Serialize a Webhook Endpoint to the ``WebhookEndpointView`` shape WITHOUT the
    secret (the Password field is never echoed by a read/list — WEBHOOKS SSRF §7)."""
    et = doc.get("event_types")
    ns = doc.get("notification_sources")
    return {
        "name": doc.get("name"),
        "label": doc.get("label"),
        "url": doc.get("url"),
        "active": bool(doc.get("active")),
        "sheet": doc.get("sheet"),
        "owner_user": doc.get("owner_user"),
        "scope": doc.get("scope"),
        "target": doc.get("target"),
        "event_types": _coerce(et) or [] if et else [],
        "notification_sources": _coerce(ns) or [] if ns else [],
    }


@frappe.whitelist()
def register_webhook(
    url,
    sheet=None,
    label=None,
    notification_sources=None,
    event_types=None,
    scope="sheet",
    target=None,
):
    """Register a Webhook Endpoint (sheet-admin surface). AUTHZ: the sheet's
    structural owner OR a platform admin (403 otherwise). SSRF-validates the URL
    (400 on a blocked/loopback/link-local/private/metadata target or a non-http(s)
    scheme). Generates the signing ``secret`` server-side and returns it ONCE in the
    response — it is NEVER echoed again. Returns the endpoint view + ``secret``."""
    actor = _actor()
    repo = _repo()
    _require_webhook_admin(repo, sheet, actor)

    try:
        clean_url = _validate_webhook_url(url)
    except WebhookURLError as exc:
        frappe.local.response["http_status_code"] = 400
        frappe.throw(str(exc), exc=frappe.ValidationError)

    sources = _coerce_sources(notification_sources)
    types = _coerce(event_types) or []
    secret = _secrets.token_urlsafe(32)

    doc = frappe.new_doc("Webhook Endpoint")
    doc.url = clean_url
    doc.label = label
    doc.sheet = sheet
    doc.owner_user = actor.user
    doc.scope = scope or "sheet"
    doc.target = target or sheet
    doc.event_types = frappe.as_json(types)
    doc.notification_sources = frappe.as_json(sources)
    doc.secret = secret  # Password field: stored encrypted; write-once here.
    doc.active = 1
    doc.insert(ignore_permissions=True)

    out = _endpoint_out(doc.as_dict())
    out["secret"] = secret  # returned ONCE, never again.
    return out


@frappe.whitelist()
def list_webhooks(sheet=None):
    """List Webhook Endpoints the actor may manage. AUTHZ: an admin sees all (or the
    ``sheet`` filter); a sheet's structural owner sees ONLY that sheet's endpoints
    (403 if they ask for a sheet they don't own). The signing ``secret`` is NEVER
    included (write-once). Read-only; emits no Tree Event."""
    actor = _actor()
    repo = _repo()
    is_admin = getattr(actor, "is_admin", False)

    filters: dict[str, Any] = {}
    if sheet:
        _require_webhook_admin(repo, sheet, actor)  # 403/404 for a non-owner/unknown sheet
        filters["sheet"] = sheet
    elif not is_admin:
        # A non-admin with no sheet filter sees only the sheets they structurally
        # own. Cheap: filter to endpoints whose owner_user is them (they registered
        # them) — a sheet-owner's endpoints all carry owner_user=them by register.
        filters["owner_user"] = actor.user

    names = frappe.get_all("Webhook Endpoint", filters=filters, pluck="name")
    out = []
    for n in names:
        doc = frappe.get_doc("Webhook Endpoint", n)
        out.append(_endpoint_out(doc.as_dict()))
    return out


@frappe.whitelist()
def update_webhook(endpoint, url=None, label=None, active=None, notification_sources=None, event_types=None):
    """Update a Webhook Endpoint's mutable fields (url / label / active /
    notification_sources / event_types). AUTHZ: the endpoint's sheet owner OR an
    admin (403). SSRF-validates a changed URL (400). NEVER rotates the secret — it
    is write-once (no secret arg). 404 on an unknown endpoint. Returns the view."""
    actor = _actor()
    repo = _repo()
    if not frappe.db.exists("Webhook Endpoint", endpoint):
        frappe.local.response["http_status_code"] = 404
        frappe.throw(_("No such webhook endpoint {0}").format(endpoint), exc=frappe.DoesNotExistError)

    doc = frappe.get_doc("Webhook Endpoint", endpoint)
    _require_webhook_admin(repo, doc.sheet, actor)

    if url is not None:
        try:
            doc.url = _validate_webhook_url(url)
        except WebhookURLError as exc:
            frappe.local.response["http_status_code"] = 400
            frappe.throw(str(exc), exc=frappe.ValidationError)
    if label is not None:
        doc.label = label
    if active is not None:
        doc.active = 1 if frappe.utils.cint(active) == 1 else 0
    if notification_sources is not None:
        doc.notification_sources = frappe.as_json(_coerce_sources(notification_sources))
    if event_types is not None:
        doc.event_types = frappe.as_json(_coerce(event_types) or [])
    doc.save(ignore_permissions=True)
    return _endpoint_out(doc.as_dict())


@frappe.whitelist()
def delete_webhook(endpoint):
    """Delete a Webhook Endpoint. AUTHZ: the endpoint's sheet owner OR an admin
    (403). 404 on an unknown endpoint. Any in-flight retries for the endpoint are
    cancelled by the dispatcher's deleted-endpoint guard on the next attempt
    (WEBHOOKS-005); we drop the pending Webhook Delivery rows here so the retry
    runner never re-picks a now-orphaned delivery."""
    actor = _actor()
    repo = _repo()
    if not frappe.db.exists("Webhook Endpoint", endpoint):
        frappe.local.response["http_status_code"] = 404
        frappe.throw(_("No such webhook endpoint {0}").format(endpoint), exc=frappe.DoesNotExistError)

    doc = frappe.get_doc("Webhook Endpoint", endpoint)
    _require_webhook_admin(repo, doc.sheet, actor)

    for d in frappe.get_all("Webhook Delivery", filters={"endpoint": endpoint}, pluck="name"):
        frappe.delete_doc("Webhook Delivery", d, ignore_permissions=True, force=True)
    frappe.delete_doc("Webhook Endpoint", endpoint, ignore_permissions=True, force=True)
    return {"ok": True}


@frappe.whitelist()
def test_webhook(endpoint):
    """Fire a signed ``type='webhook.test'`` ping at ONE endpoint through the SAME
    signed delivery engine (a real POST via the retry-capable dispatcher). AUTHZ:
    the endpoint's sheet owner OR an admin (403). 404 on an unknown endpoint. Uses a
    unique ``event_id`` per call (``test:<endpoint>:<token>``) so the per-(endpoint,
    event_id) idempotency guard never suppresses a repeat test. Returns the created
    delivery id + its terminal status."""
    actor = _actor()
    repo = _repo()
    if not frappe.db.exists("Webhook Endpoint", endpoint):
        frappe.local.response["http_status_code"] = 404
        frappe.throw(_("No such webhook endpoint {0}").format(endpoint), exc=frappe.DoesNotExistError)

    doc = frappe.get_doc("Webhook Endpoint", endpoint)
    _require_webhook_admin(repo, doc.sheet, actor)

    # Reuse the ONE delivery engine + serializer (no reinvented signing). Import
    # lazily so the module stays import-clean in the bench-free lane.
    from arbor.arbor.dispatch.frappe_dispatch import _webhook_dispatcher
    from arbor.arbor.dispatch.serializer import serialize_notification_bytes

    dispatcher = _webhook_dispatcher()
    ep_view = dispatcher._store.get_endpoint(endpoint)
    if ep_view is None:  # deleted between the exists() check and now
        frappe.local.response["http_status_code"] = 404
        frappe.throw(_("No such webhook endpoint {0}").format(endpoint), exc=frappe.DoesNotExistError)

    event_id = f"test:{endpoint}:{_secrets.token_hex(8)}"
    body = serialize_notification_bytes(
        event_id=event_id,
        source="test",
        sheet=doc.sheet,
        type="webhook.test",
        payload={"endpoint": endpoint, "ping": True},
        actor=actor.user,
        actor_type="human",
        timestamp=frappe.utils.now(),
    )
    delivery_id = dispatcher.deliver(
        ep_view,
        event_id=event_id,
        body=body,
        source="test",
        link_fields={"notification": None},
    )
    status = None
    if delivery_id is not None:
        status = frappe.db.get_value("Webhook Delivery", delivery_id, "status")
    return {"delivery": delivery_id, "status": status}
