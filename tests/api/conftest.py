"""Local pytest config for the REST API lane.

Two tiers live here:

* **bench-free** (``test_parity_harness.py``) — exercises the cross-surface
  parity guarantee (TEST-PLAN §5.4) and the registry→REST reachability contract
  by driving the ONE pure ``arbor.core.executor.execute_action`` and the agent
  tool-executor over the in-memory doubles. No frappe, no site.
* **bench** (``test_rest_parity_bench.py``) — drives the real whitelisted
  ``arbor.api`` methods (FrappeRepository / FrappeEventSink) on a live site;
  auto-skipped when frappe is not importable.

Registers the ``bench`` marker (the shared ``pyproject.toml`` is a manifest this
lane must not edit) and auto-skips ``bench``-marked tests on a bench-free
checkout, mirroring ``tests/adapter/conftest.py``.
"""

from __future__ import annotations

import importlib.util

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "bench: requires a Frappe bench + site (skipped when frappe is absent)",
    )
    config.addinivalue_line(
        "markers",
        "parity: cross-surface parity (execute_action vs REST vs agent tool-call)",
    )


def pytest_collection_modifyitems(config, items):
    has_frappe = importlib.util.find_spec("frappe") is not None
    skip_bench = pytest.mark.skip(reason="frappe not importable; bench test")
    for item in items:
        if "bench" in item.keywords and not has_frappe:
            item.add_marker(skip_bench)
