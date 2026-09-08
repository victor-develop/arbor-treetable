"""Realtime signals: the hub's fan-out rules and the SSE endpoint's gate.

The hub is exercised directly (asyncio.run — this repo has no pytest-asyncio),
because the interesting behavior is WHO a signal reaches, not the wire format.
The endpoint tests cover the parts a browser depends on: the auth/existence
gate and the anti-buffering headers, which is exactly what silently breaks
behind a proxy.
"""

from __future__ import annotations

import asyncio
import importlib

import sqlalchemy as sa
from starlette.concurrency import run_in_threadpool

import pytest
from fastapi.testclient import TestClient

from arbor.core.types import Actor, ActorType
from arbor.standalone.realtime import (
    QUEUE_MAXSIZE,
    SignalHub,
    format_comment,
    format_event,
    stream_frames,
)

ALICE = "alice@example.com"
BOB = "bob@example.com"


def actor(user: str) -> Actor:
    return Actor(user=user, actor_type=ActorType.HUMAN, is_admin=False)


# ---------------------------------------------------------------------------
# The hub.
# ---------------------------------------------------------------------------
def test_publish_reaches_only_that_sheets_subscribers():
    async def scenario():
        hub = SignalHub()
        hub.bind_loop(asyncio.get_running_loop())
        mine = hub.subscribe("s1", actor(ALICE))
        other = hub.subscribe("s2", actor(ALICE))
        assert hub.publish("s1", "comments") == 1
        await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run
        assert mine.queue.get_nowait() == {"kind": "comments", "sheet": "s1"}
        assert other.queue.empty()

    asyncio.run(scenario())


def test_can_deliver_filters_per_subscriber():
    """The read gate: a change on a column BOB cannot read never reaches him."""

    async def scenario():
        hub = SignalHub()
        hub.bind_loop(asyncio.get_running_loop())
        a = hub.subscribe("s1", actor(ALICE))
        b = hub.subscribe("s1", actor(BOB))
        delivered = hub.publish("s1", "comments", can_deliver=lambda act: act.user == ALICE)
        await asyncio.sleep(0)
        assert delivered == 1
        assert a.queue.get_nowait()["kind"] == "comments"
        assert b.queue.empty()

    asyncio.run(scenario())


def test_unsubscribe_stops_delivery_and_frees_the_slot():
    async def scenario():
        hub = SignalHub()
        hub.bind_loop(asyncio.get_running_loop())
        sub = hub.subscribe("s1", actor(ALICE))
        assert hub.subscriber_count == 1
        hub.unsubscribe(sub.sid)
        assert hub.subscriber_count == 0
        assert hub.publish("s1", "comments") == 0
        await asyncio.sleep(0)
        assert sub.queue.empty()

    asyncio.run(scenario())


def test_a_backed_up_subscriber_coalesces_instead_of_blocking():
    """A signal is a dirty marker, so dropping duplicates is correct — what must
    never happen is a slow client blocking the publisher (a comment write)."""

    async def scenario():
        hub = SignalHub()
        hub.bind_loop(asyncio.get_running_loop())
        sub = hub.subscribe("s1", actor(ALICE))
        for _ in range(QUEUE_MAXSIZE + 5):
            assert hub.publish("s1", "comments") == 1
        await asyncio.sleep(0)
        assert sub.queue.qsize() == QUEUE_MAXSIZE  # capped, never unbounded

    asyncio.run(scenario())


def test_publish_without_a_bound_loop_is_a_noop_not_an_error():
    """A publish during startup/shutdown must not turn a write into a 500."""
    hub = SignalHub()

    async def scenario():
        hub.subscribe("s1", actor(ALICE))

    asyncio.run(scenario())
    assert hub.publish("s1", "comments") == 0


def test_wire_format():
    assert format_event("comments") == "event: comments\ndata: 1\n\n"
    assert format_comment("ping") == ": ping\n\n"


