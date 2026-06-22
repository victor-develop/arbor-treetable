"""BENCH-FREE: the Frappe-side seed must mirror the pure canonical fixture.

The canonical sheet `S` is defined once in ``tests/fixtures/canonical.py`` (the
source of truth) and re-expressed for a real bench in ``arbor.adapter.seed``,
which consumes the frappe-free spec ``arbor.adapter.canonical_spec``. This test
re-derives the canonical structure from the pure fixture and asserts the
adapter spec matches it field-for-field, so the two seeds can never silently
diverge.

No frappe / no bench required: we read the pure fixture's in-memory repo and
the adapter's plain spec constants (which import nothing).
"""

from __future__ import annotations

try:  # ``arbor.adapter`` on a bench; ``arbor.arbor.adapter`` in the dev repo.
    from arbor.adapter import canonical_spec as spec
except ModuleNotFoundError:
    from arbor.arbor.adapter import canonical_spec as spec

from tests.fixtures.canonical import A, B, C, D
from tests.fixtures.canonical import seed_canonical_sheet as pure_seed


def test_personas_match():
    assert spec.PERSONAS == ("A", "B", "C", "D", "E", "F", "G", "EXT", "AGENT")


def test_columns_match_pure_fixture():
    fx = pure_seed()
    pure_cols = {c.field: c for c in fx.repo.list_columns(fx.sheet)}

    spec_by_field = {s["field"]: s for s in spec.COLUMNS}
    assert set(spec_by_field) == set(pure_cols)

    for fieldname, col in pure_cols.items():
        s = spec_by_field[fieldname]
        assert s["type"] == col.type, fieldname
        assert s["column_owner"] == col.column_owner, fieldname
        assert s.get("is_label", False) == col.is_label, fieldname
        assert s.get("editors", []) == list(col.editors), fieldname


def test_tree_edges_match_pure_fixture():
    fx = pure_seed()
    pure_edges = {n.name: n.parent for n in fx.repo.list_nodes(fx.sheet)}
    seed_edges = {label: parent for label, parent in spec.TREE}
    assert seed_edges == pure_edges


def test_values_match_pure_fixture():
    fx = pure_seed()
    col_field = {c.name: c.field for c in fx.repo.list_columns(fx.sheet)}
    pure_values = {
        (node, col_field[col]): val for (node, col), val in fx.repo.values.items()
    }
    seed_values = {(n, f): v for (n, f, v) in spec.VALUES}
    assert seed_values == pure_values


def test_grant_matches_pure_fixture():
    fx = pure_seed()
    grant = fx.repo.get_branch_grant(fx.grant_P2)
    assert spec.GRANT["branch_root"] == grant.branch_root == fx.P2
    assert spec.GRANT["grantee"] == grant.grantee == D
    assert spec.GRANT["granted_by"] == grant.granted_by == A
