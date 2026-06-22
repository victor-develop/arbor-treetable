"""Shared pytest config for the bench-free core suite.

Auto-applies the ``core`` marker to everything under ``tests/core`` so the whole
pure suite is selectable with ``-m core`` and is unambiguously bench-free.
"""

from __future__ import annotations

import importlib.util
import os

import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "/core/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.core)


# ---------------------------------------------------------------------------
# Shared operator harness for the bench tiers (api / agent / webhooks / backend /
# adapter). Makes the pytest-style ``@pytest.mark.bench`` tests runnable directly
# with pytest — frappe's unittest runner cannot collect pytest fixtures. Activates
# ONLY when frappe is importable, so the bench-free venv is unaffected. Establishes
# a connected site once and rolls every test back in a transaction.
# ---------------------------------------------------------------------------
if importlib.util.find_spec("frappe") is not None:
    import frappe

    _SITE = os.environ.get("ARBOR_TEST_SITE", "arbor.test")
    _SITES_PATH = os.environ.get(
        "ARBOR_SITES_PATH", os.path.expanduser("~/frappe-bench/sites")
    )

    @pytest.fixture(scope="session", autouse=True)
    def _frappe_session():
        frappe.init(site=_SITE, sites_path=_SITES_PATH)
        frappe.connect()
        frappe.flags.in_test = True
        frappe.flags.mute_emails = True
        try:
            yield
        finally:
            frappe.destroy()

    @pytest.fixture(autouse=True)
    def _frappe_rollback():
        frappe.db.begin()
        try:
            yield
        finally:
            frappe.db.rollback()
            frappe.set_user("Administrator")
