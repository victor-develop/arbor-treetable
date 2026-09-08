"""setColumnOrder over the real HTTP lane.

The cross-adapter ORDER semantics live in tests/core/test_set_column_order.py
(re-run against the SQL repository by tests/standalone/sqlcore). What only the
HTTP lane can prove is the STATUS mapping: an incomplete/unknown order is a
client error (400), not a 404 and — the thing that would be silently wrong —
not a 200 carrying a Change Request nobody could ever apply.

Same harness as test_add_column_endpoint: TestClient over a fresh app bound to a
per-test tmp sqlite DATABASE_URL, dev-login sessions.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

OWNER = "owner@example.com"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'order.db'}")
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


def _sheet_with_columns(client: TestClient, name: str, *fields: str) -> None:
    login(client, OWNER)
    assert act(client, "createSheet", {"name": name, "title": name}).status_code == 200
    for f in fields:
        resp = act(client, "addColumn", {"sheet": name, "field": f, "label": f, "type": "text"})
        assert resp.status_code == 200, resp.text


def test_a_complete_order_is_executed_and_read_back(client):
    _sheet_with_columns(client, "s1", "status", "due", "risk")
    resp = act(client, "setColumnOrder", {"sheet": "s1", "order": ["risk", "status", "due"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["message"]["kind"] == "executed"
    assert sheet_fields(client, "s1") == ["title", "risk", "status", "due"]


def test_an_incomplete_order_is_400_and_writes_nothing(client):
    _sheet_with_columns(client, "s2", "status", "due")
    resp = act(client, "setColumnOrder", {"sheet": "s2", "order": ["due"]})
    assert resp.status_code == 400, resp.text
    assert sheet_fields(client, "s2") == ["title", "status", "due"]


def test_an_unknown_column_is_400_and_writes_nothing(client):
    _sheet_with_columns(client, "s3", "status", "due")
    resp = act(client, "setColumnOrder", {"sheet": "s3", "order": ["due", "ghost"]})
    assert resp.status_code == 400, resp.text
    assert sheet_fields(client, "s3") == ["title", "status", "due"]


def test_naming_the_label_column_is_400(client):
    _sheet_with_columns(client, "s4", "status")
    resp = act(client, "setColumnOrder", {"sheet": "s4", "order": ["title", "status"]})
    assert resp.status_code == 400, resp.text
    assert sheet_fields(client, "s4") == ["title", "status"]


def test_a_non_string_entry_is_400_from_the_schema(client):
    _sheet_with_columns(client, "s5", "status")
    resp = act(client, "setColumnOrder", {"sheet": "s5", "order": [7]})
    assert resp.status_code == 400, resp.text
    assert sheet_fields(client, "s5") == ["title", "status"]
