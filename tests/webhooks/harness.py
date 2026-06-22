"""Webhook test harness — runnable bench-free.

Three deterministic seams the WEBHOOKS catalog (`tests/webhooks.md`) demands, with
NO wall-clock and NO live external socket beyond a loopback receiver:

1. :class:`LocalHTTPReceiver` — a programmable HTTP test double bound to
   ``127.0.0.1`` on an ephemeral port. It is the endpoint the dispatcher actually
   POSTs to over a real socket, so the genuine outbound transport path is
   exercised (not just an in-memory ``FakeTransport``). It is programmable to
   return 2xx / 4xx / 5xx, to *hang* (so the client times out), or to return a
   *malformed* body, and it captures the raw body + all headers of every request
   for assertion. The receiver — NOT the dispatcher under test — verifies HMAC,
   matching the catalog's "receiver verifies HMAC" rule.

2. :class:`SocketTransport` — adapts the loopback receiver to the dispatcher's
   ``arbor.arbor.dispatch.ports.Transport`` protocol: ``post(url, body, headers,
   timeout)`` over ``urllib`` (redirects NOT followed, per WEBHOOKS-030), raising
   ``TransportTimeout`` on a socket timeout (WEBHOOKS-023). Pure stdlib.

3. A freezable clock (reused from ``arbor.arbor.dispatch.testing.FakeClock``) and
   an on-demand retry runner (the dispatcher's ``run_retries``) so the backoff
   schedule (0s, 30s, 5m, 30m, 2h, 12h) is asserted with zero real sleeps.

Plus an **event factory**: :func:`drive_event` / :class:`WebhookWorld` run the real
``arbor.core.executor.execute_action`` against the ONE canonical seed
(``tests/fixtures/canonical.py``) so genuine Tree Events land on the append-only
stream via the emitter, then :class:`EventBridge` adapts each emitted core
``TreeEvent`` into the dispatcher's ``EventView`` and hands it to the
``WebhookDispatcher``. This proves webhooks ride the SAME stream the executor
emits, rather than fabricating Tree Event rows by hand (which would bypass the
emitter).

Nothing here imports frappe.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from arbor.core.security import verify_signature
from arbor.core.testing import RecordingEventSink
from arbor.core.types import TreeEvent

from arbor.arbor.dispatch.ports import TransportTimeout
from arbor.arbor.dispatch.testing import FakeClock, FakeEndpoint, InMemoryWebhookStore
from arbor.arbor.dispatch.webhook import (
    EVENT_ID_HEADER,
    SIGNATURE_HEADER,
    WebhookDispatcher,
)

# ---------------------------------------------------------------------------
# 1. Programmable loopback HTTP receiver
# ---------------------------------------------------------------------------
# Sentinels for programmed receiver behaviour.
HANG = "HANG"  # do not respond — force the client to time out (WEBHOOKS-023)


@dataclass
class CapturedRequest:
    """One inbound POST captured by the receiver."""

    body: bytes
    headers: dict[str, str]
    path: str

    @property
    def signature(self) -> Optional[str]:
        return self.headers.get(SIGNATURE_HEADER.lower())

    @property
    def event_id(self) -> Optional[str]:
        return self.headers.get(EVENT_ID_HEADER.lower())

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    def verify(self, secret: str) -> bool:
        """Receiver-side HMAC check over the EXACT received bytes (WEBHOOKS-016)."""
        return self.signature is not None and verify_signature(secret, self.body, self.signature)


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 (stdlib API)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        receiver: "LocalHTTPReceiver" = self.server.receiver  # type: ignore[attr-defined]
        receiver._record(CapturedRequest(body=body, headers=headers, path=self.path))

        status, resp_body = receiver._next_outcome()
        if status == HANG:
            # Sleep past any reasonable client timeout, then close. The client
            # raises a socket timeout first (WEBHOOKS-023).
            time.sleep(receiver.hang_seconds)
            try:
                self.send_response(200)
                self.end_headers()
            except Exception:  # pragma: no cover - client already gone
                pass
            return

        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if resp_body:
            self.wfile.write(resp_body if isinstance(resp_body, bytes) else resp_body.encode())

    def log_message(self, *args, **kwargs):  # silence test noise
        return


class LocalHTTPReceiver:
    """A real HTTP server on 127.0.0.1:<ephemeral> the dispatcher POSTs to.

    Program responses with ``set_default(...)`` (applied to every request) or
    ``queue([...])`` (consumed one per request, then falls back to the default).
    Each outcome is either an int status code, ``(status, body)``, or the
    :data:`HANG` sentinel to force a client timeout. Captured requests are in
    ``self.requests``.
    """

    def __init__(self, hang_seconds: float = 1.0) -> None:
        self.requests: list[CapturedRequest] = []
        self._queue: list[Any] = []
        self._default: Any = 200
        self.hang_seconds = hang_seconds
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.receiver = self  # type: ignore[attr-defined]
        self.host, self.port = self._server.server_address[0], self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    # -- url --------------------------------------------------------------
    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/hook"

    # -- programming ------------------------------------------------------
    def set_default(self, outcome: Any) -> "LocalHTTPReceiver":
        self._default = outcome
        return self

    def queue(self, outcomes: list[Any]) -> "LocalHTTPReceiver":
        self._queue = list(outcomes)
        return self

    def _next_outcome(self):
        with self._lock:
            outcome = self._queue.pop(0) if self._queue else self._default
        if outcome == HANG:
            return HANG, None
        if isinstance(outcome, tuple):
            return outcome[0], outcome[1]
        return outcome, ""

    def _record(self, req: CapturedRequest) -> None:
        with self._lock:
            self.requests.append(req)

    # -- lifecycle --------------------------------------------------------
    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()


# ---------------------------------------------------------------------------
# 2. Transport adapter over a real socket (stdlib urllib)
# ---------------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to auto-follow redirects (WEBHOOKS-030)."""

    def redirect_request(self, *args, **kwargs):  # noqa: D401
        return None


