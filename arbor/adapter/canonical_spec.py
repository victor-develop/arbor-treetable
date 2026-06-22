"""Public re-export shim: ``arbor.adapter.canonical_spec`` -> the real module.

The frappe-free canonical seed spec (importable anywhere, the single source of
truth proven == the pure fixture by ``tests/adapter/test_seed_parity.py``). This
shim only re-exports so the collapsed path resolves; no spec is duplicated.
"""

from __future__ import annotations

from arbor.arbor.adapter.canonical_spec import *  # noqa: F401,F403
