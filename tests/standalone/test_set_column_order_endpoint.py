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


# --- read-ACL: the 400 must not leak, and the affordance must not be offered --
# The pre-pass that produces this 400 runs BEFORE any authority check, so a
# caller with no relationship to the sheet reaches it. It used to answer with
# the field keys of every missing column — including ones getSheetDefinition
# had just filtered out for that same caller, one request earlier.
ALICE = "alice@example.com"
STRANGER = "stranger@example.com"


def _sheet_with_a_restricted_column(client: TestClient, name: str) -> None:
    """OWNER's sheet with a public column and one ALICE owns and hides. Nobody
    but ALICE (and a platform admin) can read `secret_salary` — not even the
    sheet's structural owner."""
    _sheet_with_columns(client, name, "public_a")
    resp = act(
        client,
        "addColumn",
        {
            "sheet": name,
            "field": "secret_salary",
            "label": "Salary",
            "type": "text",
            "column_owner": ALICE,
        },
    )
    assert resp.status_code == 200, resp.text
    login(client, ALICE)
    resp = act(
        client,
        "updateColumn",
        {"sheet": name, "column": "secret_salary", "patch": {"read_level": "owner-only"}},
    )
    assert resp.status_code == 200, resp.text


def test_the_incomplete_order_400_never_leaks_an_unreadable_field_key(client):
    _sheet_with_a_restricted_column(client, "r1")
    login(client, STRANGER)
    # Baseline: the read surface already hides it correctly.
    assert sheet_fields(client, "r1") == ["title", "public_a"]
    resp = act(client, "setColumnOrder", {"sheet": "r1", "order": []})
    assert resp.status_code == 400, resp.text
    assert "secret_salary" not in resp.text
    assert "public_a" in resp.text


def test_the_structural_owner_gets_the_same_redacted_400(client):
    # The owner is the one actor who could execute the write, and cannot read
    # every column either — same redaction, no special case.
    _sheet_with_a_restricted_column(client, "r2")
    login(client, OWNER)
    assert sheet_fields(client, "r2") == ["title", "public_a"]
    resp = act(client, "setColumnOrder", {"sheet": "r2", "order": ["public_a"]})
    assert resp.status_code == 400, resp.text
    assert "secret_salary" not in resp.text


def test_the_snapshot_tells_a_filtered_viewer_its_column_list_is_incomplete(client):
    """The hint that suppresses "save order for everyone" in the UI. A bare
    boolean: no name, no field key, no count, so it reveals nothing beyond
    "this is not the whole schema"."""
    _sheet_with_a_restricted_column(client, "r3")
    login(client, OWNER)
    snap = client.get("/api/method/arbor.get_sheet_snapshot", params={"sheet": "r3"})
    assert snap.status_code == 200, snap.text
    viewer = snap.json()["message"]["viewer"]
    assert viewer["columns_filtered"] is True
    assert "secret_salary" not in snap.text

    # ALICE reads everything, so for her the affordance stays available.
    login(client, ALICE)
    snap = client.get("/api/method/arbor.get_sheet_snapshot", params={"sheet": "r3"})
    assert snap.json()["message"]["viewer"]["columns_filtered"] is False


def test_an_unfiltered_sheet_reports_columns_filtered_false(client):
    _sheet_with_columns(client, "r4", "status", "due")
    snap = client.get("/api/method/arbor.get_sheet_snapshot", params={"sheet": "r4"})
    assert snap.status_code == 200, snap.text
    assert snap.json()["message"]["viewer"]["columns_filtered"] is False


def test_an_incomplete_order_inside_suggest_changes_is_400_not_a_filed_cr(client):
    # The batch door onto the same capability. A 200 here would file a CR whose
    # approval applies a partial order — the exact semantics the contract refuses.
    _sheet_with_columns(client, "r5", "status", "due", "risk")
    login(client, STRANGER)
    resp = act(
        client,
        "suggestChanges",
        {
            "sheet": "r5",
            "changes": [
                {"action": "setColumnOrder", "params": {"sheet": "r5", "order": ["risk"]}}
            ],
        },
    )
    assert resp.status_code == 400, resp.text
    login(client, OWNER)
    assert sheet_fields(client, "r5") == ["title", "status", "due", "risk"]
