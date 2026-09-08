"""In-process realtime signals for the standalone adapter (Server-Sent Events).

The design decision that shapes everything here: **a signal carries no content**.
The server says "the comments on sheet S changed"; the client then refetches
through the ordinary, already-ACL-filtered read endpoints. That is deliberate:

* the REVEAL-IMPOSSIBILITY invariant is inherited rather than re-implemented — a
  push path that shipped payloads would have to redo per-column read filtering,
  and getting that subtly wrong is exactly how a "realtime" feature leaks a
  column somebody must not see;
* the server keeps almost no state, so a dropped or duplicated signal costs one
  redundant refetch instead of a wrong screen.

Even so, WHO gets a signal is filtered: a comment on a column you cannot read
must not reach you at all, or the timing alone would tell you that a column you
cannot see exists and is being worked on. Each subscription therefore carries
the Actor that opened it, and the publisher supplies a predicate evaluated per
subscriber (see ``publish``). The Actor is captured at subscribe time, so a
grant that changes mid-connection takes effect on the client's next reconnect.

Transport is SSE, not WebSockets: the traffic is one-way (server -> client),
``EventSource`` reconnects on its own, and it authenticates with the session
cookie the browser already sends — a WebSocket would buy nothing here.

Scope: ONE process. The platform start command runs a single uvicorn worker and
there is no redis, so an in-process hub is the whole broadcast fabric. Two
consequences worth knowing: during a rolling deploy the old and new containers
each have their own hub (a client on the old one misses the new one's writes
until ``EventSource`` reconnects), and adding ``--workers`` later would silently
halve delivery. Both are properties of the deployment, not bugs to code around
here — but a second process REQUIRES a shared bus before it is safe.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional

from ..core.types import Actor

logger = logging.getLogger(__name__)

#: Seconds between heartbeat comments on an idle stream. Proxies and load
#: balancers drop a connection that has been silent too long, and the client
#: cannot tell "quiet" from "dead" either; a comment line is the cheapest
#: keep-alive SSE has (clients ignore it, it is not an event).
HEARTBEAT_SECONDS = 20.0

#: Per-subscriber queue depth. A signal is a dirty marker, so a client that
#: cannot keep up loses nothing by dropping duplicates — the newest marker
#: subsumes the older ones. Small on purpose.
QUEUE_MAXSIZE = 8


@dataclass
class Subscription:
    """One live SSE stream: which sheet it watches, who opened it, its queue."""

    sid: int
    sheet: str
    actor: Actor
    queue: "asyncio.Queue[dict[str, Any]]"


@dataclass
class SignalHub:
    """Fan-out from request handlers (worker threads) to SSE streams (the loop).

    Publishers are ordinary sync endpoints, which FastAPI runs in a threadpool,
    so every enqueue crosses a thread boundary and MUST go through
    ``loop.call_soon_threadsafe``. The loop is bound once at app startup.
    """

    _loop: Optional[asyncio.AbstractEventLoop] = None
    _subs: dict[int, Subscription] = field(default_factory=dict)
    _next_sid: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- lifecycle ---------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the serving event loop (called from the app's lifespan)."""
        self._loop = loop

    def subscribe(self, sheet: str, actor: Actor) -> Subscription:
        queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        with self._lock:
            self._next_sid += 1
            sub = Subscription(sid=self._next_sid, sheet=sheet, actor=actor, queue=queue)
            self._subs[sub.sid] = sub
        return sub

    def unsubscribe(self, sid: int) -> None:
        with self._lock:
            self._subs.pop(sid, None)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    # -- publishing --------------------------------------------------------
    def publish(
        self,
        sheet: str,
        kind: str,
        can_deliver: Optional[Callable[[Actor], bool]] = None,
    ) -> int:
        """Signal ``kind`` to every subscriber of ``sheet``; returns the count.

        ``can_deliver`` is the per-subscriber read gate described in the module
        docstring: it receives the Actor that opened the stream and returns
        whether this particular change is visible to them. Omitted means the
        change is sheet-wide and visible to anyone who can already read the
        sheet. It is called from the PUBLISHER's thread, so it may use the
        publisher's session.

        Safe to call with no loop bound (tests, or a publish during shutdown):
        it becomes a no-op rather than an error, because a lost dirty marker is
        recoverable and a 500 on a comment write is not.
        """
        with self._lock:
            targets = [s for s in self._subs.values() if s.sheet == sheet]
        if not targets:
            return 0
        if can_deliver is not None:
            targets = [s for s in targets if can_deliver(s.actor)]
        if not targets:
            return 0

        loop = self._loop
        if loop is None or loop.is_closed():
            logger.debug("realtime: no event loop bound; dropping %s on %s", kind, sheet)
            return 0

        message = {"kind": kind, "sheet": sheet}
        for sub in targets:
            loop.call_soon_threadsafe(_offer, sub, message)
        return len(targets)


def _offer(sub: Subscription, message: dict[str, Any]) -> None:
    """Enqueue on the loop thread, dropping the signal if the client is behind.

    Dropping is correct here and blocking would not be: the queue already holds
    a dirty marker for this sheet, and the refetch it triggers will pick up
    whatever this signal would have announced.
    """
    try:
        sub.queue.put_nowait(message)
    except asyncio.QueueFull:
        logger.debug("realtime: queue full for sub %s; coalescing", sub.sid)


def format_event(kind: str, data: str = "1") -> str:
    """One SSE frame. ``data`` is a placeholder on purpose — see the module
    docstring: the client's job is to refetch, not to trust a pushed payload."""
    return f"event: {kind}\ndata: {data}\n\n"


def format_comment(text: str) -> str:
    """An SSE comment line (heartbeat / initial flush). Clients ignore it."""
    return f": {text}\n\n"


async def stream_frames(
    queue: "asyncio.Queue[dict[str, Any]]",
    heartbeat: float = HEARTBEAT_SECONDS,
) -> "AsyncIterator[str]":
    """The SSE frame loop for one subscription: an immediate flush, then signals
    as they arrive, with a heartbeat whenever the stream goes quiet.

    Module-level and queue-shaped (rather than inline in the endpoint) so the
    loop is unit-testable: it never ends on its own, which makes it untestable
    through any blocking HTTP client. The endpoint owns subscribe/unsubscribe;
    this owns only the framing.
    """
    # Flush something immediately: a buffering proxy holds a response with an
    # empty body, and the client cannot distinguish "connected" from "hung"
    # until the first byte arrives.
    yield format_comment("connected")
    while True:
        try:
            message = await asyncio.wait_for(queue.get(), timeout=heartbeat)
        except asyncio.TimeoutError:
            # Idle: proxies drop a silent connection and the client cannot tell
            # quiet from dead either.
            yield format_comment("ping")
            continue
        yield format_event(message["kind"])


#: The process-wide hub. One app, one loop, one fabric.
hub = SignalHub()
