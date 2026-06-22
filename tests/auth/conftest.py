"""Make the Arbor app importable WITHOUT a Frappe bench.

The auth seam lives in the Frappe adapter (``arbor/arbor/auth``). It imports
``frappe`` only LAZILY (inside methods), so the seam's pure logic — provider
resolution, identity mapping, AuthResult helpers — is unit-testable here with
plain pytest.

These tests are marked ``auth`` and do NOT require a bench. Tests that exercise
the frappe-bound code paths (``ensure_user`` / session login / JWT verification
networking) are documented as bench-required and skipped here. The employee SSO
overlay (and its conformance tests) was split into the ``arbor-sso-overlay``
repo, so nothing here references it.
"""

from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Repo root makes ``arbor.auth`` (at arbor/auth, mirroring arbor.core) importable;
# it imports frappe only lazily, so the seam's pure logic is testable without a bench.
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "/auth/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.auth)
