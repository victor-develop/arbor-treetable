"""Bench-free fixtures for the agent-lane tests.

The whole agent lane (provider adapter, tools binding, react session) is
unit-testable WITHOUT a Frappe bench: it runs the core ``run_agent`` loop over
the pure ``InMemoryRepository`` + ``RecordingEventSink`` doubles and a scripted
``MockLLMProvider``. Only ``arbor.arbor.agent.chat`` needs a live bench (it
imports frappe + the adapter façade); those cases are documented in
``test_chat_endpoint_bench.py`` and skipped without frappe.
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        path = str(item.fspath).replace("\\", "/")
        if "/agent/" in path and "bench" not in path:
            item.add_marker(pytest.mark.agent)
