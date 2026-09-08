"""Named SAVED VIEWS over the real HTTP lane (Feature: saved views).

What only the HTTP lane can prove is the AUTHORIZATION and the STATUS mapping,
which is the whole risk surface of this feature:

* per-user isolation — B never sees A's PRIVATE view;
* publishing (visibility='sheet') makes it pickable by B, and only then;
* update / publish / delete by a stranger is a hard 403, NOT a silent no-op
  (a silent no-op would leave the stranger's picker showing a stale row);
* the payload is validated — unknown ``v``, a non-conforming facet, an unknown
  key, or an over-budget blob is a 400;
* REVEAL-IMPOSSIBILITY holds through a shared view: a published view whose
  order/hidden names a column the reader cannot read must not surface — nor even
  name — that column;
* saved views are PRESENTATION state: no Tree Event, no Change Request.

Same harness as test_admin_endpoints: TestClient over a fresh app bound to a
per-test tmp sqlite DATABASE_URL, dev-login sessions, ARBOR_ADMIN_EMAILS for the
one admin.
"""

from __future__ import annotations

import importlib

import pytest
from arbor.arbor.saved_view import (
    MAX_PAYLOAD_BYTES,
    MAX_VIEWS_PER_SHEET,
    filter_payload_columns,
)
from fastapi.testclient import TestClient

ADMIN = "admin@example.com"
ALICE = "alice@example.com"
BOB = "bob@example.com"
SHEET = "views-sheet"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'views.db'}")
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
    resp = client.post("/api/method/login", json={"usr": email, "pwd": "ignored"})
    assert resp.status_code == 200, resp.text


def msg(resp):
    return resp.json()["message"]


def act(client: TestClient, action_id: str, params: dict):
    return client.post(
        "/api/method/arbor.execute_action", json={"action_id": action_id, "params": params}
    )


def save(client: TestClient, **payload):
    return client.post("/api/method/arbor.save_sheet_view", json=payload)


def listing(client: TestClient, sheet: str = SHEET):
    return client.get(f"/api/method/arbor.list_sheet_views?sheet={sheet}")


def delete(client: TestClient, name: str):
    return client.post("/api/method/arbor.delete_sheet_view", json={"name": name})


def view(hidden=(), order=(), **extra) -> dict:
    return {"v": 1, "hidden": list(hidden), "order": list(order), **extra}


@pytest.fixture()
def sheet(client):
    """A sheet owned by ALICE with two data columns (``status``, ``secret``)."""
    login(client, ALICE)
    assert act(client, "createSheet", {"name": SHEET, "title": SHEET}).status_code == 200
    for field in ("status", "secret"):
        resp = act(
            client, "addColumn", {"sheet": SHEET, "field": field, "label": field, "type": "text"}
        )
        assert resp.status_code == 200, resp.text
    return SHEET


def column_name(client: TestClient, field: str) -> str:
    resp = act(client, "getSheetDefinition", {"sheet": SHEET})
    cols = resp.json()["message"]["data"]["columns"]
    return next(c["name"] for c in cols if c["field"] == field)


# ---------------------------------------------------------------------------
# Per-user isolation + publishing (the hybrid ownership rule).
# ---------------------------------------------------------------------------
def test_a_save_is_private_and_invisible_to_everyone_else(client, sheet):
    login(client, ALICE)
    created = msg(save(client, sheet=SHEET, label="Mine", view=view(hidden=["c1"])))
    assert created["visibility"] == "private"
    assert created["is_mine"] is True

    assert [v["label"] for v in msg(listing(client))] == ["Mine"]

    login(client, BOB)
    assert msg(listing(client)) == []


def test_publishing_makes_it_pickable_by_everyone_and_unpublishing_hides_it_again(client, sheet):
    login(client, ALICE)
    row = msg(save(client, sheet=SHEET, label="Team view", view=view(order=["c1"])))

    # Publish: the arrangement need not be resent — name + visibility is enough.
    published = msg(save(client, name=row["name"], visibility="sheet"))
    assert published["visibility"] == "sheet"
    assert published["view"] == row["view"]  # the arrangement is untouched by a flip

    login(client, BOB)
    shared = msg(listing(client))
    assert [(v["label"], v["is_mine"], v["author"]) for v in shared] == [
        ("Team view", False, ALICE)
    ]

    login(client, ALICE)
    assert msg(save(client, name=row["name"], visibility="private"))["visibility"] == "private"
    login(client, BOB)
    assert msg(listing(client)) == []


def test_listing_returns_own_private_plus_others_published(client, sheet):
    login(client, ALICE)
    a_private = msg(save(client, sheet=SHEET, label="A private", view=view()))
    a_shared = msg(
        save(client, sheet=SHEET, label="A shared", view=view(), visibility="sheet")
    )
    login(client, BOB)
    b_private = msg(save(client, sheet=SHEET, label="B private", view=view()))

    rows = {v["name"]: v for v in msg(listing(client))}
    assert set(rows) == {a_shared["name"], b_private["name"]}
    assert a_private["name"] not in rows
    assert rows[b_private["name"]]["is_mine"] is True
    assert rows[a_shared["name"]]["is_mine"] is False


