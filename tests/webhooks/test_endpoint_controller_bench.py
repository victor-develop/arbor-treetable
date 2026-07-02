"""Webhook Endpoint controller — closed-set event_type enforcement (WEBHOOKS-044).

runnable: NEEDS FRAPPE BENCH (``@pytest.mark.bench``; auto-skipped when frappe is
absent). The bench-free suite already asserts the *contract* (the bad type is
outside ``EVENT_TYPES``); this asserts the controller actually REJECTS it at
create time so a bogus subscription can't be persisted.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.bench

frappe = pytest.importorskip("frappe")


def _new_endpoint(event_types, notification_sources=None):
    doc = frappe.new_doc("Webhook Endpoint")
    doc.url = "http://example.test/hook"
    doc.scope = "sheet"
    doc.target = "S" if frappe.db.exists("Tree Sheet", "S") else None
    # event_types is a JSON column: store the encoded string (the dispatcher parses it)
    doc.event_types = json.dumps(event_types)
    if notification_sources is not None:
        doc.notification_sources = json.dumps(notification_sources)
    doc.active = 1
    return doc


def test_rejects_event_type_outside_closed_set():
    with pytest.raises(frappe.ValidationError, match="Unknown Tree Event type"):
        _new_endpoint(["NODE_VALUE_UPDATED", "NODE_EXPLODED"]).insert(ignore_permissions=True)


def test_accepts_valid_event_types():
    doc = _new_endpoint(["NODE_VALUE_UPDATED", "CHANGE_APPROVED"])
    doc.insert(ignore_permissions=True)
    assert doc.name
    frappe.delete_doc("Webhook Endpoint", doc.name, force=True, ignore_permissions=True)


def test_rejects_notification_source_outside_closed_set():
    """WS-A3a: the extended ``notification_sources`` filter is validated against the
    same closed set the delivery ``source`` column allows (minus ``tree_event``),
    so a bogus/never-emitted source can't be persisted."""
    with pytest.raises(frappe.ValidationError, match="Unknown notification source"):
        _new_endpoint(["NODE_VALUE_UPDATED"], ["comment", "telepathy"]).insert(
            ignore_permissions=True
        )


def test_rejects_tree_event_as_notification_source():
    """``tree_event`` rides ``event_types``; it is NOT a valid notification source."""
    with pytest.raises(frappe.ValidationError, match="Unknown notification source"):
        _new_endpoint(["NODE_VALUE_UPDATED"], ["tree_event"]).insert(ignore_permissions=True)


def test_accepts_valid_notification_sources():
    doc = _new_endpoint(["NODE_VALUE_UPDATED"], ["comment", "process", "sla", "change_request"])
    doc.insert(ignore_permissions=True)
    assert doc.name
    frappe.delete_doc("Webhook Endpoint", doc.name, force=True, ignore_permissions=True)


# ---------------------------------------------------------------------------
# WS-A3c — registration shims + SSRF + admin gate + Notification fan-out.
#
# In-process on arbor.test (rolled back by the shared _frappe_rollback fixture).
# ACME's structural_owner is an admin (victor); WIDE's is a non-admin (pm); bob is
# neither. These fixtures already exist on the seeded dev site.
# ---------------------------------------------------------------------------
from arbor.arbor import api as arbor_api  # noqa: E402

ADMIN = "victor.zhou@aftership.com"      # System Manager + ACME structural_owner
OWNER_NONADMIN = "pm@arbor.example"      # WIDE/ECOM structural_owner, NOT admin
OTHER = "bob.demo@arbor.example"         # neither admin nor a sheet owner
ADMIN_SHEET = "ACME"
OWNER_SHEET = "WIDE"


def _as(user):
    frappe.set_user(user)


def _cleanup_endpoint(name):
    if name and frappe.db.exists("Webhook Endpoint", name):
        for d in frappe.get_all("Webhook Delivery", filters={"endpoint": name}, pluck="name"):
            frappe.delete_doc("Webhook Delivery", d, force=True, ignore_permissions=True)
        frappe.delete_doc("Webhook Endpoint", name, force=True, ignore_permissions=True)


# --- admin gate -------------------------------------------------------------
def test_admin_can_register_webhook_on_sheet():
    _as(ADMIN)
    out = arbor_api.register_webhook(
        url="https://8.8.8.8/arbor",
        sheet=ADMIN_SHEET,
        label="ci",
        notification_sources=["process", "comment"],
    )
    try:
        assert out["name"]
        assert out["sheet"] == ADMIN_SHEET
        assert out["owner_user"] == ADMIN
        assert set(out["notification_sources"]) == {"process", "comment"}
        # secret is returned ONCE at register...
        assert isinstance(out.get("secret"), str) and len(out["secret"]) >= 20
        # ...and is NEVER echoed by a list read.
        listed = arbor_api.list_webhooks(sheet=ADMIN_SHEET)
        row = next(r for r in listed if r["name"] == out["name"])
        assert "secret" not in row
    finally:
        _cleanup_endpoint(out["name"])


def test_structural_owner_nonadmin_can_register_on_own_sheet():
    _as(OWNER_NONADMIN)
    out = arbor_api.register_webhook(
        url="https://8.8.8.8/wide", sheet=OWNER_SHEET, notification_sources=["sla"]
    )
    try:
        assert out["sheet"] == OWNER_SHEET
        assert out["owner_user"] == OWNER_NONADMIN
    finally:
        _cleanup_endpoint(out["name"])


def test_non_admin_non_owner_register_is_denied():
    _as(OTHER)
    with pytest.raises(frappe.PermissionError):
        arbor_api.register_webhook(
            url="https://8.8.8.8/x", sheet=ADMIN_SHEET, notification_sources=["process"]
        )
    # nothing persisted for the sheet by this caller
    assert not frappe.get_all(
        "Webhook Endpoint", filters={"sheet": ADMIN_SHEET, "owner_user": OTHER}, pluck="name"
    )


