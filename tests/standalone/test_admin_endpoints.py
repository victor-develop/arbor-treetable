"""The platform-admin surface (``arbor.admin.*``) over the real FastAPI app.

Unlike the sqlcore wrappers (pure-core suites re-pointed at SQLTestRepository),
these exercise the HTTP lane end to end: TestClient over ``arbor.standalone.app``
with a per-test tmp sqlite DATABASE_URL (the app module builds its engine at
import time, so the fixture reloads it), dev-login sessions, and the
ARBOR_ADMIN_EMAILS bootstrap seeding the one admin.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

ADMIN = "admin@example.com"
ALICE = "alice@example.com"
BOB = "bob@example.com"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A TestClient over a FRESH app bound to a tmp sqlite file.

    ``arbor.standalone.app`` creates ENGINE at import time from DATABASE_URL,
    so the env must be set BEFORE a reload rebuilds the module (the reload also
    re-runs ``configure_auth`` so the auth lane shares the same tmp engine)."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'admin.db'}")
    monkeypatch.setenv("ARBOR_DEV_LOGIN", "1")
    monkeypatch.setenv("ARBOR_NO_BACKGROUND", "1")
    monkeypatch.setenv("ARBOR_ADMIN_EMAILS", ADMIN)
    monkeypatch.delenv("ARBOR_OIDC_ISSUER", raising=False)
    monkeypatch.delenv("ARBOR_OIDC_CLIENT_ID", raising=False)

    from arbor.standalone import app as app_module

    app_module = importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def login(client: TestClient, email: str) -> None:
    """Dev-login as ``email`` (replaces the client's session cookie)."""
    resp = client.post("/api/method/login", json={"usr": email, "pwd": "ignored"})
    assert resp.status_code == 200, resp.text


def msg(resp):
    return resp.json()["message"]


# ---------------------------------------------------------------------------
# Authz: every admin endpoint is a hard 403 for a non-admin (never a CR).
# ---------------------------------------------------------------------------
def test_non_admin_gets_403_on_every_admin_endpoint(client):
    login(client, ALICE)
    posts = [
        ("arbor.admin.create_role", {"role": "ops", "label": "Ops"}),
        ("arbor.admin.update_role", {"role": "ops", "patch": {"label": "Ops"}}),
        ("arbor.admin.set_user", {"email": ALICE, "is_admin": True}),
    ]
    for method, payload in posts:
        resp = client.post(f"/api/method/{method}", json=payload)
        assert resp.status_code == 403, (method, resp.text)
    assert client.get("/api/method/arbor.admin.list_users").status_code == 403


# ---------------------------------------------------------------------------
# create_role: normalization, key validation, and duplicate -> 409.
# ---------------------------------------------------------------------------
def test_create_role_normalizes_key_and_defaults(client):
    login(client, ADMIN)
    resp = client.post(
        "/api/method/arbor.admin.create_role",
        json={"role": "  Release-Manager_1  ", "label": "Release Manager"},
    )
    assert resp.status_code == 200, resp.text
    body = msg(resp)
    assert body == {
        "role": "release-manager_1",
        "label": "Release Manager",
        "description": None,
        "applicable": True,
        "active": True,
    }
    # And it shows up in the regular read shim.
    roles = msg(client.get("/api/method/arbor.list_roles"))
    assert any(r["role"] == "release-manager_1" for r in roles)


def test_create_role_duplicate_is_409(client):
    login(client, ADMIN)
    payload = {"role": "ops", "label": "Ops"}
    assert client.post("/api/method/arbor.admin.create_role", json=payload).status_code == 200
    resp = client.post("/api/method/arbor.admin.create_role", json=payload)
    assert resp.status_code == 409
    # Normalization makes "  OPS " the SAME key — still 409, not a sibling row.
    resp = client.post(
        "/api/method/arbor.admin.create_role", json={"role": "  OPS ", "label": "Ops 2"}
    )
    assert resp.status_code == 409


@pytest.mark.parametrize("bad", ["", "   ", "has space", "role!", "rôle", "a/b"])
def test_create_role_bad_key_is_400(client, bad):
    login(client, ADMIN)
    resp = client.post(
        "/api/method/arbor.admin.create_role", json={"role": bad, "label": "Bad"}
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# update_role: 404 unknown, patch semantics, active=false as the soft retire.
# ---------------------------------------------------------------------------
def test_update_role_unknown_is_404(client):
    login(client, ADMIN)
    resp = client.post(
        "/api/method/arbor.admin.update_role",
        json={"role": "nope", "patch": {"label": "X"}},
    )
    assert resp.status_code == 404


def test_update_role_patches_and_soft_retires(client):
    login(client, ADMIN)
    client.post(
        "/api/method/arbor.admin.create_role",
        json={"role": "ops", "label": "Ops", "description": "day-2"},
    )
    resp = client.post(
        "/api/method/arbor.admin.update_role",
        json={"role": "ops", "patch": {"label": "Operations", "active": False}},
    )
    assert resp.status_code == 200, resp.text
    body = msg(resp)
    assert body["label"] == "Operations"
    assert body["active"] is False
    assert body["description"] == "day-2"  # untouched keys survive the patch
    # The soft retire is visible on the read shim (row remains, active=False).
    roles = {r["role"]: r for r in msg(client.get("/api/method/arbor.list_roles"))}
    assert roles["ops"]["active"] is False


# ---------------------------------------------------------------------------
# list_users: shape + creation order.
# ---------------------------------------------------------------------------
def test_list_users_shape_and_order(client):
    login(client, ADMIN)  # provisions admin first
    login(client, ALICE)
    login(client, BOB)
    login(client, ADMIN)
    users = msg(client.get("/api/method/arbor.admin.list_users"))
    assert [u["email"] for u in users] == [ADMIN, ALICE, BOB]  # creation asc
    for u in users:
        assert set(u) == {"email", "full_name", "is_admin", "enabled", "creation"}
    assert users[0]["is_admin"] is True  # the ARBOR_ADMIN_EMAILS bootstrap
    assert users[1]["is_admin"] is False and users[1]["enabled"] is True


# ---------------------------------------------------------------------------
# set_user: promote/disable, 404 unknown, and the self-demotion guard.
# ---------------------------------------------------------------------------
def test_set_user_promote_and_disable(client):
    login(client, ALICE)
    login(client, BOB)
    login(client, ADMIN)
    resp = client.post(
        "/api/method/arbor.admin.set_user", json={"email": ALICE, "is_admin": True}
    )
    assert resp.status_code == 200, resp.text
    assert msg(resp) == {"email": ALICE, "is_admin": True, "enabled": True}
    resp = client.post(
        "/api/method/arbor.admin.set_user", json={"email": BOB, "enabled": False}
    )
    assert msg(resp) == {"email": BOB, "is_admin": False, "enabled": False}
    # The freshly promoted admin can now reach the admin surface herself.
    login(client, ALICE)
    assert client.get("/api/method/arbor.admin.list_users").status_code == 200


def test_set_user_unknown_is_404(client):
    login(client, ADMIN)
    resp = client.post(
        "/api/method/arbor.admin.set_user", json={"email": "ghost@example.com", "enabled": False}
    )
    assert resp.status_code == 404


def test_set_user_self_demotion_guard(client):
    login(client, ADMIN)
    for patch in ({"is_admin": False}, {"enabled": False}):
        resp = client.post(
            "/api/method/arbor.admin.set_user", json={"email": ADMIN, **patch}
        )
        assert resp.status_code == 400, resp.text
        assert "cannot remove your own admin/access" in resp.json()["detail"]
    # No-op / affirmative patches on yourself stay allowed.
    resp = client.post(
        "/api/method/arbor.admin.set_user", json={"email": ADMIN, "is_admin": True}
    )
    assert resp.status_code == 200
