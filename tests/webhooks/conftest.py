"""Pytest config for the WEBHOOKS lane.

Auto-applies the ``webhooks`` marker to everything under ``tests/webhooks`` so the
whole suite is selectable with ``-m webhooks``. The suite is bench-free: it drives
the pure ``arbor.arbor.dispatch`` WebhookDispatcher and the pure
``arbor.core.executor`` over in-memory doubles + the canonical seed, and exercises
the real outbound HTTP path against a loopback :class:`~tests.webhooks.harness.
LocalHTTPReceiver` (a real socket bound to 127.0.0.1, NOT a live external host).
No Frappe site is required.
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "/webhooks/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.webhooks)
