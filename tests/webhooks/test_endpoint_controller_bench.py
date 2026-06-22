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


def _new_endpoint(event_types):
    doc = frappe.new_doc("Webhook Endpoint")
    doc.url = "http://example.test/hook"
    doc.scope = "sheet"
    doc.target = "S" if frappe.db.exists("Tree Sheet", "S") else None
    # event_types is a JSON column: store the encoded string (the dispatcher parses it)
    doc.event_types = json.dumps(event_types)
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
