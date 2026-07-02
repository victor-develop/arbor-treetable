"""Pure DAG validation for the process rule model — exhaustive, bench-free.

Covers ``arbor.core.process_graph``: edge derivation from rules, self-loop /
duplicate-edge / cycle rejection, reachability-from-START warning, and the
``would_create_cycle`` preview helper. Mirrors the client ``graph.ts`` contract.
"""

from __future__ import annotations

from arbor.core.process_graph import (
    START,
    Edge,
    build_edges,
    find_cycle,
    reachable_from_start,
    validate_rules,
    would_create_cycle,
)


def _row(rk, cols):
    return {"rule_key": rk, "trigger_kind": "row", "trigger_op": "created",
            "expected_columns": list(cols)}


def _col(rk, trigger, cols):
    return {"rule_key": rk, "trigger_kind": "column", "trigger_column": trigger,
            "trigger_op": "updated", "expected_columns": list(cols)}


def _codes(verdict):
    return {e["code"] for e in verdict.errors}


# ---------------------------------------------------------------------------
# Edge derivation
# ---------------------------------------------------------------------------
def test_row_rule_edges_flow_from_start():
    edges = build_edges([_row("start", ["colA", "colB"])])
    assert Edge("start", "row", START, "colA") in edges
    assert Edge("start", "row", START, "colB") in edges


def test_column_rule_edges_flow_from_trigger_column():
    edges = build_edges([_col("ab", "colA", ["colB"])])
    assert edges == [Edge("ab", "column", "colA", "colB")]


def test_and_rule_becomes_n_edges_sharing_rule_key():
    edges = build_edges([_col("fan", "colA", ["colB", "colC"])])
    assert {(e.from_node, e.to) for e in edges} == {("colA", "colB"), ("colA", "colC")}
    assert all(e.rule_key == "fan" for e in edges)


# ---------------------------------------------------------------------------
# Valid DAGs pass
# ---------------------------------------------------------------------------
def test_valid_chain_passes_no_errors_no_warnings():
    rules = [_row("start", ["colA"]), _col("ab", "colA", ["colB"]), _col("bc", "colB", ["colC"])]
    v = validate_rules(rules)
    assert v.ok and v.errors == [] and v.warnings == []


def test_valid_fanout_and_fanin_pass():
    fanout = [_row("s", ["colA"]), _col("f", "colA", ["colB", "colC"])]
    assert validate_rules(fanout).ok
    fanin = [_row("s", ["colA", "colB"]), _col("ac", "colA", ["colC"]), _col("bc", "colB", ["colC"])]
    assert validate_rules(fanin).ok


# ---------------------------------------------------------------------------
# Self-loop
# ---------------------------------------------------------------------------
def test_self_loop_rejected():
    v = validate_rules([_col("loop", "colA", ["colA"])])
    assert not v.ok
    assert "self-loop" in _codes(v)


# ---------------------------------------------------------------------------
# Duplicate edge
# ---------------------------------------------------------------------------
def test_duplicate_edge_across_rules_rejected():
    rules = [_row("s", ["colA"]), _col("ab1", "colA", ["colB"]), _col("ab2", "colA", ["colB"])]
    v = validate_rules(rules)
    assert not v.ok
    assert "duplicate-edge" in _codes(v)


def test_duplicate_expected_within_one_rule_rejected():
    v = validate_rules([_row("s", ["colA", "colA"])])
    assert not v.ok
    assert "duplicate-expected" in _codes(v)


# ---------------------------------------------------------------------------
# Cycle
# ---------------------------------------------------------------------------
def test_two_node_cycle_rejected():
    rules = [_col("ab", "colA", ["colB"]), _col("ba", "colB", ["colA"])]
    v = validate_rules(rules)
    assert not v.ok
    assert "cycle" in _codes(v)


def test_three_node_cycle_rejected():
    rules = [_col("ab", "colA", ["colB"]), _col("bc", "colB", ["colC"]), _col("ca", "colC", ["colA"])]
    v = validate_rules(rules)
    assert not v.ok
    assert "cycle" in _codes(v)
    cyc = next(e for e in v.errors if e["code"] == "cycle")["cycle"]
    assert cyc[0] == cyc[-1]  # closes on itself


def test_find_cycle_returns_none_for_acyclic():
    edges = build_edges([_row("s", ["colA"]), _col("ab", "colA", ["colB"])])
    assert find_cycle(edges) is None


# ---------------------------------------------------------------------------
# Reachability WARNING (never blocks)
# ---------------------------------------------------------------------------
def test_unreachable_column_is_a_warning_not_an_error():
    # colB<-colC is a floating chain with no path from START (no row rule).
    rules = [_row("s", ["colA"]), _col("cb", "colC", ["colB"])]
    v = validate_rules(rules)
    assert v.ok  # still valid (acyclic)
    codes = {w["code"] for w in v.warnings}
    assert "unreachable" in codes
    unreachable = {w["column"] for w in v.warnings if w["code"] == "unreachable"}
    assert {"colB", "colC"} <= unreachable


def test_reachable_from_start_set():
    edges = build_edges([_row("s", ["colA"]), _col("ab", "colA", ["colB"])])
    assert reachable_from_start(edges) == {"colA", "colB"}


# ---------------------------------------------------------------------------
# Structural required-field errors
# ---------------------------------------------------------------------------
def test_column_rule_missing_trigger_column_rejected():
    v = validate_rules([{"trigger_kind": "column", "expected_columns": ["colB"]}])
    assert "missing-trigger-column" in _codes(v)


def test_row_rule_with_trigger_column_rejected():
    v = validate_rules([{"trigger_kind": "row", "trigger_column": "colA", "expected_columns": ["colB"]}])
    assert "row-trigger-has-column" in _codes(v)


def test_no_expected_columns_rejected():
    v = validate_rules([{"trigger_kind": "row", "expected_columns": []}])
    assert "no-expected-columns" in _codes(v)


def test_bad_trigger_kind_rejected():
    v = validate_rules([{"trigger_kind": "sideways", "expected_columns": ["colA"]}])
    assert "bad-trigger-kind" in _codes(v)


# ---------------------------------------------------------------------------
# would_create_cycle preview helper
# ---------------------------------------------------------------------------
def test_would_create_cycle_true_when_edge_closes_loop():
    existing = [_col("ab", "colA", ["colB"])]
    assert would_create_cycle(existing, _col("ba", "colB", ["colA"])) is True


def test_would_create_cycle_false_for_acyclic_addition():
    existing = [_col("ab", "colA", ["colB"])]
    assert would_create_cycle(existing, _col("bc", "colB", ["colC"])) is False
