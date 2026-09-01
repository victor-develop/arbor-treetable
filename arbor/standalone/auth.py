"""Session-cookie auth for the standalone adapter (OIDC + dev-login).

The frappe lane delegates auth to ``arbor.auth`` providers riding on Frappe's
LoginManager session. Standalone has no framework session, so this module owns
the whole seam: a signed session cookie (itsdangerous) carrying ``{user}``, two
login providers, and the ``Actor`` resolution the API layer runs every request
through. Semantics mirror the reference implementations exactly:

* find-or-create provisioning mirrors ``arbor.auth.base.BaseAuthProvider
  .ensure_user`` (auto-create on first sight, NO default roles — the two-axis
  ACL is what actually authorizes anything);
* ``whoami`` mirrors ``arbor.auth.api.whoami`` (same envelope keys, same
  best-effort impersonation overlay, never 500s);
* ``get_current_actor`` mirrors ``arbor.arbor.api._actor`` ordering (real user
  first, ``is_admin`` from the users row, overlay force-ended when the real
  user is no longer admin).

Providers (checked in this order):

1. **OIDC** (authlib, Authorization-Code flow) when the env is present::

       ARBOR_OIDC_ISSUER         # https://idp.example.com (discovery-capable)
       ARBOR_OIDC_CLIENT_ID
       ARBOR_OIDC_CLIENT_SECRET
       ARBOR_OIDC_REDIRECT       # optional; default <request base>/auth/callback

2. **Dev-login** when OIDC is absent AND ``ARBOR_DEV_LOGIN=1``: a bare email
   form (no password — internal demo only).

Neither configured → every login route answers 401 with a clear message.

Session cookie signing key: ``ARBOR_SECRET_KEY`` env; when unset a random
per-boot key is generated (sessions won't survive a restart) with a logged
warning. authlib/requests are imported lazily so the module stays importable
in environments that only exercise dev-login or the actor seam.
"""

from __future__ import annotations

import html
import logging
import os
import re
import secrets
from functools import lru_cache
from typing import Any, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session, sessionmaker

from ..core.types import Actor, ActorType
from . import db
from .models import User

logger = logging.getLogger(__name__)

router = APIRouter()

#: The signed session cookie. Payload is just ``{"user": email}`` — everything
#: else (admin flag, impersonation overlay) is re-read from the DB per request
#: so revocations take effect immediately, mirroring the frappe lane.
SESSION_COOKIE = "arbor_session"
SESSION_MAX_AGE = 7 * 24 * 3600  # seconds; matches a typical frappe session TTL

#: Short-lived cookie carrying the OIDC ``state`` (CSRF token) + the post-login
#: redirect across the IdP round-trip — the cookie analog of the frappe
#: provider's ``frappe.cache()`` state stash (arbor/auth/oidc.py).
STATE_COOKIE = "arbor_oidc_state"
STATE_MAX_AGE = 600

#: Light shape check only (the IdP / operator owns real validation); mirrors
#: base.ensure_user's "must have an email" guard with a sanity floor.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# Cookie signing
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _secret_key() -> str:
    """``ARBOR_SECRET_KEY`` env, else a random per-boot key (sessions die on
    restart — fine for a demo, loud in the logs so deploys notice)."""
    key = os.environ.get("ARBOR_SECRET_KEY")
    if key:
        return key
    logger.warning(
        "ARBOR_SECRET_KEY not set; using a random per-boot session key — "
        "all sessions will be invalidated on restart"
    )
    return secrets.token_urlsafe(32)


def _serializer(salt: str = "arbor-session") -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret_key(), salt=salt)


