"""Public re-export shim: ``arbor.adapter`` -> ``arbor.arbor.adapter``.

The real Frappe adapter (FrappeRepository / FrappeEventSink / canonical seed)
lives at ``arbor.arbor.adapter`` (the app module path on a bench). Several
documented bind points and the backend test harness use the collapsed
``arbor.adapter`` path. This shim forwards attribute access lazily so importing
``arbor.adapter`` (or the frappe-free ``arbor.adapter.canonical_spec``) does NOT
pull in frappe on a bench-free checkout — mirroring the real package's PEP 562
laziness. No adapter logic is duplicated.
"""

from __future__ import annotations

from typing import Any

__all__ = ["FrappeRepository", "FrappeEventSink", "ConflictError"]


def __getattr__(name: str) -> Any:  # PEP 562 lazy attribute access
    from arbor.arbor import adapter as _real

    return getattr(_real, name)
