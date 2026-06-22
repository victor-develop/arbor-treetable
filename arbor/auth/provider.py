"""The ``AuthProvider`` interface + its value objects (ARCHITECTURE §10).

This is the SEAM. The open-source core depends only on these declarations; the
concrete providers (Local, OIDC) ship here, and the isolated employee SSO overlay
implements the same ``AuthProvider`` Protocol without core ever importing it.

Design notes
------------
* ``AuthProvider`` is a ``typing.Protocol`` — structural, so the overlay app's
  ``EmployeeSSOProvider`` conforms by shape, with **no inheritance and no import
  of this module required** (keeping the SDK strictly out of core's import graph).
* The four methods mirror the ARCHITECTURE §10 contract exactly:
  ``authenticate``, ``get_login_url``, ``handle_callback``, ``map_identity``.
* Auth produces a Frappe **User**; from there the same two-axis ACL applies to
  everyone — human, agent, or external system (PERMISSIONS §3, "EXT"). An
  external system is just a normal Frappe User + API key bound by that ACL
  (RESOLVED OPEN QUESTION 4); it does not need this provider at all (API-key
  auth is Frappe-native), which is why ``authenticate`` may legitimately short
  out to the existing session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class UserIdentity:
    """Normalized external identity, produced by ``map_identity(claims)``.

    Provider-agnostic: ``email`` is the join key to the Frappe User; the rest
    are best-effort profile hints used when auto-provisioning a User row. The
    raw provider claims are retained under ``raw`` for auditing / debugging.
    """

    email: str
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    #: Stable provider-side subject id (OIDC ``sub`` / SSO employee id), if any.
    subject: Optional[str] = None
    #: Provider label, e.g. "local", "oidc", "employee-sso".
    provider: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthResult:
    """Outcome of an authentication attempt.

    On success ``user`` is the resolved/created Frappe User name (its email) and
    ``identity`` carries the normalized claims. On failure ``success`` is False
    and ``error`` explains why. ``redirect_to`` lets a provider request a browser
    redirect (e.g. OIDC needs to bounce to the IdP before a session exists).
    """

    success: bool
    user: Optional[str] = None
    identity: Optional[UserIdentity] = None
    redirect_to: Optional[str] = None
    error: Optional[str] = None

    @classmethod
    def ok(cls, user: str, identity: Optional[UserIdentity] = None) -> "AuthResult":
        return cls(success=True, user=user, identity=identity)

    @classmethod
    def redirect(cls, url: str) -> "AuthResult":
        return cls(success=False, redirect_to=url)

    @classmethod
    def fail(cls, error: str) -> "AuthResult":
        return cls(success=False, error=error)


@runtime_checkable
class AuthProvider(Protocol):
    """Pluggable authentication strategy (ARCHITECTURE §10).

    Implementations turn an inbound HTTP request (or IdP callback) into a Frappe
    User / session. The selected provider is named by site config
    ``arbor.auth.provider_class`` and resolved by :func:`arbor.auth.get_auth_provider`.

    Any object exposing these four methods satisfies the Protocol — the
    employee SSO provider conforms structurally without importing this module.
    """

    def authenticate(self, request: Any) -> AuthResult:
        """Resolve the current request to a User.

        For session-based providers this inspects the live Frappe session; for
        bearer/JWT providers it verifies the token on the request. Returns an
        :class:`AuthResult` (possibly ``redirect`` if a login bounce is needed).
        """
        ...

    def get_login_url(self, redirect: str) -> str:
        """Return the URL the browser should be sent to in order to log in,
        carrying ``redirect`` as the post-login return target."""
        ...

    def handle_callback(self, request: Any) -> AuthResult:
        """Complete an IdP round-trip (the redirect back from ``get_login_url``):
        exchange the code/verify the assertion, ``map_identity`` the claims, and
        establish the Frappe session. Returns the resulting :class:`AuthResult`."""
        ...

    def map_identity(self, claims: dict) -> UserIdentity:
        """Map raw provider claims to a normalized :class:`UserIdentity`
        (external identity → Arbor User join key)."""
        ...
