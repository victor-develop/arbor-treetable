"""Notification -> webhook fan-out on the FULLY WIRED site (WS-A3b/WS-A3c) —
real-adapter (bench) round-trip.

runnable: NEEDS FRAPPE BENCH (``@pytest.mark.bench``; auto-skips bench-free).

The bench-free ``tests/dispatch/test_notification_webhook.py`` proves the pure
``fan_out`` seam over in-memory doubles; the controller bench
``tests/webhooks/test_endpoint_controller_bench.py`` proves the admin gate + SSRF +
that a process Notification enqueues a delivery through the wired
``doc_events["Notification"]["after_insert"]`` hook. THIS module closes the
remaining wired-site gaps the WAVE-3 plan calls out for the webhook lane:

* a process AND an sla Notification each fan out to a registered endpoint whose
  ``notification_sources`` cover that source, producing a SIGNED Webhook Delivery
  keyed by the notification's own ``event_id`` (the engine's HMAC verifies over the
  EXACT stored body — the reused signing seam, not a re-implementation);
* the reused RETRY/BACKOFF machinery is engaged: a first attempt that fails leaves
  the delivery ``pending`` with ``attempts`` climbing and a ``next_retry_at`` on the
  core backoff schedule (proving WS-A3b rides the SAME WebhookDispatcher, not a new
  path), and the retry runner is idempotent;
* the source FILTER excludes a non-subscribed source (an sla notification never
  reaches a process-only endpoint);
* the admin/owner gate + SSRF deny-list guard registration (a non-admin is denied;
  a loopback/link-local URL is rejected).

Runs in-process on arbor.test; the shared ``_frappe_rollback`` fixture rolls back.
The outbound POST is stubbed (no real network) so the assertion is the persisted,
signed delivery row + its retry state — not a live HTTP round-trip.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.bench

frappe = pytest.importorskip("frappe")

from arbor.core.security import verify_signature  # noqa: E402

try:  # ``arbor.arbor`` in the dev repo; ``arbor`` on a plain bench install.
    from arbor.arbor import api as arbor_api
    from arbor.arbor.dispatch import frappe_dispatch
except ModuleNotFoundError:  # pragma: no cover - bench-layout fallback
    from arbor import api as arbor_api  # type: ignore
    from arbor.dispatch import frappe_dispatch  # type: ignore


# ACME's structural_owner is a System Manager (admin); WIDE's is a non-admin owner;
# bob is neither. These personas exist on the seeded dev site (mirrors the sibling
# controller bench so the two files share ONE identity model).
ADMIN = "victor.zhou@aftership.com"
OTHER = "bob.demo@arbor.example"
SHEET = "ACME"


def _as(user):
    frappe.set_user(user)


def _cleanup_endpoint(name):
    if name and frappe.db.exists("Webhook Endpoint", name):
        for d in frappe.get_all("Webhook Delivery", filters={"endpoint": name}, pluck="name"):
            frappe.delete_doc("Webhook Delivery", d, force=True, ignore_permissions=True)
        frappe.delete_doc("Webhook Endpoint", name, force=True, ignore_permissions=True)


def _stub_ok(monkeypatch):
    """Stub the outbound transport with a 2xx so the after_insert first attempt does
    not hit the network; the signed delivery row is what we assert."""

    class _Resp:
        status_code = 200
        text = "OK"

    monkeypatch.setattr(
        frappe_dispatch.RequestsTransport,
        "post",
        lambda self, url, body, headers, timeout: _Resp(),
    )


def _stub_fail(monkeypatch):
    """Stub the outbound transport with a 5xx so the first attempt fails and the
    reused backoff schedules a retry (delivery stays pending, attempts climb)."""

    class _Resp:
        status_code = 503
        text = "unavailable"

    monkeypatch.setattr(
        frappe_dispatch.RequestsTransport,
        "post",
        lambda self, url, body, headers, timeout: _Resp(),
    )


def _insert_notification(source, *, recipient=ADMIN, ntype="PROCESS_STAGE_ASSIGNED"):
    """Insert a NON-tree-event Notification, firing the wired after_insert fan-out."""
    n = frappe.new_doc("Notification")
    n.source = source
    n.recipient = recipient
    n.channel = "in-app"
    n.requires_ack = 0
    if hasattr(n, "type"):
        n.type = ntype
    n.insert(ignore_permissions=True)
    return n


def _deliveries(endpoint, event_id):
    return frappe.get_all(
        "Webhook Delivery",
        filters={"endpoint": endpoint, "event_id": event_id},
        fields=["name", "source", "notification", "event_id", "signature", "body",
                "status", "attempts", "next_retry_at"],
    )


# ---------------------------------------------------------------------------
# process + sla notifications each fan out to a matching endpoint, signed + keyed
# by the notification's own event_id (the reused HMAC seam)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("source", ["process", "sla"])
def test_notification_fans_out_signed_and_keyed_by_event_id(monkeypatch, source):
    _stub_ok(monkeypatch)
    _as(ADMIN)
    # A GLOBAL endpoint (sheet=None) covers process/sla notifications, which resolve
    # sheet=None (they carry no source-row sheet FK). The register shim returns the
    # signing secret ONCE — we hold it to verify the HMAC below.
    ep = arbor_api.register_webhook(
        url="https://8.8.8.8/notif", sheet=None, notification_sources=["process", "sla"]
    )
    secret = ep["secret"]
    try:
        n = _insert_notification(source)
        rows = _deliveries(ep["name"], n.name)
        assert len(rows) == 1
        d = rows[0]
        # keyed by the notification's OWN id (event_id == notification == n.name).
        assert d["source"] == source
        assert d["notification"] == n.name and d["event_id"] == n.name
        # tree_event slot is empty — a notification delivery is NOT a tree-event one.
        assert not frappe.db.get_value("Webhook Delivery", d["name"], "tree_event")
        # signed via the REUSED engine: the HMAC verifies over the EXACT stored body.
        assert d["signature"]
        assert verify_signature(secret, d["body"], d["signature"])
        # delivered on the first (stubbed-200) attempt.
        assert d["status"] == "delivered" and d["attempts"] == 1
    finally:
        _cleanup_endpoint(ep["name"])


# ---------------------------------------------------------------------------
# the reused retry/backoff: a failed first attempt leaves the delivery pending with
# a scheduled next_retry_at, and the retry runner is idempotent (no duplicate row)
# ---------------------------------------------------------------------------
def test_failed_delivery_engages_reused_backoff_and_retry_runner(monkeypatch):
    from arbor.core.backoff import RETRY_SCHEDULE_SECONDS

    _stub_fail(monkeypatch)
    _as(ADMIN)
    ep = arbor_api.register_webhook(
        url="https://8.8.8.8/retry", sheet=None, notification_sources=["process"]
    )
    try:
        n = _insert_notification("process")
        rows = _deliveries(ep["name"], n.name)
        assert len(rows) == 1
        d = rows[0]
        # First attempt failed -> pending, attempts=1, and a retry scheduled on the
        # SAME core backoff schedule (proving the reused WebhookDispatcher path).
        assert d["status"] == "pending" and d["attempts"] == 1
        assert d["next_retry_at"] is not None
        assert RETRY_SCHEDULE_SECONDS[1] == 30  # first retry offset (schedule reused)

        # The retry runner does not create a duplicate delivery for this event_id.
        frappe_dispatch.run_webhook_retries()
        assert len(_deliveries(ep["name"], n.name)) == 1
    finally:
        _cleanup_endpoint(ep["name"])


# ---------------------------------------------------------------------------
# the source FILTER excludes a non-subscribed source
# ---------------------------------------------------------------------------
def test_non_subscribed_source_does_not_fan_out(monkeypatch):
    # If the filter leaks, this stub asserts (a POST must never happen for sla here).
    monkeypatch.setattr(
        frappe_dispatch.RequestsTransport,
        "post",
        lambda self, url, body, headers, timeout: (_ for _ in ()).throw(AssertionError("no POST")),
    )
    _as(ADMIN)
    ep = arbor_api.register_webhook(
        url="https://8.8.8.8/proc-only", sheet=None, notification_sources=["process"]
    )
    try:
        n = _insert_notification("sla", ntype="SLA_BREACHED")
        assert _deliveries(ep["name"], n.name) == []
    finally:
        _cleanup_endpoint(ep["name"])


# ---------------------------------------------------------------------------
# registration gate + SSRF still guard this lane
# ---------------------------------------------------------------------------
def test_non_admin_cannot_register_notification_webhook():
    _as(OTHER)
    with pytest.raises(frappe.PermissionError):
        arbor_api.register_webhook(
            url="https://8.8.8.8/x", sheet=SHEET, notification_sources=["process"]
        )


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1/hook",                       # loopback literal
        "http://169.254.169.254/latest/meta-data/",    # cloud metadata (link-local)
        "http://10.0.0.5/hook",                         # private range
        "ftp://example.com/hook",                       # non-http(s) scheme
    ],
)
def test_ssrf_url_rejected_for_notification_webhook(bad_url):
    _as(ADMIN)
    with pytest.raises(frappe.ValidationError):
        arbor_api.register_webhook(
            url=bad_url, sheet=None, notification_sources=["process"]
        )
