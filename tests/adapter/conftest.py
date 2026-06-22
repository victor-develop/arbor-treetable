"""Local pytest config for the adapter lane.

Registers the ``bench`` marker (the shared ``pyproject.toml`` is a manifest this
lane must not edit) and auto-skips ``bench``-marked tests when frappe is not
importable, so ``pytest tests/adapter`` is safe on a bench-free checkout.
"""

from __future__ import annotations

import importlib.util

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "bench: requires a Frappe bench + site (skipped when frappe is absent)",
    )


def pytest_collection_modifyitems(config, items):
    has_frappe = importlib.util.find_spec("frappe") is not None
    if has_frappe:
        return
    skip_bench = pytest.mark.skip(reason="frappe not importable; bench test")
    for item in items:
        if "bench" in item.keywords:
            item.add_marker(skip_bench)
