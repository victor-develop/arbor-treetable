"""Standalone-adapter integrity / concurrency errors.

The exact analog of ``arbor.arbor.adapter.repository``'s error family, minus
frappe: the API layer maps ``ConflictError`` (and subclasses) to HTTP 409 and
``NotFoundError`` to 404. They are storage-level conflicts, NOT core domain
errors — the pure core keeps its own ``types.StaleVersionError`` for the
in-memory double; the api seam catches BOTH families at the boundary.

``NotFoundError`` subclasses ``KeyError`` deliberately: the core's read paths
(``explore._require_node`` etc.) already catch ``KeyError`` from the in-memory
double, so a standalone miss flows through the same branches unchanged.
"""

from __future__ import annotations

from typing import Any


class NotFoundError(KeyError):
    """A referenced row does not exist → HTTP 404 (KeyError-compatible so the
    core's existing ``except KeyError`` read guards keep working)."""


class ConflictError(Exception):
    """A storage-level integrity/concurrency conflict → HTTP 409."""


class StaleMoveError(ConflictError):
    """The caller's positional revision for a move is stale (api.md API-160)."""


class CycleError(ConflictError):
    """A move would put a node under its own descendant (api.md API-150)."""


class StaleVersionError(ConflictError):
    """Optimistic-concurrency: stored cell version != expected (api.md API-161).

    Carries ``current_version`` and ``current_value`` (the authoritative stored
    state) so the API seam can build the VERSION_CONFLICT payload without a
    second read."""

    def __init__(
        self,
        message: str = "",
        *,
        current_version: int = 0,
        current_value: Any = None,
    ) -> None:
        super().__init__(message)
        self.current_version = current_version
        self.current_value = current_value
