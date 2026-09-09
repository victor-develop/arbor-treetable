"""SQL-lane wrapper: re-collect tests/core/test_acl_read_domain.py against the
standalone SQLTestRepository (swapped in by tests/standalone/conftest.py).

These wrappers are hand-written, not auto-collected — a core semantics file
without one is simply never proven on the SQL repository.
"""

from tests.core.test_acl_read_domain import *  # noqa: F401,F403