def test_owner_cannot_register_on_a_sheet_they_do_not_own():
    _as(OWNER_NONADMIN)
    with pytest.raises(frappe.PermissionError):
        arbor_api.register_webhook(
            url="https://8.8.8.8/x", sheet=ADMIN_SHEET, notification_sources=["process"]
        )


# --- SSRF deny-list ---------------------------------------------------------
@pytest.mark.parametrize(
    "bad_url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata (link-local)
        "http://localhost:8000/hook",                 # loopback name
        "http://127.0.0.1/hook",                       # loopback literal
        "http://10.0.0.5/hook",                        # private range
        "http://192.168.1.1/hook",                     # private range
        "http://[::1]/hook",                           # IPv6 loopback
        "http://metadata.google.internal/x",           # metadata hostname
        "ftp://example.com/hook",                       # non-http(s) scheme
        "file:///etc/passwd",                           # non-http(s) scheme
        "http:///nohost",                               # missing host
    ],
)
def test_ssrf_urls_are_rejected(bad_url):
    _as(ADMIN)
    with pytest.raises(frappe.ValidationError):
        arbor_api.register_webhook(url=bad_url, sheet=ADMIN_SHEET, notification_sources=["process"])


def test_ssrf_blocked_on_update_too():
    _as(ADMIN)
    out = arbor_api.register_webhook(
        url="https://8.8.8.8/ok", sheet=ADMIN_SHEET, notification_sources=["process"]
    )
    try:
        with pytest.raises(frappe.ValidationError):
            arbor_api.update_webhook(out["name"], url="http://127.0.0.1/evil")
        # the original public url survived the rejected update
        assert frappe.db.get_value("Webhook Endpoint", out["name"], "url") == "https://8.8.8.8/ok"
    finally:
        _cleanup_endpoint(out["name"])


def test_bad_notification_source_rejected_by_shim():
    _as(ADMIN)
    with pytest.raises(frappe.ValidationError):
        arbor_api.register_webhook(
            url="https://8.8.8.8/ok", sheet=ADMIN_SHEET, notification_sources=["telepathy"]
        )


# --- delete gate ------------------------------------------------------------
def test_delete_webhook_admin_gated():
    _as(ADMIN)
    out = arbor_api.register_webhook(
        url="https://8.8.8.8/del", sheet=ADMIN_SHEET, notification_sources=["process"]
    )
    name = out["name"]
    _as(OTHER)
    with pytest.raises(frappe.PermissionError):
        arbor_api.delete_webhook(name)
    assert frappe.db.exists("Webhook Endpoint", name)
    _as(ADMIN)
    assert arbor_api.delete_webhook(name)["ok"] is True
    assert not frappe.db.exists("Webhook Endpoint", name)


# --- Notification fan-out (the wired doc_event seam) ------------------------
def test_process_notification_fans_out_to_registered_endpoint(monkeypatch):
    """A process Notification with no source-row sheet FK matches a GLOBAL endpoint
    (sheet=None); a comment Notification matches its sheet-scoped endpoint. We stub
    the outbound POST so no real network call happens — the assertion is that the
    fan-out ENQUEUED a Webhook Delivery row to the endpoint through the wired hook."""
    from arbor.arbor.dispatch import frappe_dispatch

    # Stub the transport so on_notification_insert's first attempt doesn't hit the
    # network; the delivery row + signature are what we assert.
    class _StubResp:
        status_code = 200
        text = "OK"

    monkeypatch.setattr(
        frappe_dispatch.RequestsTransport, "post", lambda self, url, body, headers, timeout: _StubResp()
    )

    _as(ADMIN)
    # A process Notification resolves sheet=None → register a GLOBAL process endpoint.
    ep = arbor_api.register_webhook(
        url="https://8.8.8.8/proc", sheet=None, notification_sources=["process"]
    )
    try:
        n = frappe.new_doc("Notification")
        n.source = "process"
        n.recipient = ADMIN
        n.channel = "in-app"
        n.requires_ack = 0
        n.insert(ignore_permissions=True)  # after_insert → on_notification_insert → fan_out

        deliveries = frappe.get_all(
            "Webhook Delivery",
            filters={"endpoint": ep["name"], "event_id": n.name},
            fields=["name", "source", "notification", "signature"],
        )
        assert len(deliveries) == 1
        d = deliveries[0]
        assert d["source"] == "process"
        assert d["notification"] == n.name
        assert d["signature"]  # signed via the reused engine
    finally:
        _cleanup_endpoint(ep["name"])


def test_tree_event_notification_does_not_fan_out(monkeypatch):
    """A tree_event-sourced Notification is inert in the fan-out lane (the tree-event
    webhook lane owns those deliveries) — no notification-lane delivery is created."""
    from arbor.arbor.dispatch import frappe_dispatch

    monkeypatch.setattr(
        frappe_dispatch.RequestsTransport,
        "post",
        lambda self, url, body, headers, timeout: (_ for _ in ()).throw(AssertionError("no POST")),
    )
    _as(ADMIN)
    ep = arbor_api.register_webhook(
        url="https://8.8.8.8/te", sheet=None, notification_sources=["process"]
    )
    try:
        n = frappe.new_doc("Notification")
        n.source = "tree_event"
        n.recipient = ADMIN
        n.channel = "in-app"
        n.requires_ack = 0
        n.insert(ignore_permissions=True)
        assert not frappe.get_all(
            "Webhook Delivery", filters={"endpoint": ep["name"], "event_id": n.name}, pluck="name"
        )
    finally:
        _cleanup_endpoint(ep["name"])
