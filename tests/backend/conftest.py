"""Local pytest config + shared fixtures for the backend integration lane.

NEEDS A FRAPPE BENCH + SITE. Every test module under ``tests/backend`` is marked
``@pytest.mark.bench`` and exercises the REAL adapter (``FrappeRepository`` /
``FrappeEventSink``) through the whitelisted REST funnel in :mod:`arbor.api`,
plus the real notification dispatcher (:mod:`arbor.dispatch.frappe_dispatch`).

Mirrors ``tests/adapter/conftest.py``: registers the ``bench`` marker (the shared
``pyproject.toml`` is a manifest this lane must not edit) and auto-skips
``bench``-marked tests when frappe is not importable, so a bench-free checkout can
still run ``pytest`` over the repo without import errors.

Run on a bench::

    bench --site <site> run-tests --module tests.backend.test_permissions_acl
    bench --site <site> run-tests --module tests.backend.test_change_request_lifecycle
    bench --site <site> run-tests --module tests.backend.test_notifications_ack

or, from a bench-activated venv, simply::

    pytest tests/backend -m bench

The dispatcher is wired in production by a ``doc_events["Tree Event"]["after_insert"]``
hook (see the manifest this lane returns). To stay independent of whether the
integrator has assembled that hook into ``hooks.py`` yet, the helpers below drive
the dispatcher explicitly via :func:`dispatch_pending_events`, which feeds every
not-yet-dispatched Tree Event row to ``on_tree_event_insert``. The helper is
idempotent per ``(tree_event, recipient, channel)`` (the dispatcher dedups), so
running it even when a real hook is also active produces no duplicate rows.
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

# The frappe session + per-test rollback harness lives in the shared root
# ``tests/conftest.py`` so every bench lane (api/agent/webhooks/backend/adapter)
# reuses ONE definition (DRY).
