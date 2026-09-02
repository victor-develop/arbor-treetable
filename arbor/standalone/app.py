"""Arbor standalone REST surface — FastAPI over the SQLAlchemy adapter.

The frappe-free peer of ``arbor.arbor.api``: every route lives at the SAME
``/api/method/arbor.<name>`` path the built React frontend already fetches, and
every response is wrapped in the frappe envelope ``{"message": <payload>}`` that
the FE's ``unwrap()`` expects — so ``frontend/dist`` works unchanged.

Parity rules (mirrored from ``arbor.arbor.api``, the reference surface):

* Every mutation funnels into the ONE pure ``arbor.core.executor.execute_action``
  with the same exception -> HTTP-status mapping as the frappe ``_dispatch``
  (401 unauthenticated, 403 control-action denial / scope violation, 404 unknown
  capability or missing row, 409 storage conflict, 400 schema/cursor, 422 size
  budget) — and a stale cell/move conflict is HTTP **200** carrying
  ``{kind:"read", error:"VERSION_CONFLICT", ...}`` (Feature 1; the FE reads
  ``outcome.error``, never a thrown status).
* The read shims (list_*, snapshot, inbox, process reads) reproduce the frappe
  shims' shapes exactly, reusing the pure core (acl / explore / snapshot /
  change_request) for every ACL or redaction decision.
* Dispatch fan-out (notifications / webhooks / process) runs in-process off the
  two seams ``SQLEventSink(dispatch=...)`` / ``SQLRepository(on_notification=...)``
  — the standalone analog of the frappe doc_events — reusing the PURE dispatcher
  modules (``arbor.arbor.dispatch.{notify,webhook,matcher,serializer,
  notification_webhook}``; none import frappe). Webhook retries + the SLA sweep
  run on plain daemon threads (no redis, no celery).

Auth: ``get_current_actor(request) -> Actor`` from ``.auth`` (401 when
unauthenticated); the impersonation overlay is applied here via
``repo.get_active_impersonation`` exactly as the frappe ``_actor()`` does.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import secrets as _secrets
import socket
import threading
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

# PURE (bench-free) lanes reused verbatim — these modules never import frappe
# (their own headers guarantee it). The frappe BINDINGS (arbor.arbor.api,
# arbor.arbor.dispatch.frappe_dispatch, arbor.arbor.adapter.*) are deliberately
# NOT imported anywhere in the standalone package.
from arbor.arbor.agent.config import load_config
from arbor.arbor.agent.provider import get_provider
from arbor.arbor.agent.react import _DEFAULT_SYSTEM, _WORKSPACE_SYSTEM, run_agent_session
from arbor.arbor.dispatch.notification_webhook import FAN_OUT_SOURCES, fan_out
from arbor.arbor.dispatch.notify import NotificationDispatcher
from arbor.arbor.dispatch.ports import TransportTimeout
from arbor.arbor.dispatch.serializer import serialize_notification_bytes
from arbor.arbor.dispatch.webhook import WebhookDispatcher
from arbor.core import executor
from arbor.core import process as process_machine
from arbor.core.acl import can_read_column, resolve_column_approvers
from arbor.core.agent_scope import AgentScope, ScopeError, authorize_scope, hash_token
from arbor.core.change_request import (
    _column_editor_approvers,
    _reresolve_approver,
    _synthetic_item_cr,
)
from arbor.core.explore import (
    CellBudgetExceededError,
    SheetTooLargeError,
    process_rule_views,
)
from arbor.core.explore import (
    readable_column_label as _readable_column_label,
)
from arbor.core.skill import render_skill_md
from arbor.core.types import (
    Actor,
    ActorType,
    AuthorizationError,
    CRStateError,
    Outcome,
    SchemaValidationError,
    UnknownCapabilityError,
)
from arbor.core.types import (
    StaleVersionError as CoreStaleVersionError,
)

from . import models as m
from .auth import (
    configure as configure_auth,
)
from .auth import (
    get_current_actor,
    read_session_user,
)
from .auth import (
    router as auth_router,
)
from .db import create_all, make_engine, make_session_factory
from .errors import ConflictError, StaleMoveError, StaleVersionError
from .repository import CellDraft, SQLEventSink, SQLRepository
from .snapshot import build_sheet_snapshot

# ---------------------------------------------------------------------------
# Engine / session wiring (DATABASE_URL -> sqlite fallback; see .db).
# ---------------------------------------------------------------------------
ENGINE = make_engine()
create_all(ENGINE)
SessionLocal = make_session_factory(ENGINE)
# Share ONE engine/pool with the auth lane (its session-cookie login routes +
# get_current_actor read the same users/impersonation tables).
configure_auth(SessionLocal)


def _utcnow() -> datetime:
    """Naive UTC now (matches the models' DATETIME columns)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_db():
    """Per-request unit of work: ONE session, committed on success, rolled back
    on any raised error (incl. the 4xx HTTPExceptions the dispatch mapping
    throws) — the standalone analog of frappe's request transaction. Requires
    FastAPI >= 0.106 (exceptions propagate back into yield dependencies)."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def _msg(payload: Any) -> dict[str, Any]:
    """The frappe response envelope the FE's ``unwrap()`` expects."""
    return {"message": payload}


# ---------------------------------------------------------------------------
# Dispatch fan-out wiring (the standalone doc_events): one stored Tree Event
# feeds the notification dispatcher, the webhook dispatcher, and the process
# consumer — the exact trio of frappe's ``on_tree_event_insert``.
# ---------------------------------------------------------------------------
@dataclass
class _EventView:
    """Duck-typed ``dispatch.ports.EventView`` over a just-stored TreeEvent."""

    name: str
    sheet: str | None
    type: str
    payload: dict[str, Any]
    actor: str | None
    actor_type: str
    change_request: str | None
    timestamp: str | None
    created_at: datetime | None = None


@dataclass
class _SubView:
    """Duck-typed ``dispatch.ports.SubscriptionView`` over a Subscription row."""

    name: str
    subscriber: str
    subscriber_kind: str
    scope: str
    target: str
    event_types: list[str]
    delivery: str
    requires_ack: bool
    created_at: datetime | None = None


@dataclass
class _EndpointView:
    """Duck-typed ``dispatch.ports.WebhookEndpointView`` over an endpoint row."""

    name: str
    url: str
    secret: str
    event_types: list[str]
    scope: str
    target: str | None
    active: bool
    notification_sources: list[str] = field(default_factory=list)
    label: str | None = None
    sheet: str | None = None
    owner_user: str | None = None


def _parse_json_list(raw: Any) -> list[str]:
    """A JSON-array Text column (or an already-parsed list) -> list of str."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(s) for s in raw]
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(s) for s in parsed] if isinstance(parsed, list) else []


def _endpoint_view(row: m.WebhookEndpoint) -> _EndpointView:
    return _EndpointView(
        name=row.name,
        url=row.url,
        secret=row.secret or "",
        event_types=list(row.event_types or []),
        scope=row.scope or "sheet",
        target=row.target,
        active=bool(row.active),
        notification_sources=_parse_json_list(row.notification_sources),
        label=row.label,
        sheet=row.sheet,
        owner_user=row.owner_user,
    )


class _WallClock:
    """``dispatch.ports.Clock`` over the wall clock (naive UTC)."""

    def now(self) -> datetime:
        return _utcnow()


class SQLNotificationStore:
    """``dispatch.ports.NotificationStore`` over the standalone tables — the
    SQLAlchemy twin of ``FrappeNotificationStore``."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def live_subscriptions(self, sheet: str | None) -> list[_SubView]:
        # A subscription is "live" if it exists (unsubscribe deletes the row).
        # The matcher makes the final scope decision, same as the frappe store.
        rows = self.session.scalars(sa.select(m.Subscription)).all()
        return [
            _SubView(
                name=r.name,
                subscriber=r.subscriber,
                subscriber_kind=r.subscriber_kind or "user",
                scope=r.scope,
                target=r.target,
                event_types=list(r.event_types or []),
                delivery=r.delivery,
                requires_ack=bool(r.requires_ack),
                created_at=r.creation,
            )
            for r in rows
        ]

    def get_node_range(self, node: str) -> tuple[int, int] | None:
        row = self.session.get(m.Node, node)
        if row is None:
            return None
        return (int(row.lft or 0), int(row.rgt or 0))

    def notification_exists(self, tree_event: str, recipient: str, channel: str) -> bool:
        return bool(
            self.session.scalar(
                sa.select(m.Notification.name).where(
                    m.Notification.tree_event == tree_event,
                    m.Notification.recipient == recipient,
                    m.Notification.channel == channel,
                )
            )
        )

    def create_notification(self, data: dict[str, Any]) -> str:
        row = m.Notification(
            source=data.get("source") or "tree_event",
            tree_event=data.get("tree_event"),
            change_request=data.get("change_request"),
            recipient=data["recipient"],
            channel=data.get("channel") or "in-app",
            requires_ack=bool(data.get("requires_ack")),
            delivered_at=data.get("delivered_at") or _utcnow(),
        )
        self.session.add(row)
        self.session.flush()
        return row.name

    def count_ack_required(self, *, tree_event=None, change_request=None) -> int:
        stmt = sa.select(sa.func.count()).select_from(m.Notification).where(
            m.Notification.requires_ack.is_(True)
        )
        if tree_event is not None:
            stmt = stmt.where(m.Notification.tree_event == tree_event)
        else:
            stmt = stmt.where(m.Notification.change_request == change_request)
        return int(self.session.scalar(stmt) or 0)

    def count_acknowledged(self, *, tree_event=None, change_request=None) -> int:
        stmt = sa.select(m.Notification.name).where(m.Notification.requires_ack.is_(True))
        if tree_event is not None:
            stmt = stmt.where(m.Notification.tree_event == tree_event)
        else:
            stmt = stmt.where(m.Notification.change_request == change_request)
        names = list(self.session.scalars(stmt).all())
        if not names:
            return 0
        return int(
            self.session.scalar(
                sa.select(sa.func.count())
                .select_from(m.Acknowledgement)
                .where(m.Acknowledgement.notification.in_(names))
            )
            or 0
        )


class SQLWebhookStore:
    """``dispatch.ports.WebhookStore`` over the standalone tables — the
    SQLAlchemy twin of ``FrappeWebhookStore``."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def active_endpoints(self, sheet: str | None) -> list[_EndpointView]:
        rows = self.session.scalars(
            sa.select(m.WebhookEndpoint).where(m.WebhookEndpoint.active.is_(True))
        ).all()
        return [_endpoint_view(r) for r in rows]

    def notification_endpoints(self, source: str) -> list[_EndpointView]:
        views = self.active_endpoints(None)
        return [v for v in views if source in (v.notification_sources or [])]

    def get_endpoint(self, endpoint: str) -> _EndpointView | None:
        row = self.session.get(m.WebhookEndpoint, endpoint)
        return _endpoint_view(row) if row is not None else None

    def get_node_range(self, node: str) -> tuple[int, int] | None:
        row = self.session.get(m.Node, node)
        if row is None:
            return None
        return (int(row.lft or 0), int(row.rgt or 0))

    def delivery_exists(self, endpoint: str, tree_event: str) -> bool:
        return bool(
            self.session.scalar(
                sa.select(m.WebhookDelivery.name).where(
                    m.WebhookDelivery.endpoint == endpoint,
                    m.WebhookDelivery.tree_event == tree_event,
                )
            )
        )

    def delivery_exists_for_event(self, endpoint: str, event_id: str) -> bool:
        return bool(
            self.session.scalar(
                sa.select(m.WebhookDelivery.name).where(
                    m.WebhookDelivery.endpoint == endpoint,
                    m.WebhookDelivery.event_id == event_id,
                )
            )
        )

    def create_delivery(self, data: dict[str, Any]) -> str:
        body = data.get("body")
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        row = m.WebhookDelivery(
            endpoint=data["endpoint"],
            source=data.get("source") or "tree_event",
            tree_event=data.get("tree_event"),
            notification=data.get("notification"),
            event_id=data.get("event_id"),
            status=data.get("status") or "pending",
            attempts=int(data.get("attempts") or 0),
            signature=data.get("signature"),
            body=body,
            next_retry_at=data.get("next_retry_at"),
            last_response=data.get("last_response"),
        )
        self.session.add(row)
        self.session.flush()
        return row.name

    def update_delivery(self, delivery: str, patch: dict[str, Any]) -> None:
        row = self.session.get(m.WebhookDelivery, delivery)
        if row is None:
            return
        for k, v in (patch or {}).items():
            if hasattr(row, k):
                setattr(row, k, v)
        self.session.flush()

    def get_delivery(self, delivery: str) -> dict[str, Any]:
        row = self.session.get(m.WebhookDelivery, delivery)
        if row is None:
            raise KeyError(delivery)
        return {
            "name": row.name,
            "endpoint": row.endpoint,
            "source": row.source,
            "event_id": row.event_id,
            "tree_event": row.tree_event,
            "notification": row.notification,
            "status": row.status,
            "attempts": int(row.attempts or 0),
            "signature": row.signature,
            "body": (row.body or "").encode("utf-8"),
            "url": None,  # retries re-read the endpoint's current URL
        }

    def due_deliveries(self, now: Any) -> list[dict[str, Any]]:
        rows = self.session.scalars(
            sa.select(m.WebhookDelivery.name).where(
                m.WebhookDelivery.status == "pending",
                m.WebhookDelivery.next_retry_at.is_not(None),
                m.WebhookDelivery.next_retry_at <= now,
            )
        ).all()
        return [self.get_delivery(n) for n in rows]

    def claim_delivery(self, delivery: str) -> bool:
        # Single-process deployment (one uvicorn worker + one retry thread): the
        # in-process retry loop is the only claimer, so a plain True is safe.
        return True


@dataclass
class _TransportResponse:
    status_code: int
    text: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are NOT auto-followed (WEBHOOKS-030): a 3xx surfaces as its own
    status and reschedules like any non-2xx."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class _UrllibTransport:
    """``dispatch.ports.Transport`` over urllib (stdlib; no requests dep)."""

    def post(self, url: str, body: bytes, headers: dict[str, str], timeout: float):
        req = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with _OPENER.open(req, timeout=timeout) as resp:
                text = resp.read(512).decode("utf-8", errors="replace")
                return _TransportResponse(status_code=int(resp.status), text=text)
        except urllib.error.HTTPError as exc:  # non-2xx IS a response, not an error
            text = (exc.read(512) or b"").decode("utf-8", errors="replace")
            return _TransportResponse(status_code=int(exc.code), text=text)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransportTimeout(str(exc)) from exc


_TRANSPORT = _UrllibTransport()


def _webhook_dispatcher(session: Session) -> WebhookDispatcher:
    return WebhookDispatcher(SQLWebhookStore(session), _TRANSPORT, _WallClock())


#: source -> display type for the notification->webhook bridge (mirror of the
#: frappe ``_NotificationDoc._DISPLAY_TYPE``).
_NOTIF_DISPLAY_TYPE = {
    "comment": "COMMENT_ADDED",
    "process": "PROCESS_NOTIFICATION",
    "sla": "SLA_BREACHED",
    "change_request": "CHANGE_REQUEST_NOTIFICATION",
}


@dataclass
class _NotifView:
    """Duck-typed ``notification_webhook.NotificationView``."""

    name: str
    source: str
    sheet: str | None
    type: str
    payload: dict[str, Any]
    actor: str | None
    actor_type: str
    timestamp: str | None


def _on_notification_created(session: Session, name: str) -> None:
    """The ``Notification after_insert`` bridge (WS-A3c): fan a just-created
    NON-tree-event Notification out to the subscribing webhook endpoints through
    the ONE delivery engine. tree_event-sourced rows are inert (that lane
    already delivers them)."""
    row = session.get(m.Notification, name)
    if row is None or (row.source or "tree_event") not in FAN_OUT_SOURCES:
        return
    sheet = None
    if row.source == "comment" and row.comment:
        c = session.get(m.CellComment, row.comment)
        sheet = c.sheet if c is not None else None
    elif row.source == "change_request" and row.change_request:
        cr = session.get(m.ChangeRequest, row.change_request)
        sheet = cr.sheet if cr is not None else None
    payload: dict[str, Any] = {"recipient": row.recipient}
    if row.comment:
        payload["comment"] = row.comment
    if row.change_request:
        payload["change_request"] = row.change_request
    view = _NotifView(
        name=row.name,
        source=row.source,
        sheet=sheet,
        type=_NOTIF_DISPLAY_TYPE.get(row.source, row.source),
        payload=payload,
        actor=None,
        actor_type="system",
        timestamp=str(row.delivered_at or row.creation),
    )
    dispatcher = _webhook_dispatcher(session)
    fan_out(
        view,
        store=dispatcher._store,
        deliver=dispatcher.deliver_notification,
        serialize=serialize_notification_bytes,
    )


def _dispatch_tree_event(session: Session, repo: SQLRepository, ev: Any) -> None:
    """The ``Tree Event after_insert`` trio (mirror of frappe's
    ``on_tree_event_insert``): notifications, webhooks, then the process
    consumer. None emit a Tree Event, so there is no recursion."""
    actor_type = ev.actor_type.value if isinstance(ev.actor_type, ActorType) else ev.actor_type
    created_at = None
    if ev.timestamp:
        try:
            created_at = datetime.fromisoformat(ev.timestamp)
        except ValueError:
            created_at = None
    view = _EventView(
        name=ev.event_id,
        sheet=ev.sheet,
        type=ev.type,
        payload=dict(ev.payload or {}),
        actor=ev.actor,
        actor_type=actor_type,
        change_request=ev.change_request,
        timestamp=ev.timestamp,
        created_at=created_at,
    )
    NotificationDispatcher(SQLNotificationStore(session), _WallClock()).on_tree_event(view)
    _webhook_dispatcher(session).on_tree_event(view)
    # Process consumer (Area 3): NODE_CREATED / NODE_VALUE_UPDATED advance the
    # sheet's enabled process; the default notify sink writes in-app rows via
    # repo.create_notification (which itself bridges to webhooks).
    if view.type in ("NODE_CREATED", "NODE_VALUE_UPDATED") and view.sheet:
        process = repo.get_process(view.sheet)
        if process is not None and process.enabled:
            payload = view.payload or {}
            process_machine.on_event(
                repo,
                process,
                {
                    "type": view.type,
                    "node": payload.get("node"),
                    "column": payload.get("column"),
                    "tree_event": view.name,
                },
                now=str(_utcnow()),
                # notify_on_expect fan-out (expectation-opened → column owner);
                # silent without a sink — the machine gates on notify is not None.
                notify=_process_notifier(repo),
            )


def _repo(session: Session) -> SQLRepository:
    """A fully-wired Repository: notification inserts bridge to webhooks."""
    repo = SQLRepository(session)
    repo.on_notification = lambda name: _on_notification_created(session, name)
    return repo


def _sink(session: Session, repo: SQLRepository) -> SQLEventSink:
    """A fully-wired EventSink: stored events feed the dispatch trio."""
    return SQLEventSink(session, dispatch=lambda ev: _dispatch_tree_event(session, repo, ev))


# ---------------------------------------------------------------------------
# Actor resolution (Area 1 impersonation overlay — mirror of frappe _actor()).
# ---------------------------------------------------------------------------
def _session_user(request: Request) -> str:
    """The REAL authenticated principal from the session cookie (the
    ``frappe.session.user`` analog) — 401 when there is no valid session."""
    user = read_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _is_admin_user(repo: SQLRepository, user: str) -> bool:
    """Platform-admin signal for an arbitrary user (the ``users.is_admin`` flag
    — the standalone System Manager analog)."""
    return user in set(repo.list_admins())


def _actor_real(request: Request, session: Session) -> Actor:
    """The REAL authenticated principal WITHOUT any impersonation overlay, with
    ``is_admin`` computed from the real user's row — the begin/end impersonation
    control caps are gated on THIS (mirror of frappe ``_actor_real``): the
    effective (possibly impersonated, non-admin) identity would wrongly block
    ``endImpersonation`` while an overlay is live."""
    user = _session_user(request)
    row = session.get(m.User, user)
    is_admin = bool(row is not None and row.enabled and row.is_admin)
    return Actor(user=user, actor_type=ActorType.HUMAN, is_admin=is_admin)


def _actor(request: Request, repo: SQLRepository) -> Actor:
    """The acting identity — the auth lane's ``get_current_actor`` already
    mirrors the frappe ``_actor()`` ordering (real principal first, admin
    computed before the overlay, fail-safe force-end, ``is_admin`` recomputed
    from the impersonated user), so this is a straight delegation. ``repo`` is
    kept in the signature for call-site symmetry with the frappe shims."""
    return get_current_actor(request)


# ---------------------------------------------------------------------------
# Arbor Agent Token gate (two-tier auth; mirror of _agent_scope_from_request /
# _enforce_agent_scope over the agent_tokens table + the pure agent_scope).
# ---------------------------------------------------------------------------
def _token_secret() -> str | None:
    """The key for the Agent-Token HMAC (defense if the DB leaks). Env-injected
    in the standalone lane; absent, the hash degrades to plain SHA-256 of the
    (already high-entropy) token — still safe, just not DB-leak-resistant."""
    return os.environ.get("ARBOR_TOKEN_SECRET") or os.environ.get("SECRET_KEY")


def _parse_token_sheets(raw: str | None) -> list[str] | None:
    """The token's sheet allow-list: a JSON array (as the frappe issuer stored
    it) or a newline/comma-separated Small Text. Empty/None = every sheet."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, list):
        out = [str(s) for s in parsed if s]
    else:
        out = [s.strip() for s in re.split(r"[\n,]+", raw) if s.strip()]
    return out or None


