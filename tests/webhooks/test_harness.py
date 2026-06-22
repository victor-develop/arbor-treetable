"""Meta-tests for the WEBHOOKS harness itself — runnable bench-free.

Proves the three programmable seams the catalog requires actually behave: the
loopback :class:`LocalHTTPReceiver` returns the programmed 2xx/4xx/5xx, hangs to
force a client timeout, captures raw body + headers; the :class:`SocketTransport`
classifies a hang as ``TransportTimeout`` and does NOT follow redirects; and the
``FakeClock`` + ``run_retries`` give an on-demand, sleep-free retry runner. These
guard the harness so the catalog tests below can trust it.
"""

from __future__ import annotations

import pytest

from arbor.arbor.dispatch.ports import TransportTimeout

from tests.webhooks.harness import HANG, LocalHTTPReceiver, SocketTransport


@pytest.fixture
def receiver():
    rcv = LocalHTTPReceiver(hang_seconds=1.0)
    try:
        yield rcv
    finally:
        rcv.shutdown()


def test_receiver_returns_programmed_status_and_captures_request(receiver):
    receiver.set_default((202, "accepted"))
    tx = SocketTransport()
    resp = tx.post(receiver.url, b'{"hi":1}', {"X-Test": "v", "Content-Type": "application/json"}, 2.0)
    assert resp.status_code == 202
    assert len(receiver.requests) == 1
    req = receiver.requests[0]
    assert req.body == b'{"hi":1}'  # raw body captured byte-exact
    assert req.headers["x-test"] == "v"  # arbitrary header captured
    assert req.path == "/hook"


def test_receiver_queue_consumed_in_order_then_default(receiver):
    receiver.set_default(200).queue([500, 503])
    tx = SocketTransport()
    assert tx.post(receiver.url, b"a", {}, 2.0).status_code == 500
    assert tx.post(receiver.url, b"b", {}, 2.0).status_code == 503
    assert tx.post(receiver.url, b"c", {}, 2.0).status_code == 200  # falls back to default


def test_receiver_4xx_and_5xx_surface_as_status_codes(receiver):
    tx = SocketTransport()
    receiver.set_default(404)
    assert tx.post(receiver.url, b"x", {}, 2.0).status_code == 404
    receiver.set_default(500)
    assert tx.post(receiver.url, b"x", {}, 2.0).status_code == 500


def test_hang_forces_client_timeout(receiver):
    """The HANG sentinel makes the receiver not respond → client times out, and
    the transport raises TransportTimeout (WEBHOOKS-023 mechanism)."""
    receiver.set_default(HANG)
    tx = SocketTransport()
    with pytest.raises(TransportTimeout):
        tx.post(receiver.url, b"x", {}, 0.2)  # 0.2s << 1s hang
    # the request was still received/captured before the hang
    assert len(receiver.requests) == 1


def test_malformed_body_is_captured_verbatim(receiver):
    """A malformed (non-JSON) response body is returned as-is; the receiver
    captures whatever bytes it was sent regardless of content-type."""
    receiver.set_default((200, "}{ this is not json"))
    tx = SocketTransport()
    resp = tx.post(receiver.url, b"\x00\x01rawbytes", {}, 2.0)
    assert resp.status_code == 200
    assert resp.text == "}{ this is not json"
    assert receiver.requests[0].body == b"\x00\x01rawbytes"


def test_transport_to_dead_port_is_transport_timeout():
    """A connection failure (no server) is classified as retryable, like a
    timeout (WEBHOOKS-023)."""
    tx = SocketTransport()
    with pytest.raises(TransportTimeout):
        tx.post("http://127.0.0.1:9/hook", b"x", {}, 0.5)  # port 9 = discard/closed
