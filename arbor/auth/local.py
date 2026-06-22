"""LocalAuthProvider — Frappe's native email / session auth (the default).

This is the zero-config, open-source default selected when site config does not
set ``arbor.auth.provider_class``. It delegates entirely to Frappe's own session
and login machinery — there is no external IdP, no token to verify, no callback.

* ``authenticate`` trusts the live Frappe session (set by Frappe's own login /
  API-key middleware). An external system (PERMISSIONS §3 "EXT") authenticating
  with a Frappe API key lands here as an already-resolved ``frappe.session.user``.
* ``get_login_url`` points at Frappe's standard ``/login`` page.
* ``handle_callback`` is a no-op (no IdP round-trip exists for local auth).
* ``map_identity`` reads the Frappe User row.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .base import BaseAuthProvider
from .provider import AuthResult, UserIdentity


class LocalAuthProvider(BaseAuthProvider):
    """Frappe email/session provider. Conforms to ``AuthProvider``."""

    provider_label = "local"

    def authenticate(self, request: Any = None) -> AuthResult:
        import frappe

        user = getattr(frappe.session, "user", None)
        if not user or user == "Guest":
            return AuthResult.redirect(self.get_login_url(redirect="/app"))
        return AuthResult.ok(user=user, identity=self.map_identity({"email": user}))

    def get_login_url(self, redirect: str) -> str:
        return f"/login?redirect-to={quote(redirect or '/app', safe='')}"

    def handle_callback(self, request: Any = None) -> AuthResult:
        # Local auth has no external IdP round-trip; treat as a re-authenticate.
        return self.authenticate(request)

    def map_identity(self, claims: dict) -> UserIdentity:
        email = (claims.get("email") or claims.get("user") or "").strip().lower()
        full_name = first_name = last_name = None
        try:
            import frappe

            if email and frappe.db.exists("User", email):
                full_name = frappe.db.get_value("User", email, "full_name")
                first_name = frappe.db.get_value("User", email, "first_name")
                last_name = frappe.db.get_value("User", email, "last_name")
        except Exception:
            # bench-free / no-site context: fall back to the claim email only
            pass
        return UserIdentity(
            email=email,
            full_name=full_name,
            first_name=first_name,
            last_name=last_name,
            subject=email,
            provider=self.provider_label,
            raw=dict(claims),
        )
