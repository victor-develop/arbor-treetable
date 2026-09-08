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
# The publish side: every comment write signals, and only to readers.
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


def test_each_comment_write_publishes_one_signal(client, captured):
    login(client, ALICE)
    node, column = _seed_cell(client, "s1")

    added = client.post(
        "/api/method/arbor.add_cell_comment",
        json={"sheet": "s1", "node": node, "column": column, "body": "first"},
    )
    assert added.status_code == 200, added.text
    comment = added.json()["message"]["name"]
    assert [c["sheet"] for c in captured] == ["s1"]
    assert captured[0]["kind"] == "comments"

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
    # resolve and delete signal too — the badge changes for everyone watching.
    assert [c["sheet"] for c in captured] == ["s1", "s1", "s1"]


def test_the_signal_carries_no_content(client, captured):
    """Regression guard on the whole design: if a payload ever rides along, the
    push path would have to re-implement read filtering."""
    login(client, ALICE)
    node, column = _seed_cell(client, "s1")
    client.post(
        "/api/method/arbor.add_cell_comment",
        json={"sheet": "s1", "node": node, "column": column, "body": "secret text"},
    )
    assert captured
    assert set(captured[0]) == {"sheet", "kind", "can_deliver"}
    assert "secret text" not in repr(captured[0])


def test_the_gate_it_publishes_with_excludes_a_non_reader(client, captured):
    """The predicate handed to the hub must answer False for a subscriber who
    cannot read the commented column — the timing of a signal is itself a leak."""
    login(client, ALICE)
    node, column = _seed_cell(client, "s1")
    # Lock the column down to its owner (ALICE), then comment on it.
    locked = client.post(
        "/api/method/arbor.execute_action",
        json={
            "action_id": "updateColumn",
            "params": {"sheet": "s1", "column": column, "patch": {"read_level": "owner-only"}},
        },
    )
    assert locked.status_code == 200, locked.text
    client.post(
        "/api/method/arbor.add_cell_comment",
        json={"sheet": "s1", "node": node, "column": column, "body": "private"},
    )
    assert captured, "no signal was published"
    gate = captured[-1]["can_deliver"]
    assert gate is not None, "a column-scoped change must be published WITH a gate"
    assert gate(actor(ALICE)) is True
    assert gate(actor(BOB)) is False


def test_the_marker_is_published_only_AFTER_the_write_commits(client, monkeypatch):
    """The ordering the whole design rests on, and the one that was broken.

    Background tasks run BEFORE a plain ``Depends(get_db)`` tears down, so a
    handler that scheduled the publish without committing first announced a row
    no refetch could see yet — and no second marker follows, so the comment
    silently never appeared. Proof: count COMMITTED rows from a fresh session at
    the moment of publish.
    """
    from arbor.standalone import app as app_module

    seen: list[int] = []

    def spy(sheet: str, column: str) -> None:
        with app_module.SessionLocal() as fresh:
            seen.append(
                fresh.scalar(
                    sa.select(sa.func.count()).select_from(app_module.m.CellComment)
                )
                or 0
            )

    monkeypatch.setattr(app_module, "_publish_comment_signal", spy)
    login(client, ALICE)
    node, column = _seed_cell(client, "s1")
    resp = client.post(
        "/api/method/arbor.add_cell_comment",
        json={"sheet": "s1", "node": node, "column": column, "body": "hi"},
    )
    assert resp.status_code == 200, resp.text
    assert seen == [1], f"publish saw {seen} committed comments; expected the write to be visible"


def test_end_to_end_a_comment_write_reaches_a_subscribers_stream(client):
    """The whole chain with nothing stubbed: write -> publish -> queue -> frame.

    Every other publish-side test monkeypatches ``hub.publish``, so without this
    the wiring is only ever exercised in halves.
    """
    from arbor.standalone.realtime import hub

    login(client, ALICE)
    node, column = _seed_cell(client, "s1")

    async def scenario():
        hub.bind_loop(asyncio.get_running_loop())
        sub = hub.subscribe("s1", actor(ALICE))
        try:
            frames = stream_frames(sub.queue, heartbeat=0.02)
            assert await frames.__anext__() == ": connected\n\n"
            # The real publisher, reading the real committed row and the real ACL.
            from arbor.standalone import app as app_module

            await run_in_threadpool(app_module._publish_comment_signal, "s1", column)
            frame = await asyncio.wait_for(frames.__anext__(), timeout=2)
            assert frame == "event: comments\ndata: 1\n\n"
            await frames.aclose()
        finally:
            hub.unsubscribe(sub.sid)

    # Seed a real comment first (sync, outside the loop).
    assert (
        client.post(
            "/api/method/arbor.add_cell_comment",
            json={"sheet": "s1", "node": node, "column": column, "body": "hi"},
        ).status_code
        == 200
    )
    asyncio.run(scenario())


def test_publishing_from_another_thread_still_reaches_the_loop():
    """Publishers are threadpool workers, so the enqueue crosses threads. Every
    other hub test publishes from the loop's own thread, where a plain
    ``call_soon`` would look identical — this is what pins the requirement."""

    async def scenario():
        hub = SignalHub()
        hub.bind_loop(asyncio.get_running_loop())
        sub = hub.subscribe("s1", actor(ALICE))
        await run_in_threadpool(hub.publish, "s1", "comments")
        message = await asyncio.wait_for(sub.queue.get(), timeout=2)
        assert message == {"kind": "comments", "sheet": "s1"}

    asyncio.run(scenario())


def test_a_failing_publish_is_logged_not_raised(client, monkeypatch):
    """A marker is best-effort; an exception escaping it would 500 a write that
    already succeeded. (This path had a NameError for exactly as long as it had
    no test.)"""
    from arbor.standalone import app as app_module

    def boom(*_args, **_kwargs):
        raise RuntimeError("event loop is closed")

    monkeypatch.setattr(app_module, "_repo", boom)
    app_module._publish_comment_signal("s1", "col")  # must not raise


def test_a_sheet_scoped_agent_token_cannot_subscribe_outside_its_scope(client):
    """A live subscription is a read: the token gate has to match the one on
    get_sheet_snapshot, or a scoped token receives a signal per comment on a
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
    for method in ("resolve_cell_comment", "delete_cell_comment"):
        client.post(f"/api/method/arbor.{method}", json={"comment": "does-not-exist"})
    assert captured == []


def test_a_column_deleted_before_the_publish_delivers_to_nobody(client, monkeypatch):
    """The safe direction: with the column gone there is nothing to evaluate the
    read gate against, so the marker is dropped rather than sent ungated."""
    from arbor.standalone import app as app_module

    published: list[tuple] = []
    monkeypatch.setattr(
        app_module.hub, "publish", lambda *a, **k: published.append((a, k)) or 0
    )
    login(client, ALICE)
    make_sheet(client, "s1")
    app_module._publish_comment_signal("s1", "col-that-never-existed")
    assert published == []
