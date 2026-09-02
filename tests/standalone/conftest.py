"""Pytest config for the standalone (SQL) lane.

The ``tests/standalone/sqlcore`` modules re-collect the whole ``tests/core``
suite (one star-import wrapper per file). The autouse fixture below swaps
``InMemoryRepository`` for ``SQLTestRepository`` in EVERY module that imported
it — the core tests instantiate the repo at call time (fixtures / function
bodies), so the runtime rebind alone re-points the entire suite at the SQL
adapter with zero edits to tests/core. Each ``SQLTestRepository()`` owns a
fresh sqlite in-memory engine, so per-test isolation matches a fresh
in-memory repo.

Everything collected here gets the ``standalone`` marker (the ``core`` marker
stays reserved for the pure in-memory lane; this directory's path deliberately
avoids ``/core/`` so tests/conftest.py does not tag it).
"""

from __future__ import annotations

import sys

import pytest
from arbor.core import testing as _core_testing
from arbor.standalone.testing import SQLTestRepository


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "/standalone/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.standalone)


@pytest.fixture(autouse=True)
def _sql_repository(monkeypatch):
    """Rebind ``InMemoryRepository`` -> ``SQLTestRepository`` everywhere.

    Scans sys.modules for any module holding a reference to the REAL in-memory
    class (the canonical fixture, every test module that imported it, and
    ``arbor.core.testing`` itself) and monkeypatches that attribute for the
    duration of the test. All modules are already imported by collection time,
    and monkeypatch restores the originals after each test, so the tests/core
    lane in the same run is unaffected."""
    original = _core_testing.InMemoryRepository
    for mod in list(sys.modules.values()):
        if mod is not None and getattr(mod, "InMemoryRepository", None) is original:
            monkeypatch.setattr(mod, "InMemoryRepository", SQLTestRepository)
    yield