def read_session_user(request: Request) -> Optional[str]:
    """The authenticated user from the signed session cookie, or None (absent,
    tampered, or expired — all treated identically: no session)."""
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        payload = _serializer().loads(raw, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None
    user = payload.get("user") if isinstance(payload, dict) else None
    return user or None


def _set_session_cookie(response: Response, user: str, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        _serializer().dumps({"user": user}),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


# ---------------------------------------------------------------------------
# DB plumbing. auth owns a lazy default engine (idempotent create_all) so it
# works standing alone; the composing app may inject its own factory via
# ``configure`` so auth and API share one engine/pool.
# ---------------------------------------------------------------------------
_session_factory: Optional[sessionmaker[Session]] = None


def configure(session_factory: sessionmaker[Session]) -> None:
    """Inject the app's Session factory (call once at startup)."""
    global _session_factory
    _session_factory = session_factory


def _sessions() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        engine = db.make_engine()
        db.create_all(engine)
        _session_factory = db.make_session_factory(engine)
    return _session_factory


def _repo(session: Session) -> Any:
    """The Repository for the impersonation overlay reads/writes.

    Imported lazily from ``.repository`` (the composed-mixins class, authored
    in a parallel lane) to avoid import cycles; until that module lands, fall
    back to a shim over ``GovernanceRepoMixin`` — the mixin that actually owns
    ``get_active_impersonation`` / ``end_impersonation``, so behavior is
    identical either way.
    """
    try:
        from .repository import Repository  # noqa: PLC0415 - lazy on purpose

        return Repository(session)
    except Exception:  # pragma: no cover - parallel-authorship fallback
        from .repo_governance import GovernanceRepoMixin

        class _GovRepo(GovernanceRepoMixin):
            def __init__(self, s: Session) -> None:
                self.session = s

        return _GovRepo(session)


# ---------------------------------------------------------------------------
# Provisioning + actor resolution (the reference-semantics core of this module)
# ---------------------------------------------------------------------------
def ensure_user(session: Session, email: str, full_name: str = "") -> str:
    """Find-or-create the users row keyed by email; returns the user name.

    Mirrors ``arbor.auth.base.BaseAuthProvider.ensure_user``: lowercase the
    email, auto-create unknown identities, grant NO roles/admin (SSO users
    start with zero Arbor authority until granted). This is the ONLY place
    auth provisions a user, so both providers land users on the same footing.
    """
    email = (email or "").strip().lower()
    if not email or not _EMAIL_RE.match(email):
        raise ValueError("identity has no usable email; cannot resolve a user")
    # Admin bootstrap (the Desk-less deployment's System-Manager seed): emails in
    # ARBOR_ADMIN_EMAILS (comma-separated) are (re)stamped is_admin on login, so a
    # fresh site always has at least one admin who can then grant the rest via the
    # in-app admin surface. Only ever ADDS admin for listed emails — it never
    # demotes an admin appointed in-app.
    bootstrap = {
        e.strip().lower()
        for e in os.environ.get("ARBOR_ADMIN_EMAILS", "").split(",")
        if e.strip()
    }
    row = session.get(User, email)
    if row is not None:
        if email in bootstrap and not row.is_admin:
            row.is_admin = True
            session.flush()
        return row.email
    session.add(
        User(
            email=email,
            full_name=full_name or email,
            is_admin=email in bootstrap,
            enabled=True,
        )
    )
    session.flush()
    return email


def _is_admin_user(session: Session, user: str) -> bool:
    """The System Manager analog: enabled users row with ``is_admin`` set."""
    row = session.get(User, user)
    return bool(row is not None and row.enabled and row.is_admin)


def _actor_for_user(session: Session, real_user: str) -> Actor:
    """Build the EFFECTIVE Actor for the authenticated ``real_user``.

    Ordering mirrors ``arbor.arbor.api._actor`` and is load-bearing:
      1. ``real_is_admin`` is computed from the REAL user's row BEFORE any
         overlay is applied (begin/end authority gates on it);
      2. look up the active impersonation session for the real user;
      3. if present AND real_is_admin → Actor(user=impersonated, is_admin
         recomputed from the IMPERSONATED user, real_user/impersonated_as
         carrying the trace);
      4. if present but the real user is NO LONGER admin → force-end the
         overlay (fail-safe: you cannot keep a foreign identity by losing
         admin) and act as the real user.

    Caller owns the commit (a force-end mutates the overlay row).
    """
    real_is_admin = _is_admin_user(session, real_user)
    repo = _repo(session)
    overlay = repo.get_active_impersonation(real_user)
    if overlay:
        impersonated = overlay["impersonated_user"]
        if real_is_admin and impersonated and impersonated != real_user:
            return Actor(
                user=impersonated,
                actor_type=ActorType.HUMAN,
                is_admin=_is_admin_user(session, impersonated),
                real_user=real_user,
                impersonated_as=impersonated,
            )
        if not real_is_admin:
            repo.end_impersonation(real_user)
    return Actor(user=real_user, actor_type=ActorType.HUMAN, is_admin=real_is_admin)


def get_current_actor(request: Request) -> Actor:
    """The acting identity for this request — session cookie + impersonation
    overlay + ``is_admin`` from the users row. Raises HTTP 401 when there is
    no (valid) session. This is the standalone twin of the frappe ``_actor()``
    and the ONE function the API layer should call per request."""
    user = read_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    with _sessions()() as session:
        actor = _actor_for_user(session, user)
        session.commit()  # persist a force-ended overlay (no-op otherwise)
    return actor


# ---------------------------------------------------------------------------
# OIDC provider (authlib) — configured entirely from the environment.
# ---------------------------------------------------------------------------
def _oidc_config() -> Optional[dict[str, str]]:
    """The OIDC env triple, or None when OIDC is not configured."""
    issuer = os.environ.get("ARBOR_OIDC_ISSUER")
    client_id = os.environ.get("ARBOR_OIDC_CLIENT_ID")
    if not issuer or not client_id:
        return None
    return {
        "issuer": issuer.rstrip("/"),
        "client_id": client_id,
        "client_secret": os.environ.get("ARBOR_OIDC_CLIENT_SECRET", ""),
        "redirect": os.environ.get("ARBOR_OIDC_REDIRECT", ""),
    }


def _dev_login_enabled() -> bool:
    """Dev-login is a FALLBACK: only when OIDC is absent (never alongside it)
    and the operator opted in explicitly."""
    return _oidc_config() is None and os.environ.get("ARBOR_DEV_LOGIN") == "1"


@lru_cache(maxsize=4)
def _oidc_metadata(issuer: str) -> dict[str, Any]:
    """The issuer's discovery document (cached per boot)."""
    import requests  # lazy: deploy-time dep (pulled in alongside authlib)

    url = f"{issuer}/.well-known/openid-configuration"
    doc = requests.get(url, timeout=10).json()
    if not doc.get("authorization_endpoint") or not doc.get("token_endpoint"):
        raise RuntimeError(f"OIDC discovery at {url} returned no usable endpoints")
    return doc


def _redirect_uri(cfg: dict[str, str], request: Request) -> str:
    """``ARBOR_OIDC_REDIRECT`` when set, else derived from the request origin."""
    return cfg["redirect"] or str(request.base_url).rstrip("/") + "/auth/callback"


def _oidc_claims(cfg: dict[str, str], meta: dict[str, Any], token: dict[str, Any]) -> dict[str, Any]:
    """Identity claims for the freshly exchanged token.

    Prefers the userinfo endpoint (server-to-server over the access token, no
    signature handling); falls back to verifying the id_token against the
    issuer's JWKS — same claim set either way (email/name per OIDC core).
    """
    import requests  # lazy

    userinfo = meta.get("userinfo_endpoint")
    if userinfo:
        resp = requests.get(
            userinfo,
            headers={"Authorization": f"Bearer {token['access_token']}"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    id_token = token.get("id_token")
    jwks_uri = meta.get("jwks_uri")
    if not id_token or not jwks_uri:
        raise RuntimeError("OIDC token response has no id_token and issuer exposes no userinfo")
    from authlib.jose import jwt as jose_jwt  # lazy

    claims = jose_jwt.decode(id_token, requests.get(jwks_uri, timeout=10).json())
    claims.validate()
    return dict(claims)


def _safe_redirect(path: Optional[str]) -> str:
    """Clamp the post-login redirect to a local path (no open redirects)."""
    if path and path.startswith("/") and not path.startswith("//"):
        return path
    return "/app"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/api/method/arbor.auth.login_url")
def login_url(redirect: str = "/app") -> dict[str, Any]:
    """Where the frontend should send an unauthenticated browser. Always OUR
    ``/auth/login`` (which then bounces to the IdP or renders the dev form),
    so the frontend stays provider-agnostic — same contract as the frappe
    ``arbor.auth.login_url`` (message-wrapped, per the frappe envelope)."""
    target = f"/auth/login?{urlencode({'redirect': _safe_redirect(redirect)})}"
    return {"message": {"login_url": target}}


@router.get("/auth/login")
def auth_login(request: Request, redirect: str = "/app"):
    """Start a login: redirect to the IdP (OIDC) or render the dev form."""
    redirect = _safe_redirect(redirect)
    cfg = _oidc_config()
    if cfg:
        from authlib.integrations.requests_client import OAuth2Session  # lazy

        meta = _oidc_metadata(cfg["issuer"])
        client = OAuth2Session(
            cfg["client_id"],
            cfg["client_secret"],
            scope="openid email profile",
            redirect_uri=_redirect_uri(cfg, request),
        )
        uri, state = client.create_authorization_url(meta["authorization_endpoint"])
        response = RedirectResponse(uri, status_code=302)
        # State rides a signed short-lived cookie (the stateless analog of the
        # frappe provider's cache stash); verified + consumed in the callback.
        response.set_cookie(
            STATE_COOKIE,
            _serializer("arbor-oidc-state").dumps({"state": state, "redirect": redirect}),
            max_age=STATE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            path="/auth",
        )
        return response

    if _dev_login_enabled():
        # Internal demo only: an email is a login. No password on purpose —
        # gate deployment of this mode behind your network perimeter.
        return HTMLResponse(
            "<!doctype html><title>Arbor dev login</title>"
            "<h1>Arbor dev login</h1>"
            "<p>Internal demo mode — enter an email, no password.</p>"
            '<form method="post" action="/auth/login">'
            '<input type="email" name="email" placeholder="you@example.com" required autofocus> '
            f'<input type="hidden" name="redirect" value="{html.escape(redirect, quote=True)}">'
            "<button type=submit>Sign in</button></form>"
        )

    return JSONResponse(
        status_code=401,
        content={
            "message": "no auth provider configured: set ARBOR_OIDC_* for OIDC "
            "or ARBOR_DEV_LOGIN=1 for the dev email form"
        },
    )


@router.post("/auth/login")
async def auth_login_submit(request: Request):
    """Dev-login form target: find-or-create the users row, set the session."""
    if not _dev_login_enabled():
        return JSONResponse(
            status_code=401,
            content={"message": "dev login is not enabled (and OIDC handles its own callback)"},
        )
    # The dev form posts application/x-www-form-urlencoded; parse it with the
    # stdlib (starlette's request.form() would drag in python-multipart).
    from urllib.parse import parse_qs

    form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    email = str(form.get("email") or "")
    redirect = _safe_redirect(str(form.get("redirect") or ""))
    with _sessions()() as session:
        try:
            user = ensure_user(session, email)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"message": str(exc)})
        session.commit()
    response = RedirectResponse(redirect, status_code=303)
    _set_session_cookie(response, user, secure=request.url.scheme == "https")
    return response


@router.get("/auth/callback")
def auth_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None):
    """Complete the OIDC round-trip: verify state, exchange the code, map the
    identity to a users row (ensure_user), set the session cookie."""
    cfg = _oidc_config()
    if not cfg:
        return JSONResponse(status_code=401, content={"message": "OIDC is not configured"})

    raw_state = request.cookies.get(STATE_COOKIE)
    try:
        stash = _serializer("arbor-oidc-state").loads(raw_state or "", max_age=STATE_MAX_AGE)
    except BadSignature:
        stash = None
    if not code or not stash or not state or stash.get("state") != state:
        return JSONResponse(status_code=401, content={"message": "invalid or missing OIDC state/code"})

    from authlib.integrations.requests_client import OAuth2Session  # lazy

    meta = _oidc_metadata(cfg["issuer"])
    client = OAuth2Session(
        cfg["client_id"],
        cfg["client_secret"],
        redirect_uri=_redirect_uri(cfg, request),
        state=state,
    )
    try:
        token = client.fetch_token(meta["token_endpoint"], code=code)
        claims = _oidc_claims(cfg, meta, token)
    except Exception:
        logger.exception("OIDC code exchange failed")
        return JSONResponse(status_code=401, content={"message": "OIDC code exchange failed"})

    with _sessions()() as session:
        try:
            user = ensure_user(session, claims.get("email") or "", claims.get("name") or "")
        except ValueError:
            return JSONResponse(status_code=401, content={"message": "IdP returned no usable email claim"})
        session.commit()

    response = RedirectResponse(_safe_redirect(stash.get("redirect")), status_code=303)
    response.delete_cookie(STATE_COOKIE, path="/auth")
    _set_session_cookie(response, user, secure=request.url.scheme == "https")
    return response


@router.get("/auth/logout")
def auth_logout() -> RedirectResponse:
    """Drop the session cookie. The impersonation overlay row (if any) stays,
    exactly like the frappe lane — it re-applies on the admin's next login and
    force-ends the moment they lose admin."""
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/api/method/arbor.auth.whoami")
def whoami(request: Request) -> dict[str, Any]:
    """The resolved identity (or Guest) — same envelope as the frappe
    ``arbor.auth.whoami``: ``{user (EFFECTIVE identity), real_user,
    impersonating, authenticated, redirect_to}``, message-wrapped.

    Powers BOTH the frontend auth gate (``authenticated=False`` + a
    ``redirect_to`` login hint for a Guest) and the impersonation banner. The
    overlay lookup is best-effort: any failure falls back to the authenticated
    user (never leaks a foreign identity, never 500s)."""
    session_user = read_session_user(request)
    authenticated = bool(session_user)

    user = session_user
    real_user = session_user
    impersonating = False
    if authenticated:
        try:
            with _sessions()() as session:
                actor = _actor_for_user(session, session_user)
                session.commit()
            user = actor.user
            impersonating = actor.is_impersonated
            real_user = actor.real_user if impersonating else actor.user
        except Exception:  # pragma: no cover - defensive; whoami must never 500
            pass

    return {
        "message": {
            "user": user,
            "real_user": real_user,
            "impersonating": impersonating,
            "authenticated": authenticated,
            "redirect_to": None if authenticated else "/auth/login",
        }
    }