def _enforce_agent_scope(
    request: Request, session: Session, actor: Actor, action_id: str, params: dict[str, Any]
) -> None:
    """Apply any ``X-Arbor-Agent-Token`` scope BEFORE the executor, else no-op.

    A present-but-invalid/revoked/expired token is a hard 401; a token not owned
    by the authenticated user is 403; a scope violation is a hard 403 that NEVER
    degrades to a Change Request. Absent header -> first-party path, no-op."""
    token = request.headers.get("X-Arbor-Agent-Token")
    if not token:
        return
    token_hash = hash_token(token, secret=_token_secret())
    row = session.scalars(
        sa.select(m.AgentToken).where(m.AgentToken.token_hash == token_hash)
    ).first()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid Arbor Agent Token")
    if row.revoked or (row.expires_on is not None and row.expires_on <= _utcnow()):
        raise HTTPException(status_code=401, detail="Arbor Agent Token is revoked or expired")
    # The token may only ADD scope to its OWN user — never let A's session + B's
    # token act as B. The session is the identity; the token is a ceiling.
    if row.user != (actor.real_user or actor.user):
        raise HTTPException(
            status_code=403, detail="Agent token does not belong to the authenticated user"
        )
    row.last_used_at = _utcnow()  # best-effort; committed with the request
    sheets = _parse_token_sheets(row.sheets)
    scope = AgentScope(mode=row.mode or "write", sheets=frozenset(sheets) if sheets else None)
    try:
        authorize_scope(scope, action_id, params or {})
    except ScopeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# The ONE dispatch funnel + the frappe-parity error mapping.