# ---------------------------------------------------------------------------
# The endpoint.
# ---------------------------------------------------------------------------
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'realtime.db'}")
    monkeypatch.setenv("ARBOR_DEV_LOGIN", "1")
    monkeypatch.setenv("ARBOR_NO_BACKGROUND", "1")
    monkeypatch.delenv("ARBOR_OIDC_ISSUER", raising=False)
    monkeypatch.delenv("ARBOR_OIDC_CLIENT_ID", raising=False)

    from arbor.standalone import app as app_module

    app_module = importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def login(client: TestClient, email: str) -> None:
    assert client.post("/api/method/login", json={"usr": email, "pwd": "ignored"}).status_code == 200


def msg(resp):
    assert resp.status_code == 200, resp.text
    return resp.json()["message"]


def make_sheet(client: TestClient, sheet: str) -> None:
    resp = client.post(
        "/api/method/arbor.execute_action",
        json={"action_id": "createSheet", "params": {"name": sheet, "title": sheet}},
    )
    assert resp.status_code == 200, resp.text


def test_events_requires_a_session(client):
    with client.stream("GET", "/api/method/arbor.events?sheet=s1") as r:
        assert r.status_code == 401


def test_events_404s_an_unknown_sheet(client):
    login(client, ALICE)
    with client.stream("GET", "/api/method/arbor.events?sheet=nope") as r:
        assert r.status_code == 404


def test_stream_frames_flushes_then_emits_then_heartbeats():
    """The frame loop, which no blocking HTTP client can test: it never ends."""

    async def scenario():
        queue: asyncio.Queue = asyncio.Queue()
        frames = stream_frames(queue, heartbeat=0.02)
        # 1. immediate flush, so a buffering proxy cannot leave the client
        #    unable to tell "connected" from "hung".
        assert await frames.__anext__() == ": connected\n\n"
        # 2. a queued signal becomes an event frame.
        queue.put_nowait({"kind": "comments", "sheet": "s1"})
        assert await frames.__anext__() == "event: comments\ndata: 1\n\n"
        # 3. going quiet produces a heartbeat, not a dropped connection.
        assert await asyncio.wait_for(frames.__anext__(), timeout=2) == ": ping\n\n"
        await frames.aclose()

    asyncio.run(scenario())


def test_the_endpoint_subscribes_and_always_unsubscribes(client):
    """A subscription that outlives its stream is a slow leak: the hub would
    keep filtering and enqueueing for a client that is never coming back.

    Driven through the ASGI app directly (no HTTP client): consume the response
    stream far enough to prove the subscription exists, then close it the way a
    disconnect does and assert the generator's finally ran.
    """
    from arbor.standalone.realtime import hub

    login(client, ALICE)
    make_sheet(client, "s1")
    before = hub.subscriber_count

    async def scenario():
        from arbor.standalone import app as app_module

        response = await app_module.sheet_events(
            _FakeRequest(client.cookies.get("arbor_session")), "s1"
        )
        assert response.media_type == "text/event-stream"
        assert "no-cache" in response.headers["cache-control"]
        assert "no-transform" in response.headers["cache-control"]
        assert response.headers["x-accel-buffering"] == "no"

        body = response.body_iterator
        assert (await body.__anext__()) == ": connected\n\n"
        assert hub.subscriber_count == before + 1
        # A client going away closes the generator; the finally must unsubscribe.
        await body.aclose()
        assert hub.subscriber_count == before

    asyncio.run(scenario())


class _FakeRequest:
    """The two things ``sheet_events`` touches: the session cookie (via the auth
    reader) and nothing else. Cheaper and more direct than a live HTTP client
    against an endless stream."""

    def __init__(self, session_cookie):
        self.cookies = {"arbor_session": session_cookie} if session_cookie else {}
        self.headers = {}


# ---------------------------------------------------------------------------
# The publish side: which writes signal, with which kind, and gated by what.
# ---------------------------------------------------------------------------
def _seed_cell(client: TestClient, sheet: str) -> tuple[str, str]:
    """A sheet with one node and one data column; returns (node, column)."""
    make_sheet(client, sheet)
    add_col = client.post(
        "/api/method/arbor.execute_action",
        json={
            "action_id": "addColumn",
            "params": {"sheet": sheet, "field": "notes", "label": "Notes", "type": "text"},
        },
    )
    assert add_col.status_code == 200, add_col.text
    column = add_col.json()["message"]["data"]["column"]
    add_node = client.post(
        "/api/method/arbor.execute_action",
        json={"action_id": "addNode", "params": {"sheet": sheet, "parent": None, "label": "row"}},
    )
    assert add_node.status_code == 200, add_node.text
    return add_node.json()["message"]["data"]["node"], column


