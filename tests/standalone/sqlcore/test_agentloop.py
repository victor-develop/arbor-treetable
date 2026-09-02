"""SQL-lane wrapper: re-collect tests/core/test_agentloop.py against the standalone
SQLTestRepository (swapped in by tests/standalone/conftest.py)."""

from tests.core.test_agentloop import *  # noqa: F401,F403