# ---------------------------------------------------------------------------
def _outcome_dict(outcome: Outcome) -> dict[str, Any]:
    """Serialize an ``Outcome`` into the stable REST envelope (api.md)."""
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


def _dispatch(
    request: Request,
    session: Session,
    action_id: str,
    params: dict[str, Any],
    actor: Actor | None = None,
) -> dict[str, Any]:
    """Every capability routes here -> ``core.executor.execute_action``, with
    the SAME exception -> HTTP mapping as the frappe ``_dispatch``."""
    repo = _repo(session)
    if actor is None:
        actor = _actor(request, repo)
    sink = _sink(session, repo)
    _enforce_agent_scope(request, session, actor, action_id, params)
    try:
        outcome = executor.execute_action(action_id, params or {}, actor, repo, sink)
    except UnknownCapabilityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SchemaValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AuthorizationError as exc:
        # Control-action denial — 403. A denied MUTATION never reaches here
        # (it becomes a Change Request / "suggested", a 200 outcome).
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except CRStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (StaleVersionError, StaleMoveError, CoreStaleVersionError) as exc:
        # Feature 1 — a lost-update conflict is NOT a 4xx: HTTP 200 with a
        # structured ``read`` Outcome the FE reads off ``outcome.error``
        # (useSheet.ts) carrying the authoritative current state.
        session.rollback()
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
        # Remaining storage conflicts (cycle, duplicate sheet, ...) — 409.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SheetTooLargeError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "sheet_too_large": {
                    "count": exc.count,
                    "threshold": exc.threshold,
                    "explore_tools": list(exc.EXPLORE_TOOLS),
                },
            },
        ) from exc
    except CellBudgetExceededError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        # Standalone NotFoundError subclasses KeyError; the in-memory-contract
        # read misses (unknown CR/notification/subscription) raise bare KeyError.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        # Bad keyset cursor / unknown explore node / blank sheet title — 400.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _outcome_dict(outcome)


# ---------------------------------------------------------------------------
# App + routes. Every path mirrors ``/api/method/arbor.<name>`` exactly.
# ---------------------------------------------------------------------------
_STOP = threading.Event()


def _run_webhook_retries() -> None:
    session = SessionLocal()
    try:
        _webhook_dispatcher(session).run_retries()
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def _process_notifier(repo: "SQLRepository"):
    """The process/SLA notify sink (the SQL analog of FrappeProcessNotifier):
    ONE ``source in {'process','sla'}`` in-app Notification row per recipient via
    ``repo.create_notification`` — which also bridges to webhook fan-out. FYI
    only (``requires_ack`` False) so process rows never pollute accountability.
    Without this sink the pure machine's notify branches are SILENT (they gate
    on ``notify is not None``) and notify_on_expect / sla_breach_notify are inert.
    """

    def notify(recipients: list[str], data: dict[str, Any]) -> None:
        for r in recipients:
            # Only store-known fields: anything else lands in the JSON `extra`
            # column, and non-JSON values (datetimes) blow up the insert. The
            # row's own creation timestamp serves as the delivery time.
            repo.create_notification(
                {
                    "source": data.get("source", "process"),
                    "recipient": r,
                    "channel": "in-app",
                    "requires_ack": False,
                }
            )

    return notify


def _run_sla_sweep() -> None:
    session = SessionLocal()
    try:
        repo = _repo(session)
        process_machine.sla_sweep(
            repo,
            str(_utcnow()),
            process_of=repo.get_process_by_name,
            notify=_process_notifier(repo),
        )
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def _retry_loop() -> None:
    while not _STOP.wait(30.0):
        _run_webhook_retries()


def _sla_loop() -> None:
    while not _STOP.wait(60.0):
        _run_sla_sweep()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Background runners (no celery/redis): webhook retries on the core backoff
    # schedule (a 30s tick is fine-grained enough for the 30s slot) and the
    # process SLA sweep (frappe ran both on the per-minute scheduler).
    if os.environ.get("ARBOR_NO_BACKGROUND") != "1":
        threading.Thread(target=_retry_loop, name="arbor-webhook-retries", daemon=True).start()
        threading.Thread(target=_sla_loop, name="arbor-sla-sweep", daemon=True).start()
    yield
    _STOP.set()


app = FastAPI(title="arbor-standalone", lifespan=_lifespan)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True}


