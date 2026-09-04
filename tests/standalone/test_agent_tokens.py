"""The external-LLM surface over the real FastAPI app: the public skill.md
contract, Agent Token issuance/listing/revocation, and — the standalone-only
semantics — the token as a FULL credential (identity + scope in one header,
because there is no frappe API key to pair it with).

Same harness as test_admin_endpoints: TestClient over a fresh app bound to a
per-test tmp sqlite DATABASE_URL, dev-login sessions.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

ALICE = "alice@example.com"
BOB = "bob@example.com"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'tokens.db'}")
    monkeypatch.setenv("ARBOR_DEV_LOGIN", "1")
    monkeypatch.setenv("ARBOR_NO_BACKGROUND", "1")
    monkeypatch.delenv("ARBOR_OIDC_ISSUER", raising=False)
    monkeypatch.delenv("ARBOR_OIDC_CLIENT_ID", raising=False)

    from arbor.standalone import app as app_module

    app_module = importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def login(client: TestClient, email: str) -> None:
    resp = client.post("/api/method/login", json={"usr": email, "pwd": "ignored"})
    assert resp.status_code == 200, resp.text


def msg(resp):
    return resp.json()["message"]


def issue(client: TestClient, **payload) -> dict:
    resp = client.post("/api/method/arbor.issue_agent_token", json=payload)
    assert resp.status_code == 200, resp.text
    return msg(resp)


def bare(client: TestClient, token: str) -> TestClient:
    """A cookie-less view of the same app: only the token header speaks."""
    fresh = TestClient(client.app)
    fresh.headers["X-Arbor-Agent-Token"] = token
    return fresh


def make_sheet(client: TestClient, sheet: str) -> None:
    resp = client.post(
        "/api/method/arbor.execute_action",
        json={"action_id": "createSheet", "params": {"name": sheet, "title": sheet}},
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# The public contract.
# ---------------------------------------------------------------------------
def test_skill_md_is_public_markdown_on_both_paths(client):
    for path in ("/api/method/arbor.skill_md", "/llm/skill.md"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["content-type"].startswith("text/markdown")
        assert "X-Arbor-Agent-Token" in resp.text
        # The standalone auth story — ONE header, no frappe API key.
        assert "Frappe API key" not in resp.text


# ---------------------------------------------------------------------------
# Issuance / listing / revocation (session lane).
# ---------------------------------------------------------------------------
def test_issue_returns_plaintext_once_with_bootstrap_prompt(client):
    login(client, ALICE)
    out = issue(client, label="test bot", mode="read", sheets=["s1"], ttl_days=7)
    assert out["token"].startswith("arbor_pat_")
    assert out["mode"] == "read"
    assert out["sheets"] == ["s1"]
    assert out["token"] in out["bootstrap_prompt"]
    assert "/llm/skill.md" in out["bootstrap_prompt"]
    # Listing shows metadata only — never the secret.
    rows = msg(client.get("/api/method/arbor.list_agent_tokens"))
    assert [r["token_id"] for r in rows] == [out["token_id"]]
    assert "token" not in rows[0] and "token_hash" not in rows[0]


def test_issue_requires_auth_and_validates_mode(client):
    assert client.post("/api/method/arbor.issue_agent_token", json={}).status_code == 401
    login(client, ALICE)
    resp = client.post("/api/method/arbor.issue_agent_token", json={"mode": "admin"})
    assert resp.status_code == 400


def test_revoke_is_owner_or_admin_only(client):
    login(client, ALICE)
    out = issue(client)
    login(client, BOB)  # replaces the session cookie
    resp = client.post("/api/method/arbor.revoke_agent_token", json={"token_id": out["token_id"]})
    assert resp.status_code == 403
    login(client, ALICE)
    assert msg(
        client.post("/api/method/arbor.revoke_agent_token", json={"token_id": out["token_id"]})
    ) == {"token_id": out["token_id"], "revoked": True}


# ---------------------------------------------------------------------------
# The token as a FULL credential (identity + scope, no cookie).
# ---------------------------------------------------------------------------
def test_bare_token_authenticates_as_issuer_and_writes(client):
    login(client, ALICE)
    make_sheet(client, "s1")
    out = issue(client, mode="write")
    agent = bare(client, out["token"])
    resp = agent.post(
        "/api/method/arbor.execute_action",
        json={
            "action_id": "addNode",
            "params": {"sheet": "s1", "parent": None, "label": "via token"},
        },
    )
    assert resp.status_code == 200, resp.text
    # ALICE owns s1, so her token executes directly (not a CR).
    assert msg(resp)["kind"] == "executed"


def test_read_mode_token_cannot_write_but_can_read(client):
    login(client, ALICE)
    make_sheet(client, "s1")
    out = issue(client, mode="read")
    agent = bare(client, out["token"])
    assert (
        agent.get("/api/method/arbor.get_sheet_snapshot", params={"sheet": "s1"}).status_code
        == 200
    )
    resp = agent.post(
        "/api/method/arbor.execute_action",
        json={"action_id": "addNode", "params": {"sheet": "s1", "parent": None, "label": "x"}},
    )
    assert resp.status_code == 403  # scope violation is hard — never a CR


def test_sheet_scoped_token_is_fenced(client):
    login(client, ALICE)
    make_sheet(client, "s1")
    make_sheet(client, "s2")
    out = issue(client, mode="write", sheets=["s1"])
    agent = bare(client, out["token"])
    ok = agent.post(
        "/api/method/arbor.execute_action",
        json={"action_id": "addNode", "params": {"sheet": "s1", "parent": None, "label": "in"}},
    )
    assert ok.status_code == 200
    fenced = agent.post(
        "/api/method/arbor.execute_action",
        json={"action_id": "addNode", "params": {"sheet": "s2", "parent": None, "label": "out"}},
    )
    assert fenced.status_code == 403


def test_invalid_revoked_tokens_are_401_and_tokens_cannot_mint_tokens(client):
    login(client, ALICE)
    make_sheet(client, "s1")
    out = issue(client)
    agent = bare(client, out["token"])
    # A token must not mint tokens (no self-propagating credentials).
    assert agent.post("/api/method/arbor.issue_agent_token", json={}).status_code == 403
    # Garbage token: hard 401, not an anonymous fallthrough.
    assert (
        bare(client, "arbor_pat_garbage")
        .get("/api/method/arbor.get_sheet_snapshot", params={"sheet": "s1"})
        .status_code
        == 401
    )
    # Revocation kills the credential immediately.
    client.post("/api/method/arbor.revoke_agent_token", json={"token_id": out["token_id"]})
    assert (
        agent.get("/api/method/arbor.get_sheet_snapshot", params={"sheet": "s1"}).status_code
        == 401
    )
