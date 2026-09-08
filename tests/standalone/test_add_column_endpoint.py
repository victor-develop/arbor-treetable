"""addColumn's ``after`` over the real HTTP lane.

The cross-adapter ORDER semantics live in tests/core/test_add_column_position.py
(re-run against the SQL repository by tests/standalone/sqlcore). What only the
HTTP lane can prove is the STATUS mapping: a bogus anchor is a client error
(400), not a 404 and — the thing that would be silently wrong — not a 200
carrying a Change Request nobody could ever apply.

Same harness as test_admin_endpoints: TestClient over a fresh app bound to a
per-test tmp sqlite DATABASE_URL, dev-login sessions.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

OWNER = "owner@example.com"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'columns.db'}")
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


def act(client: TestClient, action_id: str, params: dict):
    return client.post(
        "/api/method/arbor.execute_action", json={"action_id": action_id, "params": params}
    )


def sheet_fields(client: TestClient, sheet: str) -> list[str]:
    resp = act(client, "getSheetDefinition", {"sheet": sheet})
    assert resp.status_code == 200, resp.text
    return [c["field"] for c in resp.json()["message"]["data"]["columns"]]


def _owned_sheet(client: TestClient, name: str) -> None:
    login(client, OWNER)
    resp = act(client, "createSheet", {"name": name, "title": name})
    assert resp.status_code == 200, resp.text


def test_insert_after_puts_the_column_right_of_the_anchor(client):
    _owned_sheet(client, "s1")
    for f in ("status", "due"):
        assert act(client, "addColumn", {"sheet": "s1", "field": f, "label": f, "type": "text"}).status_code == 200
    resp = act(
        client,
        "addColumn",
        {"sheet": "s1", "field": "notes", "label": "Notes", "type": "text", "after": "status"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["message"]["kind"] == "executed"
    assert sheet_fields(client, "s1") == ["title", "status", "notes", "due"]


def test_unknown_after_is_400_and_writes_nothing(client):
    _owned_sheet(client, "s2")
    resp = act(
        client,
        "addColumn",
        {"sheet": "s2", "field": "notes", "label": "Notes", "type": "text", "after": "ghost"},
    )
    assert resp.status_code == 400, resp.text
    assert sheet_fields(client, "s2") == ["title"]
