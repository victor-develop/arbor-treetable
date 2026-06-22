"""Arbor authentication seam (the ADAPTER side, ARCHITECTURE §10).

Authentication is a **pluggable provider interface**. The open-source build
depends only on the ``AuthProvider`` interface and ships two concrete providers:

- :class:`~arbor.auth.local.LocalAuthProvider` — Frappe email / session (default).
- :class:`~arbor.auth.oidc.OIDCAuthProvider` — generic OAuth2 / OIDC.

The active provider is selected by site config key ``arbor.auth.provider_class``
and resolved at runtime by :func:`~arbor.auth.get_auth_provider`. The employee
SSO integration lives in a SEPARATE app (``arbor_sso_overlay``) that
implements this same interface; **no SSO-overlay SDK import appears in core**, and
that app is omittable for the open-source build.

This package lives in the Frappe app adapter (not in ``arbor.core``) because
authentication is inherently framework-bound: it creates Frappe sessions /
Users. The pure core never authenticates — by the time a capability runs, the
executor already has an :class:`arbor.core.types.Actor`.
"""

from __future__ import annotations

from .provider import AuthProvider, AuthResult, UserIdentity
from .local import LocalAuthProvider
from .oidc import OIDCAuthProvider
from .resolver import DEFAULT_PROVIDER_CLASS, get_auth_provider, resolve_provider_class

__all__ = [
    "AuthProvider",
    "AuthResult",
    "UserIdentity",
    "LocalAuthProvider",
    "OIDCAuthProvider",
    "get_auth_provider",
    "resolve_provider_class",
    "DEFAULT_PROVIDER_CLASS",
]
