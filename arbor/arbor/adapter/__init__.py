"""The Frappe ADAPTER for Arbor.

Implements the pure core's ports (``arbor.core.ports``) over the Frappe ORM +
NestedSet:

- :class:`~arbor.arbor.adapter.repository.FrappeRepository` — the data seam.
- :class:`~arbor.arbor.adapter.repository.FrappeEventSink` — the event seam
  (writes ``Tree Event`` rows; the ONLY way an event is recorded).

The whitelisted REST surface lives in :mod:`arbor.api`; the canonical fixture
builder for a real bench lives in :mod:`arbor.adapter.seed`; the frappe-free
seed spec (importable anywhere) lives in :mod:`arbor.adapter.canonical_spec`.

The repository/event-sink modules import ``frappe``; they are exposed lazily via
``__getattr__`` so importing the package (or the frappe-free ``canonical_spec``)
does NOT pull in frappe on a bench-free checkout.
"""

from __future__ import annotations

from typing import Any

__all__ = ["FrappeRepository", "FrappeEventSink"]


def __getattr__(name: str) -> Any:  # PEP 562 lazy attribute access
    if name in __all__:
        from .repository import FrappeEventSink, FrappeRepository

        return {"FrappeRepository": FrappeRepository, "FrappeEventSink": FrappeEventSink}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