# ---------------------------------------------------------------------------
# Authorization on update / publish / delete: 403, never a silent no-op.
# ---------------------------------------------------------------------------
def test_a_stranger_cannot_update_publish_or_delete_someone_elses_view(client, sheet):
    login(client, ALICE)
    row = msg(save(client, sheet=SHEET, label="Mine", view=view(), visibility="sheet"))

    login(client, BOB)
    assert save(client, name=row["name"], label="Hijacked").status_code == 403
    assert save(client, name=row["name"], visibility="private").status_code == 403
    assert save(client, name=row["name"], view=view(hidden=["c1"])).status_code == 403
    assert delete(client, row["name"]).status_code == 403

    # And nothing changed: the row is still ALICE's, still published, still named.
    login(client, ALICE)
    after = msg(listing(client))[0]
    assert (after["label"], after["visibility"], after["view"]["hidden"]) == ("Mine", "sheet", [])


def test_admin_and_sheet_structural_owner_may_administer_any_view(client, sheet):
    # BOB saves a view on ALICE's sheet; ALICE owns the sheet structurally.
    login(client, BOB)
    row = msg(save(client, sheet=SHEET, label="Bob's", view=view()))

    login(client, ALICE)  # the sheet's structural owner
    assert msg(save(client, name=row["name"], label="Renamed by sheet owner"))["label"] == (
        "Renamed by sheet owner"
    )

    login(client, ADMIN)
    assert delete(client, row["name"]).status_code == 200
    login(client, BOB)
    assert msg(listing(client)) == []


def test_unknown_view_is_404_on_update_and_delete(client, sheet):
    login(client, ALICE)
    assert save(client, name="nope", label="x").status_code == 404
    assert delete(client, "nope").status_code == 404


def test_author_can_delete_and_the_row_is_really_gone(client, sheet):
    login(client, ALICE)
    row = msg(save(client, sheet=SHEET, label="Temp", view=view()))
    assert msg(delete(client, row["name"])) == {"ok": True}
    assert msg(listing(client)) == []


def test_a_view_can_only_be_created_for_a_sheet_that_exists(client, sheet):
    login(client, ALICE)
    assert save(client, sheet="ghost-sheet", label="X", view=view()).status_code == 404
    assert listing(client, "ghost-sheet").status_code == 404


def test_duplicate_label_for_the_same_author_and_sheet_is_409(client, sheet):
    login(client, ALICE)
    assert save(client, sheet=SHEET, label="Same", view=view()).status_code == 200
    assert save(client, sheet=SHEET, label="Same", view=view()).status_code == 409
    # …but a DIFFERENT author may reuse the name (the picker scopes per user).
    login(client, BOB)
    assert save(client, sheet=SHEET, label="Same", view=view()).status_code == 200


def test_unauthenticated_calls_are_401(client, sheet):
    client.cookies.clear()
    assert save(client, sheet=SHEET, label="X", view=view()).status_code == 401
    assert listing(client).status_code == 401
    assert delete(client, "whatever").status_code == 401


# ---------------------------------------------------------------------------
# Payload validation (400s).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad,why",
    [
        ({"v": 2, "hidden": [], "order": []}, "unknown version"),
        ({"hidden": [], "order": []}, "no version"),
        ({"v": 1, "hidden": "all", "order": []}, "hidden is not a list"),
        ({"v": 1, "hidden": [1], "order": []}, "hidden holds a non-string"),
        ({"v": 1, "hidden": [], "order": {}}, "order is not a list"),
        ({"v": 1, "hidden": [], "order": [], "width": {"c1": "wide"}}, "width is not numeric"),
        ({"v": 1, "hidden": [], "order": [], "width": {"c1": True}}, "a boolean width"),
        ({"v": 1, "hidden": [], "order": [], "collapsed": [None]}, "collapsed holds a non-string"),
        ({"v": 1, "hidden": [], "order": [], "sort": ["c1"]}, "an unknown facet"),
        ("not-json", "not an object"),
        ([1, 2, 3], "a list, not an object"),
    ],
)
def test_a_non_conforming_payload_is_400(client, sheet, bad, why):
    login(client, ALICE)
    assert save(client, sheet=SHEET, label="X", view=bad).status_code == 400, why
    assert msg(listing(client)) == []


