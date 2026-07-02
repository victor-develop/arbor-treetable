"""Pure DAG validation for the process rule model (process DAG).

ZERO frappe. A process is a SET of trigger->expectation rules; the rules compose
into a directed graph because a column EXPECTED by one rule may be the
``trigger_column`` of another. This module is the SERVER authority for rejecting
an invalid rule set (the client ``frontend/src/canvas/graph.ts`` mirrors it):

  * self-loop   — a rule whose ``trigger_column`` is also one of its own
                  ``expected_columns`` (a column that triggers itself).
  * duplicate   — two edges with the same (from, to) pair (a repeated
                  trigger->expected dependency).
  * cycle       — a dependency cycle among column nodes (detected by a
                  topological sort / DFS over the derived edge set).
  * unreachable — a column node not reachable from START (a 'row' trigger)
                  through the edge set — a WARNING, not a hard error (the rule
                  set is still acyclic + runnable).

The graph is derived from ``rules``:
  * a ``trigger_kind='row'`` rule contributes edges  START -> expected_col  (one
    per expected column). START is the synthetic source node.
  * a ``trigger_kind='column'`` rule contributes edges
    ``trigger_column`` -> expected_col  (one per expected column).

``validate_rules`` returns a ``GraphValidation`` (errors + warnings). The caller
(``handlers.define_process_handler``) raises a ``ValidationError`` when
``errors`` is non-empty; unreachable columns are surfaced as warnings only.

Each ``rule`` is a plain mapping (the ``defineProcess`` payload shape):
``{rule_key?, trigger_kind, trigger_column?, trigger_op?, expected_columns:[...],
within_seconds?, notify_on_expect?, label?}``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

#: The synthetic source node every 'row' trigger flows from. Not a real column,
#: so it can never collide with a Tree Column name (columns are doc names).
START = "__START__"


class ValidationError(Exception):
    """Raised (by the handler) when a rule set fails DAG validation. Carries the
    machine-readable ``errors`` list so the API surface can render a 400 body."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__("; ".join(e.get("message", e.get("code", "invalid")) for e in errors))


@dataclass
class GraphValidation:
    """The pure validation verdict. ``ok`` iff there are no hard ``errors``;
    ``warnings`` (e.g. unreachable columns) never block a define."""

    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class Edge:
    """One derived dependency edge ``from_node`` -> ``to`` carrying the owning
    rule + its window. ``from_kind`` is 'row' (START) or 'column'."""

    rule_key: str
    from_kind: str  # 'row' | 'column'
    from_node: str  # START for a row trigger, else the trigger_column
    to: str  # an expected column


def _rule_key(rule: dict[str, Any], idx: int) -> str:
    key = rule.get("rule_key")
    return key if key else f"r{idx}"


def trigger_columns_of(rule: Any) -> list[str]:
    """The trigger SET of a rule, normalizing the back-compat single
    ``trigger_column`` alias to ``[x]``. Works on both mapping payloads and view
    objects (attribute access). Empty when neither is set."""
    if isinstance(rule, dict):
        cols = rule.get("trigger_columns")
        single = rule.get("trigger_column")
    else:
        cols = getattr(rule, "trigger_columns", None)
        single = getattr(rule, "trigger_column", None)
    if cols:
        return [c for c in cols if c is not None]
    if single is not None:
        return [single]
    return []


def build_edges(rules: Iterable[dict[str, Any]]) -> list[Edge]:
    """Derive the ordered edge set from ``rules`` (pure). A 'row' rule fans out
    from START; a 'column' rule fans out from EACH of its trigger columns (an
    AND-join contributes one edge per (trigger_column -> expected_column)). Each
    expected column is one edge (so an 'and' rule of N expected columns is N
    edges sharing a ``rule_key``)."""
    edges: list[Edge] = []
    for i, rule in enumerate(rules):
        kind = rule.get("trigger_kind")
        rk = _rule_key(rule, i)
        if kind == "row":
            for col in rule.get("expected_columns") or []:
                edges.append(Edge(rule_key=rk, from_kind="row", from_node=START, to=col))
        else:
            for src in trigger_columns_of(rule):
                for col in rule.get("expected_columns") or []:
                    edges.append(
                        Edge(rule_key=rk, from_kind="column", from_node=src, to=col)
                    )
    return edges


def _adjacency(edges: list[Edge]) -> dict[str, list[str]]:
    adj: dict[str, list[str]] = {}
    for e in edges:
        adj.setdefault(e.from_node, []).append(e.to)
        adj.setdefault(e.to, adj.get(e.to, []))
    return adj


def find_cycle(edges: list[Edge]) -> Optional[list[str]]:
    """Return a node cycle (as a list of node names, closing back on the first)
    if the edge set is cyclic, else None. Kahn's topological sort: whatever nodes
    remain with a non-zero in-degree after the sort participate in a cycle; a DFS
    over the residual graph reconstructs one concrete cycle for the message."""
    adj = _adjacency(edges)
    indeg: dict[str, int] = {n: 0 for n in adj}
    for e in edges:
        indeg[e.to] = indeg.get(e.to, 0) + 1
        indeg.setdefault(e.from_node, indeg.get(e.from_node, 0))
    # Kahn: repeatedly strip zero-in-degree nodes.
    queue = [n for n, d in indeg.items() if d == 0]
    removed = 0
    indeg2 = dict(indeg)
    while queue:
        n = queue.pop()
        removed += 1
        for m in adj.get(n, []):
            indeg2[m] -= 1
            if indeg2[m] == 0:
                queue.append(m)
    if removed == len(indeg2):
        return None  # acyclic
    # Residual graph (nodes still with in-degree > 0) contains a cycle; DFS it.
    residual = {n for n, d in indeg2.items() if d > 0}
    stack: list[str] = []
    on_stack: set[str] = set()
    visited: set[str] = set()

    def dfs(n: str) -> Optional[list[str]]:
        visited.add(n)
        stack.append(n)
        on_stack.add(n)
        for m in adj.get(n, []):
            if m not in residual:
                continue
            if m in on_stack:
                # found a back-edge: slice the cycle out of the stack.
                i = stack.index(m)
                return stack[i:] + [m]
            if m not in visited:
                found = dfs(m)
                if found is not None:
                    return found
        stack.pop()
        on_stack.discard(n)
        return None

    for start in residual:
        if start not in visited:
            found = dfs(start)
            if found is not None:
                return found
    return None


