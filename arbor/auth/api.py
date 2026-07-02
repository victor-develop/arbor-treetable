"""Whitelisted HTTP entrypoints for the auth seam.

Thin Frappe-facing wrappers that delegate to the configured
:class:`~arbor.auth.provider.AuthProvider` (resolved via
:func:`arbor.auth.get_auth_provider`). These are the only auth methods exposed
over HTTP; they contain no provider-specific logic, so swapping the provider
(Local → OIDC → employee SSO) changes behavior without touching this file.

Integrator must add these to ``override_whitelisted_methods`` / the whitelist in
``hooks.py`` (see this lane's returned manifest).
"""

from __future__ import annotations

from typing import Optional

from . import get_auth_provider


def login_url(redirect: str = "/app") -> str:
    """``GET /api/method/arbor.auth.login_url`` → the provider's login URL.

    The frontend (open-source shell) can call this to discover where to send the
    user; the SSO-overlay shell instead wraps the app in ``<AuthProviderEmployee>``
    and never needs it, but the endpoint stays provider-agnostic.
    """
    import frappe

    url = get_auth_provider().get_login_url(redirect)
    frappe.response["login_url"] = url
    return url


def oidc_callback(code: Optional[str] = None, state: Optional[str] = None):
    """``GET /api/method/arbor.auth.oidc_callback`` → complete an IdP round-trip.

    Used by :class:`~arbor.auth.oidc.OIDCAuthProvider` (and any provider with a
    redirect-back flow). Establishes the Frappe session, then redirects the
    browser to the app.
    """
    import frappe

    result = get_auth_provider().handle_callback({"code": code, "state": state})
    if result.success:
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = "/app"
        return result.user
    frappe.local.response["http_status_code"] = 401
    return {"error": result.error or "authentication failed"}


def whoami():
    """``GET /api/method/arbor.auth.whoami`` → the resolved identity (or Guest).

    Provider-agnostic: it reads the authenticated principal from the configured
    AuthProvider, then overlays any active "act as" impersonation (Area 1) so this
    ONE endpoint powers BOTH the frontend auth gate (login screen when Guest) and
    the impersonation banner.

    Returns ``{user (EFFECTIVE identity the ACL runs against), real_user (the
    authenticated admin when impersonating, else same as user), impersonating,
    authenticated, redirect_to}``. It reads the resolved Actor overlay, never any
    provider internals, so password (LocalAuthProvider) and SSO feed it
    identically."""
    result = get_auth_provider().authenticate(request=None)
    authenticated = bool(result.success and result.user and result.user != "Guest")

    user = result.user
    real_user = result.user
    impersonating = False
    if authenticated:
        # Overlay the same impersonation the REST _actor() applies, so the banner
        # and the grid agree. Best-effort: any lookup failure falls back to the
        # authenticated user (never leaks a foreign identity).
        try:
            try:
                from arbor.api import _actor
            except (ModuleNotFoundError, ImportError):  # pragma: no cover
                from arbor.arbor.api import _actor

            actor = _actor()
            user = actor.user
            impersonating = bool(getattr(actor, "is_impersonated", False))
            real_user = actor.real_user if impersonating else actor.user
        except Exception:  # pragma: no cover - defensive; whoami must never 500
            pass

    return {
        "user": user,
        "real_user": real_user,
        "impersonating": impersonating,
        "authenticated": authenticated,
        "redirect_to": result.redirect_to,
    }


def _register_whitelist() -> None:
    """Decorate the auth endpoints with ``frappe.whitelist`` only on a live bench.

    Integrator wiring (auth-sso lane): ``login_url`` / ``oidc_callback`` allow
    guests (they run *before* a session exists); ``whoami`` requires a session.
    Off-bench these stay plain callables so the module imports without frappe,
    keeping the bench-free suite import-clean (mirrors the agent lane pattern).
    ``hooks.override_whitelisted_methods`` maps the documented ``arbor.auth.*``
    paths onto these now-whitelisted callables.
    """
    try:  # pragma: no cover - exercised only on a live bench
        import frappe

        g = globals()
        g["login_url"] = frappe.whitelist(allow_guest=True)(login_url)
        g["oidc_callback"] = frappe.whitelist(allow_guest=True)(oidc_callback)
        g["whoami"] = frappe.whitelist()(whoami)
    except Exception:  # pragma: no cover - no bench in pure tests
        pass


_register_whitelist()
