"""Bench-free fixtures for the dispatch-lane tests.

The notification + webhook dispatchers are unit/integration-testable WITHOUT a
Frappe bench: they run the pure ``arbor.arbor.dispatch`` dispatchers over the
in-memory store + freezable clock + programmable transport doubles
(``arbor.arbor.dispatch.testing``). The Frappe binding
(``arbor.arbor.dispatch.frappe_dispatch``) is exercised only on a real bench;
those paths are out of scope for this bench-free suite.

Auto-applies the ``dispatch`` marker so the whole suite is selectable with
``-m dispatch`` and is unambiguously bench-free.
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "/dispatch/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.dispatch)