@pytest.fixture()
def captured(monkeypatch):
    """Record hub.publish calls made by the app under test."""
    from arbor.standalone import app as app_module

    calls: list[dict] = []

    def fake_publish(sheet, kind, can_deliver=None):
        calls.append({"sheet": sheet, "kind": kind, "can_deliver": can_deliver})
        return 0

    monkeypatch.setattr(app_module.hub, "publish", fake_publish)
    return calls


def kinds(captured) -> list[str]:
    return [c["kind"] for c in captured]


def test_a_comment_write_signals_comments_gated_on_its_column(client, captured):
    login(client, ALICE)
    node, column = _seed_cell(client, "s1")
    captured.clear()  # drop the seed's own signals

    added = client.post(
        "/api/method/arbor.add_cell_comment",
        json={"sheet": "s1", "node": node, "column": column, "body": "first"},
    )
    assert added.status_code == 200, added.text
    comment = added.json()["message"]["name"]
    assert kinds(captured) == ["comments"]
    assert captured[0]["sheet"] == "s1"
    assert captured[0]["can_deliver"] is not None  # column-scoped => gated

    captured.clear()
    assert (
        client.post(
            "/api/method/arbor.resolve_cell_comment",
            json={"comment": comment, "resolved": True},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/method/arbor.delete_cell_comment", json={"comment": comment}
        ).status_code
        == 200
    )
    # Resolve and delete signal too — the badge changes for everyone watching.
    assert kinds(captured) == ["comments", "comments"]


def test_a_cell_write_signals_sheet_gated_on_its_column(client, captured):
    login(client, ALICE)
    node, column = _seed_cell(client, "s1")
    captured.clear()

    resp = client.post(
        "/api/method/arbor.execute_action",
        json={
            "action_id": "updateCell",
            "params": {"sheet": "s1", "node": node, "column": column, "value": "v1"},
        },
    )
    assert resp.status_code == 200, resp.text
    assert kinds(captured) == ["sheet"]
    assert captured[0]["can_deliver"] is not None


def test_a_structural_write_signals_sheet_WITHOUT_a_gate(client, captured):
    """No column in the payload means the change is sheet-wide, so every reader
    of the sheet hears it."""
    login(client, ALICE)
    make_sheet(client, "s1")
    captured.clear()

    resp = client.post(
        "/api/method/arbor.execute_action",
        json={"action_id": "addNode", "params": {"sheet": "s1", "parent": None, "label": "r"}},
    )
    assert resp.status_code == 200, resp.text
    assert kinds(captured) == ["sheet"]
    assert captured[0]["can_deliver"] is None


def test_a_column_REORDER_is_sheet_wide_not_column_gated(client, captured):
    """The split that matters: configuring an owner-only column must not hint at
    its existence, but a reorder changes the layout for everyone."""
    login(client, ALICE)
    make_sheet(client, "s1")
    fields = []
    for field in ("a", "b"):
        resp = client.post(
            "/api/method/arbor.execute_action",
            json={
                "action_id": "addColumn",
                "params": {"sheet": "s1", "field": field, "label": field, "type": "text"},
            },
        )
        assert resp.status_code == 200, resp.text
        fields.append(field)
    captured.clear()

    resp = client.post(
        "/api/method/arbor.execute_action",
        json={"action_id": "setColumnOrder", "params": {"sheet": "s1", "order": ["b", "a"]}},
    )
    assert resp.status_code == 200, resp.text
    assert kinds(captured) == ["sheet"]
    assert captured[0]["can_deliver"] is None, "a reorder must not be gated on one column"


