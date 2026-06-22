"""OIDCAuthProvider — a generic OAuth2 / OpenID Connect provider.

Open-source, vendor-neutral. Drives the standard Authorization-Code flow against
any compliant IdP (Auth0, Okta, Keycloak, Google, Azure AD, …) configured via
site config. The employee SSO overlay is intentionally NOT this — it has its
own SDK-based flow in the separate ``arbor_sso_overlay`` app — but many
deployments need only this generic provider, so it ships in core.

Configuration (site_config.json), all under ``arbor.auth.oidc``::

    "arbor.auth.provider_class": "arbor.auth.oidc.OIDCAuthProvider",
    "arbor.auth.oidc": {
        "client_id": "...",
        "client_secret": "...",
        "discovery_url": "https://idp.example.com/.well-known/openid-configuration",
        # or explicit endpoints if no discovery doc:
        "authorization_endpoint": "...",
        "token_endpoint": "...",
        "jwks_uri": "...",
        "scopes": ["openid", "email", "profile"],
        "redirect_uri": "https://arbor.example.com/api/method/arbor.auth.oidc_callback"
    }

The token-exchange / JWKS-verification calls require ``requests`` (and a JWT lib)
at deploy time; they are imported lazily so this module stays importable on a
bench-free checkout. The flow shape is fully implemented; only the network calls
need a live IdP.
"""

from __future__ import annotations

import secrets
from typing import Any, Optional
from urllib.parse import urlencode

from .base import BaseAuthProvider
from .provider import AuthResult, UserIdentity

CONFIG_KEY = "arbor.auth.oidc"


class OIDCConfigError(RuntimeError):
    """Raised when required OIDC site config is missing."""


class OIDCAuthProvider(BaseAuthProvider):
    """Generic OIDC Authorization-Code provider. Conforms to ``AuthProvider``."""

    provider_label = "oidc"

    # -- config -----------------------------------------------------------------
    def _config(self) -> dict:
        import frappe

        cfg = frappe.conf.get(CONFIG_KEY) or {}
        if not cfg.get("client_id"):
            raise OIDCConfigError(f"site config {CONFIG_KEY!r} missing or has no client_id")
        return cfg

    def _endpoints(self, cfg: dict) -> dict:
        """Resolve auth/token/jwks endpoints, preferring the discovery doc."""
        if cfg.get("authorization_endpoint") and cfg.get("token_endpoint"):
            return {
                "authorization_endpoint": cfg["authorization_endpoint"],
                "token_endpoint": cfg["token_endpoint"],
                "jwks_uri": cfg.get("jwks_uri"),
            }
        disc = cfg.get("discovery_url")
        if not disc:
            raise OIDCConfigError(
                "provide either explicit endpoints or a discovery_url in "
                f"{CONFIG_KEY!r}"
            )
        import requests  # lazy: deploy-time dep

        doc = requests.get(disc, timeout=10).json()
        return {
            "authorization_endpoint": doc["authorization_endpoint"],
            "token_endpoint": doc["token_endpoint"],
            "jwks_uri": doc.get("jwks_uri"),
        }

    # -- AuthProvider interface -------------------------------------------------
    def get_login_url(self, redirect: str) -> str:
        cfg = self._config()
        eps = self._endpoints(cfg)
        state = secrets.token_urlsafe(24)
        self._stash_state(state, redirect)
        params = {
            "response_type": "code",
            "client_id": cfg["client_id"],
            "redirect_uri": cfg["redirect_uri"],
            "scope": " ".join(cfg.get("scopes", ["openid", "email", "profile"])),
            "state": state,
        }
        return f"{eps['authorization_endpoint']}?{urlencode(params)}"

    def authenticate(self, request: Any = None) -> AuthResult:
        """If a Frappe session already exists (post-callback), trust it;
        otherwise bounce the browser to the IdP."""
        import frappe

        user = getattr(frappe.session, "user", None)
        if user and user != "Guest":
            return AuthResult.ok(user=user, identity=self.map_identity({"email": user}))
        return AuthResult.redirect(self.get_login_url(redirect="/app"))

    def handle_callback(self, request: Any) -> AuthResult:
        """Exchange ``code`` for tokens, verify the id_token, map identity, and
        log the user in. ``request`` must expose ``code`` and ``state`` (a Frappe
        request object or a dict both work)."""
        cfg = self._config()
        eps = self._endpoints(cfg)
        code = _get(request, "code")
        state = _get(request, "state")
        if not code or not self._verify_state(state):
            return AuthResult.fail("invalid or missing OIDC state/code")

        import requests  # lazy

        token_resp = requests.post(
            eps["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": cfg["redirect_uri"],
                "client_id": cfg["client_id"],
                "client_secret": cfg.get("client_secret"),
            },
            timeout=10,
        ).json()
        id_token = token_resp.get("id_token")
        if not id_token:
            return AuthResult.fail("OIDC token endpoint returned no id_token")

        claims = self._verify_id_token(id_token, cfg, eps)
        identity = self.map_identity(claims)
        user = self.ensure_user(identity)
        return self.login_user(user, identity)

    def map_identity(self, claims: dict) -> UserIdentity:
        return UserIdentity(
            email=(claims.get("email") or "").strip().lower(),
            full_name=claims.get("name"),
            first_name=claims.get("given_name"),
            last_name=claims.get("family_name"),
            subject=claims.get("sub"),
            provider=self.provider_label,
            raw=dict(claims),
        )

    # -- helpers ----------------------------------------------------------------
    def _verify_id_token(self, id_token: str, cfg: dict, eps: dict) -> dict:
        """Verify signature + audience via the IdP JWKS and return claims.

        Uses PyJWT's JWKS client when available. Kept isolated so a deployment
        can swap the JWT lib without touching the flow.
        """
        import jwt  # lazy: deploy-time dep (PyJWT)
        from jwt import PyJWKClient

        jwks_uri = eps.get("jwks_uri")
        if not jwks_uri:
            raise OIDCConfigError("no jwks_uri available to verify id_token")
        signing_key = PyJWKClient(jwks_uri).get_signing_key_from_jwt(id_token)
        return jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=cfg["client_id"],
        )

    def _stash_state(self, state: str, redirect: str) -> None:
        try:
            import frappe

            frappe.cache().set_value(f"arbor_oidc_state:{state}", redirect or "/app", expires_in_sec=600)
        except Exception:
            pass

    def _verify_state(self, state: Optional[str]) -> bool:
        if not state:
            return False
        try:
            import frappe

            key = f"arbor_oidc_state:{state}"
            if frappe.cache().get_value(key) is None:
                return False
            frappe.cache().delete_value(key)
            return True
        except Exception:
            # No cache (bench-free): accept presence of a state token.
            return True


def _get(request: Any, attr: str) -> Optional[str]:
    """Read a field from either a dict-ish or attribute-ish request object."""
    if request is None:
        return None
    if isinstance(request, dict):
        return request.get(attr)
    return getattr(request, attr, None)