def reachable_from_start(edges: list[Edge]) -> set[str]:
    """The set of column nodes reachable from START through the edge set (BFS).
    START itself is excluded from the returned set (it is synthetic)."""
    adj = _adjacency(edges)
    seen: set[str] = set()
    frontier = [START]
    while frontier:
        n = frontier.pop()
        for m in adj.get(n, []):
            if m not in seen:
                seen.add(m)
                frontier.append(m)
    return seen


def all_columns(edges: list[Edge]) -> set[str]:
    """Every column node named by the edge set (excludes synthetic START)."""
    cols: set[str] = set()
    for e in edges:
        if e.from_node not in (START, None):
            cols.add(e.from_node)
        if e.to is not None:
            cols.add(e.to)
    return cols


def would_create_cycle(rules: Iterable[dict[str, Any]], new_rule: dict[str, Any]) -> bool:
    """Client/preview helper: whether appending ``new_rule`` to ``rules`` makes
    the derived graph cyclic (mirrors the canvas ``wouldCreateCycle``)."""
    combined = list(rules) + [new_rule]
    return find_cycle(build_edges(combined)) is not None


def validate_rules(rules: list[dict[str, Any]]) -> GraphValidation:
    """The pure validation authority. Collects hard ERRORS (self-loop, duplicate
    edge, cycle, and structural required-field violations) and soft WARNINGS
    (columns unreachable from START). Never raises — the caller decides."""
    result = GraphValidation()

    # --- per-rule structural checks + self-loop -----------------------------
    for i, rule in enumerate(rules):
        rk = _rule_key(rule, i)
        kind = rule.get("trigger_kind")
        expected = list(rule.get("expected_columns") or [])
        triggers = trigger_columns_of(rule)
        join = rule.get("trigger_join") or "any"
        if kind not in ("row", "column"):
            result.errors.append(
                {"code": "bad-trigger-kind", "rule_key": rk,
                 "message": f"rule {rk!r} has invalid trigger_kind {kind!r}"}
            )
        if kind == "column" and join not in ("any", "all"):
            result.errors.append(
                {"code": "bad-trigger-join", "rule_key": rk,
                 "message": f"rule {rk!r} has invalid trigger_join {join!r}"}
            )
        if kind == "column" and not triggers:
            result.errors.append(
                {"code": "missing-trigger-column", "rule_key": rk,
                 "message": f"column-trigger rule {rk!r} has no trigger columns"}
            )
        if kind == "row" and (rule.get("trigger_column") or rule.get("trigger_columns")):
            result.errors.append(
                {"code": "row-trigger-has-column", "rule_key": rk,
                 "message": f"row-trigger rule {rk!r} must not set trigger columns"}
            )
        if not expected:
            result.errors.append(
                {"code": "no-expected-columns", "rule_key": rk,
                 "message": f"rule {rk!r} expects no columns"}
            )
        # self-loop: a trigger column that is also one of its own expected columns.
        if kind == "column":
            for tcol in triggers:
                if tcol in expected:
                    result.errors.append(
                        {"code": "self-loop", "rule_key": rk, "column": tcol,
                         "message": f"rule {rk!r}: column {tcol!r} triggers itself"}
                    )
        # a rule expecting the same column twice is a degenerate duplicate.
        if len(expected) != len(set(expected)):
            dup = next(c for c in expected if expected.count(c) > 1)
            result.errors.append(
                {"code": "duplicate-expected", "rule_key": rk, "column": dup,
                 "message": f"rule {rk!r} expects column {dup!r} more than once"}
            )

    edges = build_edges(rules)

    # --- duplicate edge (same from->to across the whole set) ----------------
    seen_pairs: dict[tuple[str, str], str] = {}
    for e in edges:
        pair = (e.from_node, e.to)
        if pair in seen_pairs:
            result.errors.append(
                {"code": "duplicate-edge", "from": e.from_node, "to": e.to,
                 "message": f"duplicate dependency {e.from_node!r} -> {e.to!r}"}
            )
        else:
            seen_pairs[pair] = e.rule_key

    # --- cycle --------------------------------------------------------------
    cycle = find_cycle(edges)
    if cycle is not None:
        result.errors.append(
            {"code": "cycle", "cycle": cycle,
             "message": "rule set has a dependency cycle: " + " -> ".join(cycle)}
        )

    # --- reachability WARNING (never blocks) --------------------------------
    if not cycle:  # a cyclic graph's reachability is meaningless; skip.
        reachable = reachable_from_start(edges)
        for col in sorted(all_columns(edges) - reachable):
            result.warnings.append(
                {"code": "unreachable", "column": col,
                 "message": f"column {col!r} is not reachable from START"}
            )
    return result