def test_a_change_request_signals_crs(client, captured):
    """A non-owner's write degrades to a CR, and the inbox has to hear about it."""
    login(client, ALICE)
    node, column = _seed_cell(client, "s1")
    login(client, BOB)  # BOB owns nothing here
    captured.clear()

    resp = client.post(
        "/api/method/arbor.execute_action",
        json={
            "action_id": "updateCell",
            "params": {"sheet": "s1", "node": node, "column": column, "value": "from bob"},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["message"]["kind"] == "suggested"
    assert "crs" in kinds(captured)


def test_only_mapped_event_types_enqueue_a_marker():
    """A marker nobody acts on is noise: subscription/delegation changes drive
    panels that do not listen, so they must not reach the wire. Tested on the
    mapping directly — it is the thing that decides, and this keeps the test
    independent of any capability's param shape."""
    from types import SimpleNamespace

    from arbor.standalone import app as app_module
    from arbor.standalone.realtime import SESSION_KEY

    def enqueued(event_type: str, payload: dict | None = None):
        session = SimpleNamespace(info={}, get=lambda *_a, **_k: None)
        ev = SimpleNamespace(type=event_type, sheet="s1", payload=payload or {})
        app_module._enqueue_event_signal(session, ev)
        return session.info.get(SESSION_KEY, [])

    assert enqueued("SUBSCRIPTION_CHANGED") == []
    assert [x.kind for x in enqueued("NODE_VALUE_UPDATED", {"column": "c1"})] == ["sheet"]
    assert [x.column for x in enqueued("NODE_VALUE_UPDATED", {"column": "c1"})] == ["c1"]
    assert [x.column for x in enqueued("NODE_CREATED", {"node": "n1"})] == [None]
    # The REAL proposal shape nests the original call — asserting an empty
    # payload here is what let an ungated CR marker ship once.
    proposed = enqueued("CHANGE_PROPOSED", {"change_request": "cr-1", "params": {"column": "c9"}})
    assert [x.kind for x in proposed] == ["crs"]
    assert [x.column for x in proposed] == ["c9"]
    # Every schema change is sheet-wide, gated on nothing: see
    # _COLUMN_GATED_EVENTS for why gating these was wrong twice over.
    for payload in ({"op": "reorder", "order": []}, {"op": "delete", "column": "c1"}):
        assert [x.column for x in enqueued("COLUMN_CONFIG_UPDATED", payload)] == [None]
    # A delegation moves per-cell can_edit, so the grid must refetch.
    assert [x.kind for x in enqueued("DELEGATION_CHANGED", {})] == ["sheet"]


def test_enqueue_collapses_duplicates_within_one_request():
    """One dispatch can touch the same (sheet, kind, column) repeatedly and one
    marker says all of it. Tested on ``enqueue`` directly: the integration path
    that used to stand in for this emitted a single event either way, so it
    passed with the de-duplication deleted."""
    from arbor.standalone.realtime import enqueue, take

    info: dict = {}
    enqueue(info, "s1", "sheet", "c1")
    enqueue(info, "s1", "sheet", "c1")
    enqueue(info, "s1", "sheet", None)  # different subject => its own marker
    enqueue(info, "s1", "crs", "c1")
    enqueue(info, "", "sheet", None)  # no sheet => nothing to address
    assert [(x.kind, x.column) for x in take(info)] == [
        ("sheet", "c1"),
        ("sheet", None),
        ("crs", "c1"),
    ]
    assert take(info) == []


def test_deleting_a_column_still_signals(client, captured):
    """The gate resolves a column AFTER commit, and a deleted column is gone by
    then — gating this dropped the marker every single time, so every open
    client kept rendering a column that no longer existed."""
    login(client, ALICE)
    _node, column = _seed_cell(client, "s1")
    captured.clear()

    resp = client.post(
        "/api/method/arbor.execute_action",
        json={"action_id": "deleteColumn", "params": {"sheet": "s1", "column": column}},
    )
    assert resp.status_code == 200, resp.text
    assert kinds(captured) == ["sheet"]
    assert captured[0]["can_deliver"] is None, "a delete cannot be gated on the row it removed"


def test_revoking_read_access_signals_the_viewer_who_lost_it(client, captured):
    """Gating this on the NEW acl told everyone EXCEPT the one viewer who most
    needed to refetch — their tab kept showing a column they may no longer see."""
    login(client, ALICE)
    _node, column = _seed_cell(client, "s1")
    captured.clear()

    resp = client.post(
        "/api/method/arbor.execute_action",
        json={
            "action_id": "updateColumn",
            "params": {"sheet": "s1", "column": column, "patch": {"read_level": "owner-only"}},
        },
    )
    assert resp.status_code == 200, resp.text
    assert kinds(captured) == ["sheet"]
    gate = captured[0]["can_deliver"]
    assert gate is None, "the viewer losing access must still be told to refetch"


def test_a_change_request_marker_is_gated_on_the_column_it_targets(client, captured):
    """A CR payload names a column and carries the proposed value, so its marker
    must not be broadcast to viewers who cannot read that column."""
    login(client, ALICE)
    node, column = _seed_cell(client, "s1")
    login(client, BOB)  # BOB owns nothing here, so his write degrades to a CR
    captured.clear()

    resp = client.post(
        "/api/method/arbor.execute_action",
        json={
            "action_id": "updateCell",
            "params": {"sheet": "s1", "node": node, "column": column, "value": "from bob"},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["message"]["kind"] == "suggested"
    crs = [c for c in captured if c["kind"] == "crs"]
    assert crs, f"no crs marker in {kinds(captured)}"
    assert crs[0]["can_deliver"] is not None, "a column-scoped CR marker must be gated"


def test_a_rolled_back_write_announces_nothing(client, captured):
    """`enqueue`'s promise is that a marker follows a COMMITTED write. A stale
    base_version rolls the request back at HTTP 200, so the queue has to be
    cleared with it."""
    login(client, ALICE)
    node, column = _seed_cell(client, "s1")
    captured.clear()

    resp = client.post(
        "/api/method/arbor.execute_action",
        json={
            "action_id": "updateCell",
            "params": {
                "sheet": "s1",
                "node": node,
                "column": column,
                "value": "v",
                "base_version": 99,  # nothing is at version 99 => VERSION_CONFLICT
            },
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["message"].get("error") == "VERSION_CONFLICT", resp.text
    assert captured == []


def test_publishing_from_another_thread_still_reaches_the_loop():
    """Publishers are threadpool workers, so the enqueue crosses threads. Every
    other hub test publishes from the loop's own thread, where a plain
    ``call_soon`` would look identical — this is what pins the requirement."""

    async def scenario():
        hub = SignalHub()
        hub.bind_loop(asyncio.get_running_loop())
        sub = hub.subscribe("s1", actor(ALICE))
        await run_in_threadpool(hub.publish, "s1", "sheet")
        message = await asyncio.wait_for(sub.queue.get(), timeout=2)
        assert message == {"kind": "sheet", "sheet": "s1"}

    asyncio.run(scenario())


def test_the_signal_carries_no_content(client, captured):
    """Regression guard on the whole design: if a payload ever rides along, the
    push path would have to re-implement read filtering."""
    login(client, ALICE)
    node, column = _seed_cell(client, "s1")
    captured.clear()
    client.post(
        "/api/method/arbor.add_cell_comment",
        json={"sheet": "s1", "node": node, "column": column, "body": "secret text"},
    )
    assert captured
    assert set(captured[0]) == {"sheet", "kind", "can_deliver"}
    assert "secret text" not in repr(captured[0])


def test_the_gate_excludes_a_non_reader(client, captured):
    """The predicate handed to the hub must answer False for a subscriber who
    cannot read the column — the timing of a signal is itself a leak."""
    login(client, ALICE)
    node, column = _seed_cell(client, "s1")
    locked = client.post(
        "/api/method/arbor.execute_action",
        json={
            "action_id": "updateColumn",
            "params": {"sheet": "s1", "column": column, "patch": {"read_level": "owner-only"}},
        },
    )
    assert locked.status_code == 200, locked.text
    captured.clear()
    client.post(
        "/api/method/arbor.add_cell_comment",
        json={"sheet": "s1", "node": node, "column": column, "body": "private"},
    )
    assert captured, "no signal was published"
    gate = captured[-1]["can_deliver"]
    assert gate is not None, "a column-scoped change must be published WITH a gate"
    assert gate(actor(ALICE)) is True
    assert gate(actor(BOB)) is False


def test_markers_are_published_only_AFTER_the_write_commits(client, monkeypatch):
    """The ordering the whole design rests on, and the one that was broken once.

    A publish from inside the transaction announces a row no refetch can see
    yet, and no second marker follows — so the change silently never appears,
    which is the exact bug this feature exists to fix. Proof: count COMMITTED
    rows from a FRESH session at the moment of publish.
    """
    from arbor.standalone import app as app_module

    seen: list[int] = []

    def spy(sheet, kind, can_deliver=None):
        with app_module.SessionLocal() as fresh:
            seen.append(
                fresh.scalar(
                    sa.select(sa.func.count()).select_from(app_module.m.CellComment)
                )
                or 0
            )
        return 0

    login(client, ALICE)
    node, column = _seed_cell(client, "s1")
    monkeypatch.setattr(app_module.hub, "publish", spy)
    resp = client.post(
        "/api/method/arbor.add_cell_comment",
        json={"sheet": "s1", "node": node, "column": column, "body": "hi"},
    )
    assert resp.status_code == 200, resp.text
    assert seen == [1], f"publish saw {seen} committed comments; the write must be visible"


def test_end_to_end_a_comment_write_reaches_a_subscribers_stream(client):
    """The whole chain with nothing stubbed: a real HTTP write in a worker
    thread -> the cross-thread enqueue -> the subscriber's queue -> a frame."""
    from arbor.standalone.realtime import hub

    login(client, ALICE)
    node, column = _seed_cell(client, "s1")

    async def scenario():
        hub.bind_loop(asyncio.get_running_loop())
        sub = hub.subscribe("s1", actor(ALICE))
        try:
            frames = stream_frames(sub.queue, heartbeat=0.05)
            assert await frames.__anext__() == ": connected\n\n"
            # The write happens in a worker thread, exactly as under uvicorn.
            resp = await run_in_threadpool(
                lambda: client.post(
                    "/api/method/arbor.add_cell_comment",
                    json={"sheet": "s1", "node": node, "column": column, "body": "hi"},
                )
            )
            assert resp.status_code == 200, resp.text
            frame = await asyncio.wait_for(frames.__anext__(), timeout=3)
            assert frame == "event: comments\ndata: 1\n\n"
            await frames.aclose()
        finally:
            hub.unsubscribe(sub.sid)

    asyncio.run(scenario())


def test_a_failing_flush_is_logged_not_raised(client, monkeypatch):
    """A marker is best-effort; an exception escaping the flush would fail a
    request whose write already committed. (This path had a NameError for
    exactly as long as it had no test.)"""
    from arbor.standalone import app as app_module
    from arbor.standalone.realtime import enqueue

    login(client, ALICE)
    _node, column = _seed_cell(client, "s1")

    def boom(*_args, **_kwargs):
        raise RuntimeError("event loop is closed")

    monkeypatch.setattr(app_module.hub, "publish", boom)
    with app_module.SessionLocal() as session:
        enqueue(session.info, "s1", "comments", column)
        app_module._flush_realtime(session)  # must swallow, not raise

    # And through a real request: the write must still succeed.
    resp = client.post(
        "/api/method/arbor.add_cell_comment",
        json={"sheet": "s1", "node": _node, "column": column, "body": "hi"},
    )
    assert resp.status_code == 200, resp.text


def test_a_vanished_column_delivers_to_nobody(client, monkeypatch):
    """The safe direction: with the column gone there is nothing to evaluate the
    read gate against, so the marker is dropped rather than sent ungated."""
    from arbor.standalone import app as app_module
    from arbor.standalone.realtime import enqueue

    published: list[tuple] = []
    monkeypatch.setattr(
        app_module.hub, "publish", lambda *a, **k: published.append((a, k)) or 0
    )
    login(client, ALICE)
    make_sheet(client, "s1")
    with app_module.SessionLocal() as session:
        enqueue(session.info, "s1", "comments", "col-that-never-existed")
        app_module._flush_realtime(session)
    assert published == []


def test_a_sheet_scoped_agent_token_cannot_subscribe_outside_its_scope(client):
    """A live subscription is a read: the token gate has to match the one on
    get_sheet_snapshot, or a scoped token receives a signal per change on a
    sheet it may not touch (and the 404-vs-200 split enumerates sheet names)."""
    login(client, ALICE)
    make_sheet(client, "s1")
    make_sheet(client, "s2")
    minted = client.post(
        "/api/method/arbor.issue_agent_token",
        json={"mode": "read", "sheets": ["s1"]},
    )
    assert minted.status_code == 200, minted.text
    token = minted.json()["message"]["token"]

    scoped = TestClient(client.app)
    scoped.headers["X-Arbor-Agent-Token"] = token
    with scoped.stream("GET", "/api/method/arbor.events?sheet=s2") as r:
        assert r.status_code == 403, r.status_code


def test_an_unknown_comment_id_publishes_nothing(client, captured):
    login(client, ALICE)
    make_sheet(client, "s1")
    captured.clear()
    for method in ("resolve_cell_comment", "delete_cell_comment"):
        client.post(f"/api/method/arbor.{method}", json={"comment": "does-not-exist"})
    assert captured == []


# ---------------------------------------------------------------------------
# The Change Request inbox's own read-ACL filter. Realtime made this urgent:
# the `crs` marker's safety argument is "the client refetches, so the read
# endpoint's filtering decides what it sees" — which was false while the inbox
# returned every payload verbatim.
# ---------------------------------------------------------------------------
CAROL = "carol@example.com"


def test_the_cr_inbox_hides_a_request_targeting_an_unreadable_column(client):
    """A CR payload names its target column AND carries the proposed value, so
    an unfiltered inbox handed both to every reader of the sheet — including
    someone with no involvement at all."""
    login(client, ALICE)
    node, column = _seed_cell(client, "s1")
    locked = client.post(
        "/api/method/arbor.execute_action",
        json={
            "action_id": "updateColumn",
            "params": {"sheet": "s1", "column": column, "patch": {"read_level": "owner-only"}},
        },
    )
    assert locked.status_code == 200, locked.text

    # BOB proposes a change to that column (he cannot execute it directly).
    login(client, BOB)
    proposed = client.post(
        "/api/method/arbor.execute_action",
        json={
            "action_id": "updateCell",
            "params": {"sheet": "s1", "node": node, "column": column, "value": "SECRET-VALUE"},
        },
    )
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["message"]["kind"] == "suggested"

    # CAROL is an uninvolved reader: the snapshot correctly hides the column,
    # and the inbox must not hand it back to her either.
    login(client, CAROL)
    snap = msg(client.get("/api/method/arbor.get_sheet_snapshot", params={"sheet": "s1"}))
    assert column not in [c["name"] for c in snap["columns"]]
    inbox = msg(client.get("/api/method/arbor.list_change_requests", params={"sheet": "s1"}))
    assert inbox == [], f"the inbox leaked {inbox}"
    assert "SECRET-VALUE" not in repr(inbox)

    # The column's owner still sees her own review queue.
    login(client, ALICE)
    owner_inbox = msg(client.get("/api/method/arbor.list_change_requests", params={"sheet": "s1"}))
    assert len(owner_inbox) == 1
    assert "SECRET-VALUE" in repr(owner_inbox)


def test_the_cr_inbox_keeps_structural_requests_visible(client):
    """Structure is not column-filtered, so a move/add request has no column to
    hide behind and must stay in everyone's inbox."""
    login(client, ALICE)
    make_sheet(client, "s1")
    parent = client.post(
        "/api/method/arbor.execute_action",
        json={"action_id": "addNode", "params": {"sheet": "s1", "parent": None, "label": "p"}},
    )
    assert parent.status_code == 200, parent.text

    login(client, BOB)  # not the structural owner => suggestion
    proposed = client.post(
        "/api/method/arbor.execute_action",
        json={"action_id": "addNode", "params": {"sheet": "s1", "parent": None, "label": "mine"}},
    )
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["message"]["kind"] == "suggested"

    login(client, CAROL)
    inbox = msg(client.get("/api/method/arbor.list_change_requests", params={"sheet": "s1"}))
    assert len(inbox) == 1