@dataclass
class _Resp:
    status_code: int
    text: str = ""


class SocketTransport:
    """Adapts a loopback POST to ``arbor.arbor.dispatch.ports.Transport``.

    Does NOT follow redirects. A socket timeout (or connection failure) is
    surfaced as ``TransportTimeout`` so the dispatcher classifies it as a
    retryable failure (WEBHOOKS-023). A 3xx/4xx/5xx comes back as an ordinary
    ``_Resp`` with the real status code (WEBHOOKS-030/022).
    """

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect)

    def post(self, url: str, body: bytes, headers: dict[str, str], timeout: float) -> _Resp:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                return _Resp(resp.status, resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:  # non-2xx, incl. 3xx not followed
            return _Resp(exc.code, (exc.read() or b"").decode("utf-8", "replace"))
        except (socket.timeout, TimeoutError) as exc:
            raise TransportTimeout(str(exc)) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise TransportTimeout(str(reason)) from exc
            raise TransportTimeout(f"connection failed: {reason}") from exc


# ---------------------------------------------------------------------------
# 3. EventBridge — core TreeEvent -> dispatcher EventView
# ---------------------------------------------------------------------------
@dataclass
class EventBridge:
    """Adapts a core ``arbor.core.types.TreeEvent`` (as produced by the executor's
    EventSink) into the dispatcher's ``EventView`` shape (``.name`` == event_id).

    This is exactly what the Frappe adapter does when it reads back a persisted
    Tree Event row to hand to the dispatchers — proving the webhook fan-out
    consumes the SAME event the executor emitted.
    """

    name: str
    sheet: str
    type: str
    payload: dict[str, Any]
    actor: Optional[str]
    actor_type: str
    change_request: Optional[str]
    timestamp: Optional[str]

    @classmethod
    def of(cls, event: TreeEvent) -> "EventBridge":
        at = event.actor_type
        return cls(
            name=event.event_id,
            sheet=event.sheet,
            type=event.type,
            payload=dict(event.payload or {}),
            actor=event.actor,
            actor_type=at.value if hasattr(at, "value") else str(at),
            change_request=event.change_request,
            timestamp=event.timestamp,
        )


# ---------------------------------------------------------------------------
# Canonical world wiring: seed -> WebhookStore node ranges + dispatcher
# ---------------------------------------------------------------------------
def store_with_canonical_ranges(repo) -> InMemoryWebhookStore:
    """A fresh ``InMemoryWebhookStore`` whose node ranges mirror the canonical
    seed's NestedSet (so branch-scope matching uses the SAME lft/rgt the executor
    sees). Reuses the ONE canonical repo — no second seed definition."""
    store = InMemoryWebhookStore()
    for node in repo.nodes.values():
        store.set_node_range(node.name, node.lft, node.rgt)
    return store


def endpoint(
    name: str = "EXT_ENDPOINT",
    *,
    url: str,
    secret: str = "test-secret",
    event_types: Optional[list[str]] = None,
    scope: str = "sheet",
    target: str = "S",
    active: bool = True,
) -> FakeEndpoint:
    return FakeEndpoint(
        name=name,
        url=url,
        secret=secret,
        event_types=event_types or ["NODE_VALUE_UPDATED", "CHANGE_APPROVED"],
        scope=scope,
        target=target,
        active=active,
    )


@dataclass
class WebhookWorld:
    """Bundles the canonical seed, the webhook store/dispatcher, the event sink the
    executor emits into, the loopback receiver, and a frozen clock — the full
    bench-free webhook rig.
    """

    fx: Any  # CanonicalFixture
    repo: Any
    sink: RecordingEventSink
    store: InMemoryWebhookStore
    transport: Any
    clock: FakeClock
    dispatcher: WebhookDispatcher
    receiver: Optional[LocalHTTPReceiver] = None
    _dispatched: int = field(default=0, repr=False)

    # -- event factory: drive the REAL executor -> emit -> fan out --------
    def execute(self, action_id: str, params: dict, actor) -> Any:
        """Run ``execute_action`` (real emitter) then fan every NEW emitted event
        out through the webhook dispatcher. Returns the executor Outcome."""
        from arbor.core.executor import execute_action

        before = len(self.sink.events)
        # Snapshot the tree's ranges BEFORE the mutation. A NODE_DELETED event
        # records the pre-delete position, so its subtree membership must be
        # resolved against this pre-mutation snapshot (after the delete, both the
        # node AND its shrunken ancestors' ranges have moved).
        pre_ranges = {n.name: (n.lft, n.rgt) for n in self.repo.nodes.values()}

        outcome = execute_action(action_id, params, actor, self.repo, self.sink)

        # Live (post-mutation) ranges: nodes created/moved get their current
        # lft/rgt, exactly what the Frappe ``get_node_range`` reads at emit time.
        post_ranges = {n.name: (n.lft, n.rgt) for n in self.repo.nodes.values()}

        for ev in self.sink.events[before:]:
            # Delete events match against the pre-delete tree; all others against
            # the live post-mutation tree.
            self.store.node_ranges = pre_ranges if ev.type == "NODE_DELETED" else post_ranges
            self.dispatcher.on_tree_event(EventBridge.of(ev))
        self.store.node_ranges = post_ranges
        return outcome

    def deliveries(self) -> list[dict]:
        return list(self.store.deliveries.values())

    def deliveries_for(self, endpoint_name: str) -> list[dict]:
        return [d for d in self.deliveries() if d["endpoint"] == endpoint_name]


def make_world(
    *,
    settings: Optional[dict] = None,
    receiver: Optional[LocalHTTPReceiver] = None,
    transport: Optional[Any] = None,
    jitter: bool = False,
) -> WebhookWorld:
    """Build a WebhookWorld over the ONE canonical seed.

    Pass a :class:`LocalHTTPReceiver` to exercise the real socket path (and a
    :class:`SocketTransport`), or omit both to inject a custom ``transport``
    double for pure no-socket runs. Jitter defaults OFF for exact
    ``next_retry_at`` assertions.
    """
    from tests.fixtures.canonical import seed_canonical_sheet

    fx = seed_canonical_sheet(settings=settings)
    store = store_with_canonical_ranges(fx.repo)
    if transport is None:
        transport = SocketTransport()
    clock = FakeClock()
    dispatcher = WebhookDispatcher(store, transport, clock, jitter=jitter)
    return WebhookWorld(
        fx=fx,
        repo=fx.repo,
        sink=RecordingEventSink(),
        store=store,
        transport=transport,
        clock=clock,
        dispatcher=dispatcher,
        receiver=receiver,
    )
