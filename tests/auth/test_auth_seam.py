"""Bench-free tests for the Arbor AuthProvider seam (ARCHITECTURE §10).

Covers the pure, framework-free surface of the auth lane:
- the ``AuthProvider`` Protocol and its value objects,
- the bundled Local / OIDC providers' identity mapping + login URLs,
- provider selection by site config (``arbor.auth.provider_class``),
- ISOLATION: core contains no SSO-overlay SDK import (the overlay provider's own
  Protocol-conformance tests live in the split-out ``arbor-sso-overlay`` repo).

No Frappe bench required. Frappe-bound paths (User provisioning, session login,
live JWKS verification) are bench-required and asserted only for their seams.
"""

from __future__ import annotations

import pathlib

import pytest

from arbor.auth import (
    DEFAULT_PROVIDER_CLASS,
    AuthProvider,
    AuthResult,
    LocalAuthProvider,
    OIDCAuthProvider,
    UserIdentity,
    get_auth_provider,
    resolve_provider_class,
)


# --- value objects ----------------------------------------------------------
def test_auth_result_helpers():
    ok = AuthResult.ok("a@b.com", UserIdentity(email="a@b.com"))
    assert ok.success and ok.user == "a@b.com" and ok.identity.email == "a@b.com"
    red = AuthResult.redirect("/login?x=1")
    assert not red.success and red.redirect_to == "/login?x=1"
    bad = AuthResult.fail("nope")
    assert not bad.success and bad.error == "nope" and bad.user is None


def test_user_identity_defaults():
    i = UserIdentity(email="x@y.com")
    assert i.raw == {} and i.provider is None and i.subject is None


# --- bundled providers conform to the Protocol ------------------------------
@pytest.mark.parametrize("provider", [LocalAuthProvider(), OIDCAuthProvider()])
def test_bundled_providers_conform(provider):
    assert isinstance(provider, AuthProvider)


def test_local_login_url_carries_redirect():
    url = LocalAuthProvider().get_login_url("/sheet/123")
    assert url.startswith("/login?redirect-to=")
    assert "%2Fsheet%2F123" in url


def test_local_map_identity_no_site():
    # bench-free: with no live site, falls back to the claim email only
    i = LocalAuthProvider().map_identity({"email": "Joe@Example.com"})
    assert i.email == "joe@example.com" and i.provider == "local"


def test_oidc_map_identity_normalizes_claims():
    i = OIDCAuthProvider().map_identity(
        {"email": "A@B.com", "name": "A B", "given_name": "A", "family_name": "B", "sub": "s-1"}
    )
    assert i.email == "a@b.com"
    assert (i.full_name, i.first_name, i.last_name, i.subject) == ("A B", "A", "B", "s-1")
    assert i.provider == "oidc" and i.raw["sub"] == "s-1"


# --- provider selection ------------------------------------------------------
def test_default_provider_is_local():
    # no frappe.conf available bench-free → default + safe fallback
    assert resolve_provider_class() == DEFAULT_PROVIDER_CLASS
    assert isinstance(get_auth_provider(), LocalAuthProvider)


def test_explicit_override_selects_oidc():
    p = get_auth_provider("arbor.auth.oidc.OIDCAuthProvider", cache=False)
    assert isinstance(p, OIDCAuthProvider)


def test_bad_provider_class_degrades_to_local():
    p = get_auth_provider("nonexistent.module.Nope", cache=False)
    assert isinstance(p, LocalAuthProvider)


# --- ISOLATION: core stays clean of any SSO-overlay coupling -----------------
# The EmployeeSSOProvider Protocol-conformance + config-string-selection tests
# live in the SSO overlay's own repo (`arbor-sso-overlay`, which was split out
# of this monorepo). The open-source core keeps only the structural guard below:
# no SSO-overlay SDK import may leak into `arbor/` or `frontend/`.
def test_no_sso_overlay_sdk_import_in_core():
    """ARCHITECTURE §10: no SSO-overlay *SDK import* anywhere in the open-source
    core (``arbor/`` app + ``frontend/``). Prose mentions of the overlay that
    explain the seam are fine; an actual ``import`` / ``from`` / ``require`` of
    the SDK is the forbidden coupling. All real SDK references live only in
    ``arbor_sso_overlay/``."""
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    # Match real import statements that pull in the example SSO-overlay SDK:
    # the overlay package itself, plus any `*-product-auth` / `employee-sso-sdk`
    # JS/TS module.
    patterns = [
        re.compile(r"^\s*from\s+\S*arbor_sso_overlay\S*\s+import", re.I | re.M),
        re.compile(r"^\s*import\s+\S*arbor_sso_overlay", re.I | re.M),
        re.compile(r"""(import|require)\s*\(?\s*['"][^'"]*(product-auth|employee-sso-sdk)""", re.I),
        re.compile(r"""from\s+['"][^'"]*(product-auth|employee-sso-sdk)""", re.I),
    ]
    offenders = []
    for base in ("arbor", "frontend"):
        for path in (root / base).rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx", ".js", ".json"}:
                continue
            if "node_modules" in path.parts or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if any(p.search(text) for p in patterns):
                offenders.append(str(path.relative_to(root)))
    assert offenders == [], f"SSO-overlay SDK import leaked into core: {offenders}"
