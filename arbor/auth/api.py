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
    """``GET /api/method/arbor.auth.whoami`` → the authenticated User (or Guest).

    A trivial parity helper letting every surface confirm the resolved identity
    that the two-axis ACL will run against.
    """
    result = get_auth_provider().authenticate(request=None)
    return {
        "user": result.user,
        "authenticated": bool(result.success and result.user and result.user != "Guest"),
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
