"""Bounded, navigable LLM read API (the *explore* surface).

A large tree must NOT be pulled in one shot. ``get_sheet_snapshot`` is only safe
up to :data:`EXPLORE_THRESHOLD` nodes; above that the agent navigates the tree
piece-by-piece through the functions here:

  * :func:`sheet_overview`  — structure only, ALWAYS safe (no per-node cells).
  * :func:`list_children`   — one parent's children, keyset-paginated.
  * :func:`get_subtree`     — a bounded preorder window, node-budget capped.
  * :func:`get_node`        — one node with all its cells + a breadcrumb path.
  * :func:`search_nodes`    — case-insensitive substring search, paginated.
  * :func:`get_cells`       — a sparse node x column matrix, budget-guarded.

Everything here is PURE: it depends only on the :class:`~arbor.core.ports.Repository`
PORT (``get_sheet``, ``list_columns``, ``list_nodes``, ``count_nodes``,
``get_value``) and never imports frappe. All slicing/pagination happens in-core so
the same code is exercised by the in-memory fake and the Frappe adapter alike.

Pagination uses an OPAQUE keyset cursor: a ``(lft, name)`` tuple base64-encoded as
a string. Callers treat ``next_cursor`` as opaque and pass it back verbatim. Keyset
(rather than offset) means a page is stable under concurrent inserts to its left.

Limits are clamped to ``[1, MAX_PAGE]`` and windows are capped at
:data:`NODE_BUDGET`; :func:`get_cells` rejects matrices larger than
:data:`CELL_BUDGET`.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Optional

from .acl import visible_columns
from .ports import Repository
from .types import Actor

# --- bounds -----------------------------------------------------------------
EXPLORE_THRESHOLD = 500
"""Above this node count ``get_sheet_snapshot`` refuses; explore instead."""

MIN_PAGE = 1
MAX_PAGE = 200
"""Page limits are clamped to ``[MIN_PAGE, MAX_PAGE]``."""

NODE_BUDGET = 200
"""Hard cap on the number of nodes any single subtree/search window returns."""

CELL_BUDGET = 1000
"""Max ``len(nodes) * len(columns)`` a single :func:`get_cells` may request."""


# --- typed errors -----------------------------------------------------------
class SheetTooLargeError(Exception):
    """Raised when a whole-sheet read is attempted on a sheet over threshold.

    Carries the offending ``count`` and the ``threshold`` so adapters can surface
    a 4xx (never an unhandled 500). The message names the count + threshold and
    steers the caller to the explore tools.
    """

    #: Names of the bounded read tools the caller should use instead.
    EXPLORE_TOOLS = (
        "getSheetOverview",
        "listChildren",
        "getSubtree",
        "getNode",
        "searchNodes",
        "getCells",
    )

    def __init__(self, count: int, threshold: int = EXPLORE_THRESHOLD) -> None:
        self.count = count
        self.threshold = threshold
        tools = ", ".join(self.EXPLORE_TOOLS)
        super().__init__(
            f"Sheet has {count} nodes, over the {threshold}-node snapshot limit. "
            f"Use the explore tools instead: {tools}. "
            f"Start with getSheetOverview to understand the structure, then "
            f"navigate with listChildren / getSubtree."
        )


class CellBudgetExceededError(Exception):
    """Raised when a :func:`get_cells` matrix exceeds :data:`CELL_BUDGET`.

    Distinct from :class:`SheetTooLargeError` (which is the whole-sheet guard);
    this one is the per-request matrix guard.
    """

    def __init__(self, requested: int, budget: int = CELL_BUDGET) -> None:
        self.requested = requested
        self.budget = budget
        super().__init__(
            f"get_cells requested {requested} cells, over the {budget}-cell budget. "
            f"Request fewer nodes or columns per call."
        )


# --- cursor codec -----------------------------------------------------------
def _encode_cursor(lft: int, name: str) -> str:
    """Encode a keyset position as an opaque base64 token."""
    raw = json.dumps([lft, name], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: Optional[str]) -> Optional[tuple[int, str]]:
    """Decode an opaque cursor back to ``(lft, name)``; ``None`` stays ``None``.

    A malformed cursor raises ``ValueError`` so the adapter can map it to a 4xx.
    """
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        lft, name = json.loads(raw)
        return int(lft), str(name)
    except Exception as exc:  # noqa: BLE001 — normalise to a typed ValueError
        raise ValueError(f"malformed cursor: {cursor!r}") from exc


def _clamp_limit(limit: int) -> int:
    """Clamp a requested page size into ``[MIN_PAGE, MAX_PAGE]``."""
    if limit < MIN_PAGE:
        return MIN_PAGE
    if limit > MAX_PAGE:
        return MAX_PAGE
    return limit


# --- internal helpers -------------------------------------------------------
def _nodes_by_lft(repo: Repository, sheet: str) -> list[Any]:
    """All sheet nodes in stable preorder (NestedSet ``lft`` ascending)."""
    return sorted(repo.list_nodes(sheet), key=lambda n: n.lft)


def _readable_columns(repo: Repository, sheet: str, actor: Actor) -> list[Any]:
    """The sheet's columns the ``actor`` may read (Feature 3 read-ACL filter).

    The ONE filter, shared with the snapshot via ``acl.visible_columns`` so the
    explore surface and the whole-sheet snapshot can never diverge.
    """
    return visible_columns(repo, repo.get_sheet(sheet), actor, repo.list_columns(sheet))


def _label_column(repo: Repository, sheet: str) -> Optional[str]:
    """The name of the sheet's label column, if any.

    The label column is always readable (``can_read_column`` short-circuits on
    ``is_label``), so this does not need the actor.
    """
    for c in repo.list_columns(sheet):
        if c.is_label:
            return c.name
    return None


def _label_of(repo: Repository, node: str, label_col: Optional[str]) -> Optional[Any]:
    """The label value for ``node`` (value of the label column), or ``None``."""
    if label_col is None:
        return None
    return repo.get_value(node, label_col)


def _all_values(repo: Repository, node: str, columns: list[Any]) -> dict[str, Any]:
    """All cells for ``node`` keyed by column name (missing -> ``None``)."""
    return {c.name: repo.get_value(node, c.name) for c in columns}


def _child_count(children_of: dict[Optional[str], list[Any]], node: str) -> int:
    return len(children_of.get(node, []))


def _children_index(nodes: list[Any]) -> dict[Optional[str], list[Any]]:
    """Group nodes by parent, preserving preorder within each sibling group."""
    index: dict[Optional[str], list[Any]] = {}
    for n in nodes:
        index.setdefault(n.parent, []).append(n)
    return index


def _require_node(repo: Repository, sheet: str, node: str) -> Any:
    """Fetch a node, raising ``ValueError`` if it is missing / not in the sheet."""
    try:
        n = repo.get_node(node)
    except KeyError as exc:
        raise ValueError(f"unknown node {node!r}") from exc
    if n is None or n.sheet != sheet:
        raise ValueError(f"node {node!r} not in sheet {sheet!r}")
    return n


def _paginate(items: list[Any], cursor: Optional[tuple[int, str]], limit: int):
    """Keyset-slice ``items`` (already lft-sorted) after ``cursor``.

    Returns ``(window, has_more, next_cursor_token)``. The cursor selects items
    strictly greater than ``(lft, name)``; ``next_cursor`` points at the last
    item of the returned window.
    """
    if cursor is not None:
        c_lft, c_name = cursor
        start = 0
        for i, n in enumerate(items):
            if (n.lft, n.name) > (c_lft, c_name):
                start = i
                break
        else:
            start = len(items)
        items = items[start:]
    window = items[:limit]
    has_more = len(items) > limit
    next_cursor = (
        _encode_cursor(window[-1].lft, window[-1].name) if has_more and window else None
    )
    return window, has_more, next_cursor


# --- size guard -------------------------------------------------------------
def count_nodes(repo: Repository, sheet: str) -> int:
    """Total node count for ``sheet`` (delegates to the repo PORT)."""
    return repo.count_nodes(sheet)


def assert_snapshot_size(repo: Repository, sheet: str) -> None:
    """Guard used by ``get_sheet_snapshot``.

    Raises :class:`SheetTooLargeError` when the sheet exceeds
    :data:`EXPLORE_THRESHOLD`. The boundary (``count == threshold``) is allowed.
    """
    count = count_nodes(repo, sheet)
    if count > EXPLORE_THRESHOLD:
        raise SheetTooLargeError(count, EXPLORE_THRESHOLD)


# --- reads ------------------------------------------------------------------
def sheet_overview(repo: Repository, sheet: str, actor: Actor) -> dict[str, Any]:
    """Structural summary of a sheet — ALWAYS safe (never raises on size).

    Returns name, structural_owner, column metadata (NO cell payload),
    total_nodes, root_node_ids, max_depth, and top_branches (the root's direct
    children with their child counts). Carries no per-node values.

    Columns are filtered to those ``actor`` may read (Feature 3 read-ACL).
    """
    sv = repo.get_sheet(sheet)
    nodes = _nodes_by_lft(repo, sheet)
    children_of = _children_index(nodes)
    label_col = _label_column(repo, sheet)

    columns = [
        {
            "name": c.name,
            "field": c.field,
            "label": getattr(c, "label", "") or c.field,
            "type": getattr(c, "type", "text"),
            "column_owner": c.column_owner,
        }
        for c in _readable_columns(repo, sheet, actor)
    ]

    roots = children_of.get(None, [])
    root_ids = [n.name for n in roots]

    # Depth via a single pass over parent links (root = 0).
    depth_of: dict[str, int] = {}
    max_depth = 0
    for n in nodes:  # preorder -> parents precede children
        d = 0 if n.parent is None else depth_of.get(n.parent, 0) + 1
        depth_of[n.name] = d
        if d > max_depth:
            max_depth = d

    # Top branches are the direct children of the root(s) — the first level the
    # caller would drill into. (For a single-root sheet these are the root's
    # children; for a forest, every root's children.)
    top_nodes: list[Any] = []
    for r in roots:
        top_nodes.extend(children_of.get(r.name, []))
    top_branches = [
        {
            "node": b.name,
            "label": _label_of(repo, b.name, label_col),
            "child_count": _child_count(children_of, b.name),
        }
        for b in top_nodes
    ]

    return {
        "name": sv.name,
        "structural_owner": sv.structural_owner,
        "columns": columns,
        "total_nodes": len(nodes),
        "root_node_ids": root_ids,
        "max_depth": max_depth,
        "top_branches": top_branches,
    }


def list_children(
    repo: Repository,
    sheet: str,
    parent: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 50,
    *,
    actor: Actor,
) -> dict[str, Any]:
    """One parent's direct children, keyset-paginated.

    ``parent=None`` lists the sheet's roots. Each node carries all its readable
    cells and its own child_count. ``child_count`` (top level) is the TOTAL
    number of children regardless of paging. Limit clamped to ``[1, 200]``.
    Cells are filtered to the columns ``actor`` may read (Feature 3).
    """
    limit = _clamp_limit(limit)
    decoded = _decode_cursor(cursor)
    nodes = _nodes_by_lft(repo, sheet)
    children_of = _children_index(nodes)
    columns = _readable_columns(repo, sheet, actor)
    label_col = _label_column(repo, sheet)

    if parent is not None:
        _require_node(repo, sheet, parent)

    siblings = children_of.get(parent, [])
    total = len(siblings)
    window, has_more, next_cursor = _paginate(siblings, decoded, limit)

    rows = [
        {
            "name": n.name,
            "parent": n.parent,
            "label": _label_of(repo, n.name, label_col),
            "values": _all_values(repo, n.name, columns),
            "child_count": _child_count(children_of, n.name),
        }
        for n in window
    ]

    return {
        "nodes": rows,
        "child_count": total,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


def get_subtree(
    repo: Repository,
    sheet: str,
    node: str,
    depth: int = 1,
    cursor: Optional[str] = None,
    limit: int = 50,
    *,
    actor: Actor,
) -> dict[str, Any]:
    """A bounded preorder window of ``node``'s subtree, to ``depth`` levels.

    The window starts at ``node`` (or resumes after ``cursor``) and walks the
    subtree in preorder, including descendants no deeper than ``depth`` levels
    below ``node``. It is capped at ``min(limit, NODE_BUDGET)`` nodes; when the
    matching set exceeds the cap the result is clipped with ``has_more=True`` and
    a ``next_cursor`` to resume. Raises ``ValueError`` for an unknown node.
    Cells are filtered to the columns ``actor`` may read (Feature 3).
    """
    root = _require_node(repo, sheet, node)
    cap = min(_clamp_limit(limit), NODE_BUDGET)
    decoded = _decode_cursor(cursor)

    nodes = _nodes_by_lft(repo, sheet)
    children_of = _children_index(nodes)
    columns = _readable_columns(repo, sheet, actor)
    label_col = _label_column(repo, sheet)

    # Preorder walk of the subtree, bounded by depth. ``node`` itself is level 0.
    matched: list[Any] = []
    stack: list[tuple[Any, int]] = [(root, 0)]
    while stack:
        cur, level = stack.pop()
        matched.append(cur)
        if level < depth:
            kids = children_of.get(cur.name, [])
            # push reversed so preorder pops left-to-right
            for child in reversed(kids):
                stack.append((child, level + 1))

    # ``matched`` is preorder; keyset-paginate over it by (lft, name).
    window, has_more, next_cursor = _paginate(matched, decoded, cap)

    rows = [
        {
            "name": n.name,
            "parent": n.parent,
            "label": _label_of(repo, n.name, label_col),
            "values": _all_values(repo, n.name, columns),
            "child_count": _child_count(children_of, n.name),
        }
        for n in window
    ]

    return {
        "nodes": rows,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


def get_node(repo: Repository, sheet: str, node: str, *, actor: Actor) -> dict[str, Any]:
    """One node with all its READABLE cells, child_count, and a root..node breadcrumb.

    ``path`` is a list of ``{"name", "label"}`` from the root down to ``node``
    inclusive. Raises ``ValueError`` for an unknown node. Cells are filtered to
    the columns ``actor`` may read (Feature 3).
    """
    n = _require_node(repo, sheet, node)
    nodes = _nodes_by_lft(repo, sheet)
    children_of = _children_index(nodes)
    columns = _readable_columns(repo, sheet, actor)
    label_col = _label_column(repo, sheet)

    # Breadcrumb: ancestors_self is nearest-first, so reverse to root..node.
    chain = list(repo.ancestors_self(node))
    chain.reverse()
    path = [
        {"name": a.name, "label": _label_of(repo, a.name, label_col)} for a in chain
    ]

    return {
        "name": n.name,
        "parent": n.parent,
        "label": _label_of(repo, n.name, label_col),
        "values": _all_values(repo, n.name, columns),
        "child_count": _child_count(children_of, n.name),
        "path": path,
    }


def search_nodes(
    repo: Repository,
    sheet: str,
    query: str,
    column: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 50,
    *,
    actor: Actor,
) -> dict[str, Any]:
    """Case-insensitive substring search, keyset-paginated.

    ``column=None`` searches the label and every READABLE cell value; otherwise
    the search is scoped to the given column's value only (a label-only match is
    excluded). A column the ``actor`` cannot read is NEVER searched and never
    matches (no value-existence leak): an all-columns search ignores forbidden
    cells, and a search explicitly scoped to a forbidden column returns nothing.
    Results are returned in preorder; limit clamped to ``[1, 200]``.
    """
    limit = _clamp_limit(limit)
    decoded = _decode_cursor(cursor)
    needle = (query or "").lower()

    nodes = _nodes_by_lft(repo, sheet)
    children_of = _children_index(nodes)
    columns = _readable_columns(repo, sheet, actor)
    label_col = _label_column(repo, sheet)
    readable_names = {c.name for c in columns}

    def hit(n: Any) -> bool:
        if column is not None:
            # Scoping to a forbidden column must not leak its values.
            if column not in readable_names:
                return False
            val = repo.get_value(n.name, column)
            return val is not None and needle in str(val).lower()
        # all-columns: label + every cell value
        lbl = _label_of(repo, n.name, label_col)
        if lbl is not None and needle in str(lbl).lower():
            return True
        for c in columns:
            val = repo.get_value(n.name, c.name)
            if val is not None and needle in str(val).lower():
                return True
        return False

    matched = [n for n in nodes if hit(n)]
    window, has_more, next_cursor = _paginate(matched, decoded, limit)

    rows = [
        {
            "name": n.name,
            "parent": n.parent,
            "label": _label_of(repo, n.name, label_col),
            "values": _all_values(repo, n.name, columns),
            "child_count": _child_count(children_of, n.name),
        }
        for n in window
    ]

    return {
        "nodes": rows,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


def get_cells(
    repo: Repository,
    sheet: str,
    nodes: list[str],
    columns: list[str],
    *,
    actor: Actor,
) -> dict[str, Any]:
    """A sparse ``node x column`` value matrix.

    Returns ``{"cells": {node: {column: value}}}`` with missing cells as ``None``.
    Raises :class:`CellBudgetExceededError` when ``len(nodes) * len(columns)``
    exceeds :data:`CELL_BUDGET`.

    Requested columns the ``actor`` cannot read (Feature 3) are silently OMITTED
    — treated as nonexistent rather than raising — so a caller cannot probe a
    column's existence. The budget is checked against the REQUESTED size before
    filtering (the request itself is what is bounded).
    """
    requested = len(nodes) * len(columns)
    if requested > CELL_BUDGET:
        raise CellBudgetExceededError(requested, CELL_BUDGET)

    readable_names = {c.name for c in _readable_columns(repo, sheet, actor)}

    # de-duplicate columns while preserving order (the matrix is keyed by name),
    # dropping any the actor cannot read (existence-hidden, not an error).
    seen: set[str] = set()
    ordered_cols = [
        c
        for c in columns
        if c in readable_names and not (c in seen or seen.add(c))
    ]

    cells: dict[str, dict[str, Any]] = {}
    for node in nodes:
        cells[node] = {c: repo.get_value(node, c) for c in ordered_cols}

    return {"cells": cells}
