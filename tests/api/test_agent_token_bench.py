"""Bench regression: the external-agent scope gate must be inert OUTSIDE an HTTP
request (bench, scheduler, background jobs, the in-process agent lane).

Guards a real bug: ``frappe.request`` is a werkzeug LocalProxy that is never None
(reading it unbound raises ``RuntimeError("object is not bound")``), so a naive
``getattr(frappe, "request", None) is None`` guard never fires and the header read
crashes every non-request dispatch. The gate must instead check
``frappe.local.request`` and no-op when it is unbound.

Auto-skipped when frappe is absent (bench-free checkout) via the ``bench`` marker.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.bench


def test_agent_scope_is_none_outside_request_context():
    import frappe

    from arbor.arbor.api import _agent_scope_from_request

    # No HTTP request is bound in a bench/test context.
    assert getattr(frappe.local, "request", None) is None
    # Must return None (no token), NOT raise RuntimeError on the header read.
    assert _agent_scope_from_request() is None


def test_enforce_agent_scope_is_a_noop_outside_request_context():
    from arbor.arbor.api import _enforce_agent_scope

    # A mutating capability would be blocked by a read/sheet-scoped token, but with
    # no request (hence no token) the gate must pass through without raising.
    _enforce_agent_scope("updateCell", {"sheet": "any", "node": "n", "column": "c", "value": 1})