def test_an_oversize_payload_is_400(client, sheet):
    login(client, ALICE)
    fat = view(hidden=[f"col-{i:04d}" for i in range(MAX_PAYLOAD_BYTES // 8)])
    assert save(client, sheet=SHEET, label="Fat", view=fat).status_code == 400
    # The cap is on the STORED payload, so a view just under it still saves.
    assert save(client, sheet=SHEET, label="Slim", view=view(hidden=["c1"])).status_code == 200


def test_a_label_must_be_present_and_bounded(client, sheet):
    login(client, ALICE)
    assert save(client, sheet=SHEET, label="   ", view=view()).status_code == 400
    assert save(client, sheet=SHEET, view=view()).status_code == 400
    assert save(client, sheet=SHEET, label="x" * 200, view=view()).status_code == 400


def test_one_author_cannot_hoard_unbounded_views_on_a_sheet(client, sheet):
    """The per-(author, sheet) cap is the only bound on an otherwise unbounded
    per-user write; an UPDATE of an existing row is never blocked by it."""
    login(client, ALICE)
    for i in range(MAX_VIEWS_PER_SHEET):
        assert save(client, sheet=SHEET, label=f"v{i}", view=view()).status_code == 200
    assert save(client, sheet=SHEET, label="one too many", view=view()).status_code == 400

    first = msg(listing(client))[0]
    assert save(client, name=first["name"], view=view(hidden=["c1"])).status_code == 200
    # …and the cap is PER AUTHOR: another user still has their own budget.
    login(client, BOB)
    assert save(client, sheet=SHEET, label="mine", view=view()).status_code == 200


def test_visibility_outside_the_two_values_is_400(client, sheet):
    login(client, ALICE)
    assert save(
        client, sheet=SHEET, label="X", view=view(), visibility="world"
    ).status_code == 400


def test_a_valid_payload_round_trips_every_facet(client, sheet):
    login(client, ALICE)
    c_status, c_secret = column_name(client, "status"), column_name(client, "secret")
    full = view(
        hidden=[c_secret],
        order=[c_status, c_secret],
        width={c_status: 220},
        collapsed=["node-1"],
    )
    saved = msg(save(client, sheet=SHEET, label="Full", view=full))
    assert saved["view"] == full
    assert msg(listing(client))[0]["view"] == full


# ---------------------------------------------------------------------------
# REVEAL-IMPOSSIBILITY through a shared view.
# ---------------------------------------------------------------------------
def test_a_published_view_never_surfaces_or_names_a_column_the_reader_cannot_read(client, sheet):
    login(client, ALICE)
    c_status, c_secret = column_name(client, "status"), column_name(client, "secret")
    # ALICE owns `secret` and locks it to owner-only, then publishes a view whose
    # order/hidden/width all name it.
    assert act(
        client,
        "updateColumn",
        {"sheet": SHEET, "column": c_secret, "patch": {"read_level": "owner-only", "readers": []}},
    ).status_code == 200
    published = msg(
        save(
            client,
            sheet=SHEET,
            label="Everything",
            view=view(
                hidden=[c_status], order=[c_secret, c_status], width={c_secret: 400}
            ),
            visibility="sheet",
        )
    )
    # The author still sees their own arrangement in full.
    assert published["view"]["order"] == [c_secret, c_status]

    login(client, BOB)
    shared = msg(listing(client))[0]["view"]
    # The unreadable column's NAME is redacted from every column-referencing
    # facet — B cannot even learn that `secret` exists from a shared view.
    assert c_secret not in shared["order"]
    assert c_secret not in shared["hidden"]
    assert c_secret not in shared.get("width", {})
    assert shared["order"] == [c_status]
    # …and the snapshot B resolves the view against does not contain it either,
    # which is what makes surfacing structurally impossible (resolveColumns only
    # ever draws from these columns).
    snap = msg(client.get(f"/api/method/arbor.get_sheet_snapshot?sheet={SHEET}"))
    assert c_secret not in {c["name"] for c in snap["columns"]}


def test_filter_payload_columns_keeps_nodes_and_degrades_a_legacy_row():
    """``collapsed`` holds NODE names — the read-ACL is a column axis, so it is
    passed through untouched. A row whose payload no longer validates degrades to
    the default view rather than raising on a read path."""
    kept = filter_payload_columns(
        {"v": 1, "hidden": ["a", "b"], "order": ["b", "a"], "collapsed": ["n1"]}, {"a"}
    )
    assert kept == {"v": 1, "hidden": ["a"], "order": ["a"], "collapsed": ["n1"]}
    assert filter_payload_columns({"v": 99}, {"a"}) == {"v": 1, "hidden": [], "order": []}


# ---------------------------------------------------------------------------
# Presentation state: no Tree Event, no Change Request.
# ---------------------------------------------------------------------------
def test_saving_publishing_and_deleting_emit_no_event_and_file_no_change_request(client, sheet):
    login(client, BOB)  # a NON-owner of the sheet: a governed write would suggest

    def events() -> int:
        resp = client.get(f"/api/method/arbor.list_activity?sheet={SHEET}&limit=100")
        assert resp.status_code == 200, resp.text
        return len(msg(resp)["events"])

    def crs() -> int:
        resp = client.get(f"/api/method/arbor.list_change_requests?sheet={SHEET}")
        assert resp.status_code == 200, resp.text
        return len(msg(resp))

    before_events, before_crs = events(), crs()
    row = msg(save(client, sheet=SHEET, label="Quiet", view=view(hidden=["c1"])))
    msg(save(client, name=row["name"], visibility="sheet"))
    msg(save(client, name=row["name"], view=view(order=["c1"])))
    msg(delete(client, row["name"]))
    assert (events(), crs()) == (before_events, before_crs)