# ---- generic dispatch + sheet bootstrap -------------------------------------
@app.post("/api/method/arbor.execute_action")
def execute_action(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    payload = payload or {}
    action_id = payload.get("action_id")
    if not action_id:
        raise HTTPException(status_code=400, detail="action_id is required")
    return _msg(_dispatch(request, session, action_id, payload.get("params") or {}))


@app.post("/api/method/arbor.create_sheet")
def create_sheet(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    """Dispatch the ``createSheet`` capability, then add the historical
    top-level ``sheet`` key beside the Outcome envelope (the FE reads
    ``{sheet}``). Duplicate name -> 409; blank name+title -> 400."""
    payload = payload or {}
    name = str(payload.get("name") or "").strip()
    title = payload.get("title")
    title = title.strip() if isinstance(title, str) else None
    body = _dispatch(
        request,
        session,
        "createSheet",
        {"title": title or name, "name": name or None, "label_column": payload.get("label") or "Item"},
    )
    body["sheet"] = (body.get("data") or {}).get("sheet")
    return _msg(body)


# ---- impersonation (Area 1) — dispatched AS the REAL principal --------------
@app.post("/api/method/arbor.begin_impersonation")
def begin_impersonation(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    payload = payload or {}
    return _msg(
        _dispatch(
            request,
            session,
            "beginImpersonation",
            {"impersonated_user": payload.get("impersonated_user"), "reason": payload.get("reason")},
            actor=_actor_real(request, session),
        )
    )


@app.post("/api/method/arbor.end_impersonation")
def end_impersonation(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    return _msg(
        _dispatch(request, session, "endImpersonation", {}, actor=_actor_real(request, session))
    )


# ---- snapshot + definition ---------------------------------------------------
@app.get("/api/method/arbor.get_sheet_snapshot")
def get_sheet_snapshot(request: Request, sheet: str, session: Session = Depends(get_db)):
    repo = _repo(session)
    actor = _actor(request, repo)
    # This endpoint reaches the executor directly (not via _dispatch), so apply
    # the agent-token scope here too (a sheet-scoped token must not bulk-read
    # outside its scope).
    _enforce_agent_scope(request, session, actor, "getSheetSnapshot", {"sheet": sheet})
    if session.get(m.Sheet, sheet) is None:
        raise HTTPException(status_code=404, detail=f"No such sheet {sheet}")
    # Flow through the executor for parity (the >EXPLORE_THRESHOLD size guard).
    try:
        executor.execute_action(
            "getSheetSnapshot", {"sheet": sheet}, actor, repo, _sink(session, repo)
        )
    except SheetTooLargeError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "sheet_too_large": {
                    "count": exc.count,
                    "threshold": exc.threshold,
                    "explore_tools": list(exc.EXPLORE_TOOLS),
                },
            },
        ) from exc
    return _msg(build_sheet_snapshot(session, repo, sheet, actor))


@app.get("/api/method/arbor.get_sheet_definition")
def get_sheet_definition(request: Request, sheet: str, session: Session = Depends(get_db)):
    return _msg(_dispatch(request, session, "getSheetDefinition", {"sheet": sheet}))


# ---- sheet catalog -------------------------------------------------------------
@app.get("/api/method/arbor.list_sheets")
def list_sheets(request: Request, session: Session = Depends(get_db)):
    repo = _repo(session)
    _actor(request, repo)  # 401 gate
    counts = {
        sheet: count
        for sheet, count in session.execute(
            sa.select(m.Node.sheet, sa.func.count()).group_by(m.Node.sheet)
        ).all()
        if sheet
    }
    rows = session.execute(
        sa.select(m.Sheet.name, m.Sheet.structural_owner).order_by(m.Sheet.modified.desc())
    ).all()
    return _msg(
        [
            {"name": name, "structural_owner": owner, "node_count": int(counts.get(name, 0))}
            for name, owner in rows
        ]
    )


# ---- change requests (review inbox) --------------------------------------------
def _viewer_can_decide(cr: dict[str, Any], user: str, repo: SQLRepository) -> bool:
    """Whether ``user`` may approve/reject at least one part of ``cr`` now
    (core approver re-resolution — mirror of the frappe shim)."""
    items = cr.get("changes") or []
    if items:
        for it in items:
            syn = _synthetic_item_cr(cr, it)
            approver, co = _reresolve_approver(syn, repo)
            if user in ({approver} | set(co) | _column_editor_approvers(syn, repo)):
                return True
        return False
    approver, co = _reresolve_approver(cr, repo)
    return user in ({approver} | set(co) | _column_editor_approvers(cr, repo))


@app.get("/api/method/arbor.list_change_requests")
def list_change_requests(
    request: Request, sheet: str, status: str = "proposed", session: Session = Depends(get_db)
):
    repo = _repo(session)
    actor = _actor(request, repo)
    names = session.scalars(
        sa.select(m.ChangeRequest.name)
        .where(m.ChangeRequest.sheet == sheet, m.ChangeRequest.status == status)
        .order_by(m.ChangeRequest.creation.asc())
    ).all()
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
    return _msg(out)


# ---- notifications / inbox ----------------------------------------------------
# Human verbs for the in-app notification message (display only).
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


def _acked(session: Session, notification: str, user: str) -> bool:
    return bool(
        session.scalar(
            sa.select(m.Acknowledgement.name).where(
                m.Acknowledgement.notification == notification,
                m.Acknowledgement.user == user,
            )
        )
    )


@app.get("/api/method/arbor.list_notifications")
def list_notifications(request: Request, sheet: str, session: Session = Depends(get_db)):
    repo = _repo(session)
    actor = _actor(request, repo)
    rows = session.scalars(
        sa.select(m.Notification)
        .where(m.Notification.recipient == actor.user, m.Notification.channel == "in-app")
        .order_by(m.Notification.creation.desc())
    ).all()
    out = []
    for r in rows:
        # Branch on ``source`` to resolve the owning sheet: comment rows carry
        # tree_event=NULL, so their sheet resolves from the linked comment.
        source = r.source or "tree_event"
        if source == "comment":
            if not r.comment:
                continue
            c = session.get(m.CellComment, r.comment)
            if c is None or c.sheet != sheet:
                continue
            out.append(
                {
                    "name": r.name,
                    "event_type": "COMMENT_ADDED",
                    "message": f"{c.author} commented on a cell",
                    "requires_ack": bool(r.requires_ack),
                    "acked": _acked(session, r.name, actor.user),
                }
            )
        else:
            ev = session.get(m.TreeEventRow, r.tree_event) if r.tree_event else None
            if ev is None or ev.sheet != sheet:
                continue
            out.append(
                {
                    "name": r.name,
                    "event_type": ev.type,
                    "message": f"{ev.actor} {_NOTIF_VERB.get(ev.type, ev.type)}",
                    "requires_ack": bool(r.requires_ack),
                    "acked": _acked(session, r.name, actor.user),
                }
            )
    return _msg(out)


def _inbox_process_context(
    session: Session, repo: SQLRepository, actor: Actor
) -> list[dict[str, Any]]:
    """The viewer's process work across all sheets (mirror of the frappe
    ``_inbox_process_context``): for every run that notified the viewer via an
    expectation's ``notified_owner`` ledger, OR that carries an OPEN expectation
    whose column the viewer LIVE-owns, a ``{sheet, node, rule_key, column}``
    deep-link context, newest run first."""
    ctx: list[dict[str, Any]] = []
    runs = session.scalars(
        sa.select(m.ProcessRun).order_by(m.ProcessRun.creation.desc())
    ).all()
    for run in runs:
        exps = session.scalars(
            sa.select(m.ProcessRunExpectation)
            .where(m.ProcessRunExpectation.run == run.name)
            .order_by(m.ProcessRunExpectation.name)
        ).all()
        matched = None
        for e in exps:
            notified = (e.notified_owner or "").split(",") if e.notified_owner else []
            live_owner = (
                run.status == "active"
                and e.satisfied_at is None
                and not e.breached
                and actor.user in resolve_column_approvers(repo, run.sheet, e.expected_column)
            )
            if actor.user in notified or live_owner:
                matched = {"rule_key": e.rule_key, "column": e.expected_column}
                break
        if matched is not None:
            ctx.append({"sheet": run.sheet, "node": run.node, **matched})
    return ctx


@app.get("/api/method/arbor.inbox")
def inbox(request: Request, session: Session = Depends(get_db)):
    repo = _repo(session)
    actor = _actor(request, repo)
    rows = session.scalars(
        sa.select(m.Notification)
        .where(m.Notification.recipient == actor.user, m.Notification.channel == "in-app")
        .order_by(m.Notification.creation.desc())
    ).all()
    process_ctx = _inbox_process_context(session, repo, actor)
    proc_cursor = 0
    out = []
    for r in rows:
        source = r.source or "tree_event"
        acked = _acked(session, r.name, actor.user)
        if source in ("process", "sla"):
            ctx = process_ctx[proc_cursor] if proc_cursor < len(process_ctx) else None
            proc_cursor += 1
            if source == "sla":
                event_type, verb = "PROCESS_SLA_DUE", "a process stage is overdue"
            else:
                event_type, verb = "PROCESS_STAGE_ASSIGNED", "a process stage is waiting on you"
            out.append(
                {
                    "name": r.name,
                    "source": source,
                    "event_type": event_type,
                    "message": verb,
                    "sheet": ctx.get("sheet") if ctx else None,
                    "node": ctx.get("node") if ctx else None,
                    "requires_ack": bool(r.requires_ack),
                    "acked": acked,
                }
            )
        elif source == "comment":
            if not r.comment:
                continue
            c = session.get(m.CellComment, r.comment)
            if c is None:
                continue
            out.append(
                {
                    "name": r.name,
                    "source": "comment",
                    "event_type": "COMMENT_ADDED",
                    "message": f"{c.author} commented on a cell",
                    "sheet": c.sheet,
                    "node": c.node,
                    "requires_ack": bool(r.requires_ack),
                    "acked": acked,
                }
            )
        else:
            ev = session.get(m.TreeEventRow, r.tree_event) if r.tree_event else None
            if ev is None:
                continue
            payload = ev.payload or {}
            out.append(
                {
                    "name": r.name,
                    "source": "tree_event",
                    "event_type": ev.type,
                    "message": f"{ev.actor} {_NOTIF_VERB.get(ev.type, ev.type)}",
                    "sheet": ev.sheet,
                    "node": payload.get("node"),
                    "requires_ack": bool(r.requires_ack),
                    "acked": acked,
                }
            )
    return _msg(out)


# ---- activity feed --------------------------------------------------------------
# Friendly PAST-TENSE verbs for the activity feed, keyed by EventType.
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


def _node_label(repo: SQLRepository, sheet: str, label_col: str | None, node: str | None):
    """The human label of ``node`` (value of the label column), or the raw node
    id. Labels are ALWAYS readable (``can_read_column`` short-circuits on
    ``is_label``), so no ACL gate here."""
    if not node:
        return None
    if label_col is not None:
        val = repo.get_value(node, label_col)
        if val:
            return val
    return node


def _activity_summary(ev_type, actor_name, payload, repo, sheet, actor, label_col):
    """The human one-liner for one Tree Event, resolving node/column LABELS and
    REDACTING any column the viewer cannot read. NEVER a raw cell value."""
    payload = payload or {}
    verb = _ACTIVITY_VERB.get(ev_type, ev_type)

    if ev_type == "NODE_CREATED":
        label = _node_label(repo, sheet, label_col, payload.get("node"))
        return f"{actor_name} added {label}" if label else f"{actor_name} added a node"

    if ev_type == "NODE_DELETED":
        return f"{actor_name} deleted a node"

    if ev_type == "NODE_MOVED":
        label = _node_label(repo, sheet, label_col, payload.get("node"))
        return f"{actor_name} moved {label}" if label else f"{actor_name} moved a node"

    if ev_type == "NODE_VALUE_UPDATED":
        col_label, readable = _readable_column_label(repo, sheet, actor, payload.get("column"))
        node_label = _node_label(repo, sheet, label_col, payload.get("node"))
        if readable and node_label:
            return f"{actor_name} updated the {col_label} of {node_label}"
        if readable:
            return f"{actor_name} updated the {col_label}"
        if node_label:
            return f"{actor_name} updated a cell of {node_label}"
        return f"{actor_name} updated a cell"

    if ev_type == "COLUMN_CONFIG_UPDATED":
        col_label, readable = _readable_column_label(repo, sheet, actor, payload.get("column"))
        op = payload.get("op")
        action = {"add": "added", "delete": "deleted", "grant": "changed access to"}.get(
            op, "changed"
        )
        if readable:
            return f"{actor_name} {action} the {col_label} column"
        return f"{actor_name} {action} a column"

    return f"{actor_name} {verb}"


def _encode_activity_cursor(creation: Any, name: str) -> str:
    raw = f"{creation}|{name}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_activity_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    """Decode the OPAQUE base64 ``creation|name`` keyset cursor; a malformed
    token is a 400 (a bad cursor is a client error)."""
    if cursor is None or cursor == "":
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        creation_s, name = raw.split("|", 1)
        return datetime.fromisoformat(creation_s), name
    except Exception as exc:  # noqa: BLE001 — normalize to the client error
        raise HTTPException(status_code=400, detail=f"malformed cursor: {cursor!r}") from exc


@app.get("/api/method/arbor.list_activity")
def list_activity(
    request: Request,
    sheet: str,
    limit: int = 50,
    before: str | None = None,
    type: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    session: Session = Depends(get_db),
):
    repo = _repo(session)
    viewer = _actor(request, repo)
    label_col = next((c.name for c in repo.list_columns(sheet) if c.is_label), None)
    boundary = _decode_activity_cursor(before)

    # Keyset WHERE: sheet scope + optional type/actor + the strictly-older
    # boundary (creation < c OR (creation = c AND name < n)); newest first;
    # limit+1 sentinel decides next_cursor.
    stmt = sa.select(m.TreeEventRow).where(m.TreeEventRow.sheet == sheet)
    if type:
        stmt = stmt.where(m.TreeEventRow.type == type)
    if actor:
        stmt = stmt.where(m.TreeEventRow.actor == actor)
    if boundary is not None:
        c_creation, c_name = boundary
        stmt = stmt.where(
            sa.or_(
                m.TreeEventRow.creation < c_creation,
                sa.and_(
                    m.TreeEventRow.creation == c_creation, m.TreeEventRow.name < c_name
                ),
            )
        )
    stmt = stmt.order_by(m.TreeEventRow.creation.desc(), m.TreeEventRow.name.desc()).limit(
        int(limit) + 1
    )
    rows = list(session.scalars(stmt).all())

    has_more = len(rows) > int(limit)
    page = rows[: int(limit)]
    next_cursor = (
        _encode_activity_cursor(page[-1].creation, page[-1].name) if has_more and page else None
    )

    out = []
    for r in page:
        payload = r.payload or {}
        node_id = payload.get("node")
        node_label = _node_label(repo, sheet, label_col, node_id) if node_id else None
        col_label, col_readable = _readable_column_label(repo, sheet, viewer, payload.get("column"))
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
    return _msg({"events": out, "next_cursor": next_cursor})


# ---- roles (Feature: roles) — read shims ----------------------------------------
@app.get("/api/method/arbor.list_roles")
def list_roles(request: Request, session: Session = Depends(get_db)):
    repo = _repo(session)
    actor = _actor(request, repo)
    held = set(
        session.scalars(
            sa.select(m.RoleGrant.role).where(
                m.RoleGrant.grantee == actor.user, m.RoleGrant.active.is_(True)
            )
        ).all()
    )
    open_apps = set(
        session.scalars(
            sa.select(m.RoleApplication.role).where(
                m.RoleApplication.requester == actor.user,
                m.RoleApplication.status == "proposed",
            )
        ).all()
    )
    rows = session.scalars(sa.select(m.Role).order_by(m.Role.label.asc())).all()
    return _msg(
        [
            {
                "role": r.role,
                "label": r.label,
                "description": r.description,
                "applicable": bool(r.applicable),
                "active": bool(r.active),
                "viewer_holds": r.role in held,
                "viewer_has_open_application": r.role in open_apps,
            }
            for r in rows
        ]
    )


@app.get("/api/method/arbor.list_role_grants")
def list_role_grants(
    request: Request,
    role: str | None = None,
    grantee: str | None = None,
    session: Session = Depends(get_db),
):
    repo = _repo(session)
    actor = _actor(request, repo)
    stmt = sa.select(m.RoleGrant).where(m.RoleGrant.active.is_(True))
    if role:
        stmt = stmt.where(m.RoleGrant.role == role)
    if grantee:
        stmt = stmt.where(m.RoleGrant.grantee == grantee)
    rows = session.scalars(stmt.order_by(m.RoleGrant.creation.asc())).all()
    return _msg(
        [
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
    )


@app.get("/api/method/arbor.list_role_applications")
def list_role_applications(
    request: Request,
    status: str = "proposed",
    requester: str | None = None,
    session: Session = Depends(get_db),
):
    repo = _repo(session)
    actor = _actor(request, repo)
    stmt = sa.select(m.RoleApplication)
    if status:
        stmt = stmt.where(m.RoleApplication.status == status)
    if requester:
        stmt = stmt.where(m.RoleApplication.requester == requester)
    rows = session.scalars(stmt.order_by(m.RoleApplication.creation.desc())).all()
    return _msg(
        [
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
    )


# ---- platform-admin operations (roles + users) -----------------------------------
# Standalone-only PLATFORM-ADMIN face (like internalReset): these never become
# registry capabilities and are never exposed to LLM tools. Hard 403 for a
# non-admin — never a Change Request.
_ROLE_KEY_RE = re.compile(r"^[a-z0-9\-_]+$")


def _require_admin(actor: Actor) -> None:
    """AUTHZ: platform admin only (403 otherwise; never a CR — this is an
    out-of-band admin surface, the webhook-admin gate's sitewide twin)."""
    if not getattr(actor, "is_admin", False):
        raise HTTPException(status_code=403, detail="Only an admin may perform this operation")


def _role_out(row: m.Role) -> dict[str, Any]:
    return {
        "role": row.role,
        "label": row.label,
        "description": row.description,
        "applicable": bool(row.applicable),
        "active": bool(row.active),
    }


@app.post("/api/method/arbor.admin.create_role")
def admin_create_role(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    """Create a Role. The role key is normalized (trim + lowercase) and must
    match ``[a-z0-9-_]+`` (400 otherwise); an existing key is a 409 — creation
    is NOT an upsert (``update_role`` owns edits)."""
    payload = payload or {}
    repo = _repo(session)
    actor = _actor(request, repo)
    _require_admin(actor)

    role = str(payload.get("role") or "").strip().lower()
    if not _ROLE_KEY_RE.match(role):
        raise HTTPException(
            status_code=400, detail="role key must match [a-z0-9-_]+ (trimmed, lowercase)"
        )
    if session.get(m.Role, role) is not None:
        raise HTTPException(status_code=409, detail=f"Role {role} already exists")

    label = payload.get("label")
    label = label.strip() if isinstance(label, str) else ""
    row = m.Role(
        name=role,  # frappe autonamed by field:role — the PK IS the role key
        role=role,
        label=label or role,
        description=payload.get("description"),
        applicable=bool(payload.get("applicable", True)),
        active=bool(payload.get("active", True)),
    )
    session.add(row)
    session.flush()
    return _msg(_role_out(row))


@app.post("/api/method/arbor.admin.update_role")
def admin_update_role(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    """Patch label/description/applicable/active of one Role (404 unknown key).
    There is NO delete — ``active=false`` is the soft retire, matching the
    frappe-era semantics (the grant ledger keeps pointing at the row)."""
    payload = payload or {}
    repo = _repo(session)
    actor = _actor(request, repo)
    _require_admin(actor)

    role = str(payload.get("role") or "").strip().lower()
    row = session.get(m.Role, role)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No such role {role}")

    patch = payload.get("patch") or {}
    if patch.get("label") is not None:
        row.label = str(patch["label"])
    if "description" in patch:
        row.description = patch["description"]
    if patch.get("applicable") is not None:
        row.applicable = bool(patch["applicable"])
    if patch.get("active") is not None:
        row.active = bool(patch["active"])
    session.flush()
    return _msg(_role_out(row))


@app.get("/api/method/arbor.admin.list_users")
def admin_list_users(request: Request, session: Session = Depends(get_db)):
    repo = _repo(session)
    actor = _actor(request, repo)
    _require_admin(actor)
    rows = session.scalars(sa.select(m.User).order_by(m.User.creation.asc())).all()
    return _msg(
        [
            {
                "email": r.email,
                "full_name": r.full_name,
                "is_admin": bool(r.is_admin),
                "enabled": bool(r.enabled),
                "creation": str(r.creation),
            }
            for r in rows
        ]
    )


@app.post("/api/method/arbor.admin.set_user")
def admin_set_user(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    """Patch ``is_admin``/``enabled`` of one users row (404 unknown email).
    GUARD: an admin may never demote or disable THEMSELVES — the last admin
    locking the whole site out is exactly the failure this prevents."""
    payload = payload or {}
    repo = _repo(session)
    actor = _actor(request, repo)
    _require_admin(actor)

    email = str(payload.get("email") or "").strip().lower()
    row = session.get(m.User, email)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No such user {email}")

    demoting = payload.get("is_admin") is not None and not payload["is_admin"]
    disabling = payload.get("enabled") is not None and not payload["enabled"]
    # Both identities of an impersonating admin count as "self" here.
    if (demoting or disabling) and email in {actor.user, actor.real_user or actor.user}:
        raise HTTPException(status_code=400, detail="cannot remove your own admin/access")

    if payload.get("is_admin") is not None:
        row.is_admin = bool(payload["is_admin"])
    if payload.get("enabled") is not None:
        row.enabled = bool(payload["enabled"])
    session.flush()
    return _msg(
        {"email": row.email, "is_admin": bool(row.is_admin), "enabled": bool(row.enabled)}
    )


# ---- personal cell-draft box (Feature: cell drafts) ------------------------------
def _find_cell_draft(session: Session, user: str, sheet, node, column) -> CellDraft | None:
    return session.scalars(
        sa.select(CellDraft).where(
            CellDraft.user == user,
            CellDraft.sheet == sheet,
            CellDraft.node == node,
            CellDraft.column == column,
        )
    ).first()


@app.post("/api/method/arbor.save_cell_draft")
def save_cell_draft(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    """Upsert the actor's draft for one cell, keyed (user, sheet, node, column)
    — two saves on the same cell collapse to ONE draft holding the latest value."""
    payload = payload or {}
    repo = _repo(session)
    actor = _actor(request, repo)
    bv = payload.get("base_version")
    bv = int(bv) if bv not in (None, "") else None
    existing = _find_cell_draft(
        session, actor.user, payload.get("sheet"), payload.get("node"), payload.get("column")
    )
    if existing is not None:
        existing.value = payload.get("value")
        existing.base_version = bv
        session.flush()
        return _msg({"name": existing.name})
    row = CellDraft(
        user=actor.user,
        sheet=payload.get("sheet"),
        node=payload.get("node"),
        column=payload.get("column"),
        value=payload.get("value"),
        base_version=bv,
    )
    session.add(row)
    session.flush()
    return _msg({"name": row.name})


@app.get("/api/method/arbor.list_cell_drafts")
def list_cell_drafts(request: Request, sheet: str, session: Session = Depends(get_db)):
    repo = _repo(session)
    actor = _actor(request, repo)
    rows = session.scalars(
        sa.select(CellDraft)
        .where(CellDraft.user == actor.user, CellDraft.sheet == sheet)
        .order_by(CellDraft.creation.asc())
    ).all()
    return _msg(
        [
            {
                "name": r.name,
                "node": r.node,
                "column": r.column,
                "value": r.value,
                "base_version": r.base_version,
            }
            for r in rows
        ]
    )


@app.post("/api/method/arbor.discard_cell_draft")
def discard_cell_draft(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    payload = payload or {}
    repo = _repo(session)
    actor = _actor(request, repo)
    existing = _find_cell_draft(
        session, actor.user, payload.get("sheet"), payload.get("node"), payload.get("column")
    )
    if existing is not None:
        session.delete(existing)
        session.flush()
    return _msg({"ok": True})


@app.post("/api/method/arbor.discard_cell_drafts")
def discard_cell_drafts(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    payload = payload or {}
    repo = _repo(session)
    actor = _actor(request, repo)
    rows = session.scalars(
        sa.select(CellDraft).where(
            CellDraft.user == actor.user, CellDraft.sheet == payload.get("sheet")
        )
    ).all()
    for r in rows:
        session.delete(r)
    session.flush()
    return _msg({"discarded": len(rows)})


@app.post("/api/method/arbor.submit_cell_drafts")
def submit_cell_drafts(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    """Promote ALL the actor's drafts for the sheet into ONE multi-change CR via
    the ``suggestChanges`` funnel, then delete the submitted drafts. An empty
    box is a no-op returning ``{kind:"read", data:{}}`` (no CR created)."""
    payload = payload or {}
    sheet = payload.get("sheet")
    repo = _repo(session)
    actor = _actor(request, repo)
    rows = session.scalars(
        sa.select(CellDraft)
        .where(CellDraft.user == actor.user, CellDraft.sheet == sheet)
        .order_by(CellDraft.creation.asc())
    ).all()
    if not rows:
        return _msg({"kind": "read", "data": {}})

    changes = []
    for r in rows:
        params: dict[str, Any] = {
            "sheet": sheet,
            "node": r.node,
            "column": r.column,
            "value": r.value,
        }
        if r.base_version is not None:
            params["base_version"] = int(r.base_version)
        changes.append({"action": "updateCell", "params": params})

    body = _dispatch(request, session, "suggestChanges", {"sheet": sheet, "changes": changes}, actor=actor)

    # Promoted to a CR — clear the box.
    for r in rows:
        session.delete(r)
    session.flush()
    return _msg(body)


# ---- per-cell comments (Area 2) ---------------------------------------------------
_MENTION_RE = re.compile(
    r"(?<![\w.])@([A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|[A-Za-z0-9._-]+)"
)


def _extract_mentions(body: str) -> list[str]:
    """Parse ``@token`` mentions into candidate User ids, de-duplicated and
    order-preserving (the read-ACL filter + existence check are the caller's)."""
    if not body:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in _MENTION_RE.finditer(body):
        tok = match.group(1)
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _require_readable_cell(session: Session, repo: SQLRepository, sheet, node, column, actor):
    """Assert the cell exists and ``actor`` may READ its column; 404 / 403
    otherwise (never leak an owner-only cell's existence). Returns the view."""
    if session.get(m.Sheet, sheet) is None:
        raise HTTPException(status_code=404, detail=f"No such sheet {sheet}")
    node_row = session.get(m.Node, node)
    if node_row is None or node_row.sheet != sheet:
        raise HTTPException(status_code=404, detail=f"No such node {node}")
    try:
        col = repo.get_column(sheet, column)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"No such column {column}") from exc
    if not can_read_column(repo, sheet, col, actor):
        raise HTTPException(status_code=403, detail="You do not have access to this column")
    return col


def _can_resolve_comment(repo: SQLRepository, sheet: str, column: str, actor: Actor) -> bool:
    if getattr(actor, "is_admin", False):
        return True
    return actor.user in resolve_column_approvers(repo, sheet, column)


@app.post("/api/method/arbor.add_cell_comment")
def add_cell_comment(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    """Post a comment (or reply). THIN over the ``addComment`` capability, plus
    the api-layer extras the frappe shim carried: 400 empty body, 404 unknown
    parent, @mention parse filtered to existing users who can read the column,
    then the in-app Notification fan-out (column approvers + mentions, minus the
    author; idempotent per (comment, recipient))."""
    payload = payload or {}
    sheet, node, column = payload.get("sheet"), payload.get("node"), payload.get("column")
    repo = _repo(session)
    actor = _actor(request, repo)
    _require_readable_cell(session, repo, sheet, node, column, actor)

    body = payload.get("body")
    body = body.strip() if isinstance(body, str) else ""
    if not body:
        raise HTTPException(status_code=400, detail="Comment body must not be empty")

    parent_comment = payload.get("parent_comment")
    if parent_comment and session.get(m.CellComment, parent_comment) is None:
        raise HTTPException(status_code=404, detail=f"No such comment {parent_comment}")

    # @mentions: existing Users who can STILL read the column (a mention of a
    # non-reader is silently dropped — never signal an owner-only cell). The
    # client's own ``mentions`` field is deliberately ignored.
    col = repo.get_column(sheet, column)
    mentions: list[str] = []
    for tok in _extract_mentions(body):
        if session.get(m.User, tok) is None:
            continue
        mentioned = Actor(
            user=tok, actor_type=ActorType.HUMAN, is_admin=_is_admin_user(repo, tok)
        )
        if can_read_column(repo, sheet, col, mentioned):
            mentions.append(tok)

    outcome = _dispatch(
        request,
        session,
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

    # FYI fan-out (a comment is NOT a Tree Event): column approvers + surviving
    # mentions, minus the author; idempotent per (comment, recipient, in-app).
    recipients = set(resolve_column_approvers(repo, sheet, column)) | set(mentions)
    recipients.discard(actor.user)
    for recipient in sorted(recipients):
        exists = session.scalar(
            sa.select(m.Notification.name).where(
                m.Notification.comment == comment_name,
                m.Notification.recipient == recipient,
                m.Notification.channel == "in-app",
            )
        )
        if exists:
            continue
        repo.create_notification(
            {
                "source": "comment",
                "comment": comment_name,
                "tree_event": None,
                "recipient": recipient,
                "channel": "in-app",
                "requires_ack": False,
            }
        )

    return _msg({"name": comment_name, "thread_root": thread_root, "mentions": mentions})


@app.get("/api/method/arbor.list_cell_comments")
def list_cell_comments(
    request: Request, sheet: str, node: str, column: str, session: Session = Depends(get_db)
):
    repo = _repo(session)
    actor = _actor(request, repo)
    _require_readable_cell(session, repo, sheet, node, column, actor)

    can_resolve = _can_resolve_comment(repo, sheet, column, actor)
    rows = session.scalars(
        sa.select(m.CellComment)
        .where(
            m.CellComment.sheet == sheet,
            m.CellComment.node == node,
            m.CellComment.column == column,
            m.CellComment.deleted.is_(False),
        )
        .order_by(m.CellComment.creation.asc(), m.CellComment.name.asc())
    ).all()
    return _msg(
        [
            {
                "name": r.name,
                "thread_root": r.thread_root,
                "parent_comment": r.parent_comment,
                "author": r.author,
                "body": r.body,
                "mentions": list(r.mentions or []),
                "resolved": bool(r.resolved),
                "resolved_by": r.resolved_by,
                "resolved_at": str(r.resolved_at) if r.resolved_at else None,
                "timestamp": str(r.creation),
                "can_resolve": can_resolve,
                "can_delete": (actor.user == r.author) or can_resolve,
            }
            for r in rows
        ]
    )


@app.post("/api/method/arbor.resolve_cell_comment")
def resolve_cell_comment(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    payload = payload or {}
    want = bool(payload.get("resolved", True))
    outcome = _dispatch(
        request, session, "resolveComment", {"comment": payload.get("comment"), "resolved": want}
    )
    data = outcome.get("data") or {}
    return _msg({"name": data.get("comment"), "resolved": bool(data.get("resolved"))})


@app.post("/api/method/arbor.delete_cell_comment")
def delete_cell_comment(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    payload = payload or {}
    _dispatch(request, session, "deleteComment", {"comment": payload.get("comment")})
    return _msg({"ok": True, "tombstoned": True})


# ---- process / SLA reads (Area 3) ---------------------------------------------------
def _process_view_dict(repo: SQLRepository, actor: Actor, process) -> dict[str, Any]:
    """Process metadata + the ONE pure per-rule view builder (read-ACL redaction
    + live owner resolution live in ``core.explore.process_rule_views``)."""
    return {
        "name": process.name,
        "sheet": process.sheet,
        "title": process.title,
        "enabled": bool(process.enabled),
        "row_scope": process.row_scope,
        "sla_breach_notify": bool(process.sla_breach_notify),
        "rules": process_rule_views(repo, process.sheet, actor, process),
    }


@app.get("/api/method/arbor.get_process")
def get_process(request: Request, sheet: str, session: Session = Depends(get_db)):
    repo = _repo(session)
    actor = _actor(request, repo)
    process = repo.get_process(sheet)
    if process is None:
        return _msg(None)
    return _msg(_process_view_dict(repo, actor, process))


@app.get("/api/method/arbor.process_dashboard")
def process_dashboard(request: Request, sheet: str, session: Session = Depends(get_db)):
    repo = _repo(session)
    actor = _actor(request, repo)
    process = repo.get_process(sheet)
    if process is None:
        return _msg(None)
    runs = repo.list_process_runs(sheet)
    agg = process_machine.dashboard_aggregate(process, runs)
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
        to_label, to_readable = _readable_column_label(repo, sheet, actor, edge.get("to_column"))
        edge["to_label"] = to_label
        if not to_readable:
            edge["to_column"] = None
    return _msg(agg)


@app.get("/api/method/arbor.list_process_runs")
def list_process_runs(
    request: Request,
    sheet: str,
    rule_key: str | None = None,
    column: str | None = None,
    status: str | None = None,
    session: Session = Depends(get_db),
):
    repo = _repo(session)
    actor = _actor(request, repo)
    label_col = next((c.name for c in repo.list_columns(sheet) if c.is_label), None)
    out = []
    for run in repo.list_process_runs(sheet, status=status):
        exps = run.get("expectations") or []
        # Edge drill-down filters: keep the run iff a matching expectation exists.
        if rule_key is not None and not any(e.get("rule_key") == rule_key for e in exps):
            continue
        if column is not None and not any(e.get("expected_column") == column for e in exps):
            continue
        expectations = []
        for e in exps:
            label, readable = _readable_column_label(repo, sheet, actor, e.get("expected_column"))
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
    return _msg(out)


# ---- webhooks (Area 3, WS-A3c) — owner/admin surface, never a capability -----------
#: The NON-tree-event notification sources an endpoint may subscribe to.
_WEBHOOK_NOTIFICATION_SOURCES = ("comment", "process", "sla", "change_request")

#: Hostnames that resolve to a cloud metadata / link-local service.
_SSRF_DENY_HOSTNAMES = frozenset({"metadata", "metadata.google.internal"})


class WebhookURLError(Exception):
    """A webhook URL failed validation (bad scheme, unparseable, or resolves to
    a blocked SSRF target). Surfaced as a 400."""


def _ip_is_blocked(ip) -> bool:
    """Deny-by-default SSRF classifier: loopback, link-local (incl. the
    169.254.169.254 metadata address), private, reserved, multicast, and
    unspecified ranges; also the IPv6 forms embedding a blocked v4."""
    if (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return True
    embedded = getattr(ip, "ipv4_mapped", None) or getattr(ip, "sixtofour", None)
    if embedded is not None and _ip_is_blocked(embedded):
        return True
    return False


def _validate_webhook_url(url: str) -> str:
    """Return ``url`` if it is a public http(s) endpoint; else raise
    :class:`WebhookURLError`. EVERY DNS record must be public (a split-horizon
    name with one private record is refused); unresolvable is refused too."""
    if not isinstance(url, str) or not url.strip():
        raise WebhookURLError("Webhook URL is required")
    from urllib.parse import urlsplit

    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise WebhookURLError(f"Webhook URL must be http(s), not {parts.scheme or '(none)'}")
    host = parts.hostname
    if not host:
        raise WebhookURLError("Webhook URL has no host")
    if host.lower() in _SSRF_DENY_HOSTNAMES:
        raise WebhookURLError(f"Webhook URL host {host} is not allowed")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _ip_is_blocked(literal):
            raise WebhookURLError(f"Webhook URL resolves to a blocked address {host}")
        return url.strip()

    try:
        infos = socket.getaddrinfo(host, parts.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise WebhookURLError(f"Webhook URL host {host} does not resolve") from exc
    if not infos:
        raise WebhookURLError(f"Webhook URL host {host} does not resolve")
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _ip_is_blocked(ip):
            raise WebhookURLError(
                f"Webhook URL host {host} resolves to a blocked address {addr}"
            )
    return url.strip()


def _coerce_sources(sources: Any) -> list[str]:
    """Validate ``notification_sources`` against the closed set (400 outside)."""
    if sources is None:
        return []
    if not isinstance(sources, (list, tuple)):
        raise HTTPException(status_code=400, detail="notification_sources must be a list")
    out: list[str] = []
    for s in sources:
        if s not in _WEBHOOK_NOTIFICATION_SOURCES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown notification source {s}; the closed set is "
                    f"{', '.join(_WEBHOOK_NOTIFICATION_SOURCES)}"
                ),
            )
        if s not in out:
            out.append(s)
    return out


def _require_webhook_admin(
    session: Session, repo: SQLRepository, sheet: str | None, actor: Actor
) -> None:
    """AUTHZ: the sheet's structural owner OR a platform admin (403 otherwise;
    never a CR — this is an out-of-band admin surface). Missing sheet -> 404."""
    if getattr(actor, "is_admin", False):
        return
    if not sheet:
        raise HTTPException(status_code=403, detail="Only an admin may manage global webhooks")
    if session.get(m.Sheet, sheet) is None:
        raise HTTPException(status_code=404, detail=f"No such sheet {sheet}")
    owner = repo.get_sheet(sheet).structural_owner
    if actor.user != owner:
        raise HTTPException(
            status_code=403,
            detail="Only the sheet's structural owner or an admin may manage its webhooks",
        )


def _endpoint_out(row: m.WebhookEndpoint) -> dict[str, Any]:
    """The WebhookEndpointView shape WITHOUT the secret (never echoed by a
    read/list — write-once)."""
    return {
        "name": row.name,
        "label": row.label,
        "url": row.url,
        "active": bool(row.active),
        "sheet": row.sheet,
        "owner_user": row.owner_user,
        "scope": row.scope,
        "target": row.target,
        "event_types": list(row.event_types or []),
        "notification_sources": _parse_json_list(row.notification_sources),
    }


@app.post("/api/method/arbor.register_webhook")
def register_webhook(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    """Register an endpoint: owner/admin authz, SSRF URL validation, and a
    server-minted write-once ``secret`` returned exactly ONCE here."""
    payload = payload or {}
    repo = _repo(session)
    actor = _actor(request, repo)
    sheet = payload.get("sheet")
    _require_webhook_admin(session, repo, sheet, actor)

    try:
        clean_url = _validate_webhook_url(payload.get("url"))
    except WebhookURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sources = _coerce_sources(payload.get("notification_sources"))
    types = payload.get("event_types") or []
    if not isinstance(types, list):
        raise HTTPException(status_code=400, detail="event_types must be a list")
    secret = _secrets.token_urlsafe(32)

    row = m.WebhookEndpoint(
        url=clean_url,
        label=payload.get("label") or "",
        sheet=sheet,
        owner_user=actor.user,
        scope=payload.get("scope") or "sheet",
        target=payload.get("target") or sheet,
        event_types=[str(t) for t in types],
        notification_sources=json.dumps(sources),
        secret=secret,  # opaque server-side column; write-once here
        active=True,
    )
    session.add(row)
    session.flush()

    out = _endpoint_out(row)
    out["secret"] = secret  # returned ONCE, never again
    return _msg(out)


@app.get("/api/method/arbor.list_webhooks")
def list_webhooks(
    request: Request, sheet: str | None = None, session: Session = Depends(get_db)
):
    repo = _repo(session)
    actor = _actor(request, repo)
    stmt = sa.select(m.WebhookEndpoint)
    if sheet:
        _require_webhook_admin(session, repo, sheet, actor)  # 403/404 for a non-owner
        stmt = stmt.where(m.WebhookEndpoint.sheet == sheet)
    elif not getattr(actor, "is_admin", False):
        # A non-admin with no filter sees only what they registered themselves.
        stmt = stmt.where(m.WebhookEndpoint.owner_user == actor.user)
    rows = session.scalars(stmt).all()
    return _msg([_endpoint_out(r) for r in rows])


@app.post("/api/method/arbor.update_webhook")
def update_webhook(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    """Patch url/label/active/notification_sources/event_types. NEVER rotates
    the secret (write-once; no secret in the patch surface)."""
    payload = payload or {}
    repo = _repo(session)
    actor = _actor(request, repo)
    row = session.get(m.WebhookEndpoint, payload.get("endpoint"))
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"No such webhook endpoint {payload.get('endpoint')}"
        )
    _require_webhook_admin(session, repo, row.sheet, actor)

    if payload.get("url") is not None:
        try:
            row.url = _validate_webhook_url(payload["url"])
        except WebhookURLError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.get("label") is not None:
        row.label = payload["label"]
    if payload.get("active") is not None:
        row.active = bool(payload["active"])
    if payload.get("notification_sources") is not None:
        row.notification_sources = json.dumps(_coerce_sources(payload["notification_sources"]))
    if payload.get("event_types") is not None:
        types = payload["event_types"]
        if not isinstance(types, list):
            raise HTTPException(status_code=400, detail="event_types must be a list")
        row.event_types = [str(t) for t in types]
    session.flush()
    return _msg(_endpoint_out(row))


@app.post("/api/method/arbor.delete_webhook")
def delete_webhook(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    payload = payload or {}
    repo = _repo(session)
    actor = _actor(request, repo)
    row = session.get(m.WebhookEndpoint, payload.get("endpoint"))
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"No such webhook endpoint {payload.get('endpoint')}"
        )
    _require_webhook_admin(session, repo, row.sheet, actor)
    # Drop pending deliveries so the retry runner never re-picks an orphan.
    session.execute(sa.delete(m.WebhookDelivery).where(m.WebhookDelivery.endpoint == row.name))
    session.delete(row)
    session.flush()
    return _msg({"ok": True})


@app.post("/api/method/arbor.test_webhook")
def test_webhook(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    """Fire a signed ``type='webhook.test'`` ping through the REAL delivery
    engine (signing, delivery ledger, retry scheduling). Unique event_id per
    call so the (endpoint, event_id) idempotency key never suppresses a repeat."""
    payload = payload or {}
    repo = _repo(session)
    actor = _actor(request, repo)
    row = session.get(m.WebhookEndpoint, payload.get("endpoint"))
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"No such webhook endpoint {payload.get('endpoint')}"
        )
    _require_webhook_admin(session, repo, row.sheet, actor)

    dispatcher = _webhook_dispatcher(session)
    ep_view = dispatcher._store.get_endpoint(row.name)
    event_id = f"test:{row.name}:{_secrets.token_hex(8)}"
    body = serialize_notification_bytes(
        event_id=event_id,
        source="test",
        sheet=row.sheet,
        type="webhook.test",
        payload={"endpoint": row.name, "ping": True},
        actor=actor.user,
        actor_type="human",
        timestamp=str(_utcnow()),
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
        d = session.get(m.WebhookDelivery, delivery_id)
        status = d.status if d is not None else None
    return _msg({"delivery": delivery_id, "status": status})


# ---- server-side Re-Act agent (ARCHITECTURE §8) --------------------------------------
def _agent_conf() -> dict[str, Any]:
    """The standalone agent config source: ``ARBOR_AGENT_*`` env vars (the
    site-config analog). Unset keys fall back to the documented defaults."""
    nested: dict[str, Any] = {}
    for key in ("provider_class", "model", "api_key", "api_base", "max_steps"):
        val = os.environ.get(f"ARBOR_AGENT_{key.upper()}")
        if val:
            nested[key] = val
    return {"arbor_agent": nested}


@app.post("/api/method/arbor.agent.chat")
def agent_chat(
    request: Request, payload: dict | None = Body(None), session: Session = Depends(get_db)
):
    """One user turn of the server-side Re-Act agent (``sheet: null`` = the
    workspace agent). The agent acts under the caller's OWN user, stamped
    ``actor_type='agent'`` — same two-axis ACL, mutate-or-suggest, no bypass.
    Returns the whole session as ONE JSON doc (not streamed)."""
    payload = payload or {}
    message = payload.get("message")
    if not message:
        raise HTTPException(status_code=400, detail="`message` is required")
    sheet = payload.get("sheet")

    # Mirror the frappe chat: the agent runs under the caller's REAL session
    # user (never an impersonation overlay), stamped actor_type=agent.
    actor = Actor(user=_session_user(request), actor_type=ActorType.AGENT)
    repo = _repo(session)
    sink = _sink(session, repo)

    def snapshot_fn(sheet_name: str, act: Actor) -> dict[str, Any]:
        # The ONE shared snapshot serializer, so the agent's read matches REST.
        return build_sheet_snapshot(session, repo, sheet_name, act)

    cfg = load_config(_agent_conf())
    provider = get_provider(cfg)

    if sheet:
        system = (
            f"{_DEFAULT_SYSTEM} You are operating on the sheet named '{sheet}'. "
            f"Always pass sheet='{sheet}' as the 'sheet' argument to every tool call, "
            f"and call getSheetSnapshot for '{sheet}' before any mutation."
        )
    else:
        system = _WORKSPACE_SYSTEM

    result = run_agent_session(
        message=message,
        actor=actor,
        repo=repo,
        sink=sink,
        provider=provider,
        snapshot_fn=snapshot_fn,
        system=system,
        max_steps=int(payload.get("max_steps") or cfg.max_steps),
    )
    return _msg(result.as_dict())


# ---- the crawlable external-agent contract ---------------------------------------------
@app.get("/api/method/arbor.skill_md")
def skill_md(request: Request):
    """Public (guest-allowed): the doc describes the API SHAPE + capability
    catalog only, never tenant data. Raw ``text/markdown``."""
    base = str(request.base_url).rstrip("/")
    return PlainTextResponse(render_skill_md(base), media_type="text/markdown")


# ---------------------------------------------------------------------------
# Auth routes (whoami / login_url / the /auth/* login flow) — owned by .auth.
# ---------------------------------------------------------------------------
app.include_router(auth_router)


# ---------------------------------------------------------------------------
# Built frontend: mounted at / (html=True) AFTER the api routes so every
# /api/method/* path wins; anything else serves the SPA.
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DIST = os.environ.get("ARBOR_FRONTEND_DIST") or os.path.join(_REPO_ROOT, "frontend", "dist")
if os.path.isdir(_DIST):
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")
