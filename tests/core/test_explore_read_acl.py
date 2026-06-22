"""RED — Feature 3 read-ACL applied to the EXPLORE surface (the key under-wired
seam).

Every explore read (sheet_overview / list_children / get_subtree / get_node /
search_nodes / get_cells) gains an ``actor`` param and replaces
``repo.list_columns(sheet)`` with ``visible_columns(repo, sheet, actor, ...)`` so
a forbidden column never appears in any returned ``columns`` / ``values`` map.

get_cells additionally DROPS requested column names the actor cannot read,
treating them as nonexistent (omit, NOT error) so existence is not leaked.

search_nodes must NOT match on a forbidden column's value.

These tests call the explore functions with ``actor=`` as a keyword so they are
RED on signature (the functions don't accept it yet), and assert filtering once
they do.
"""

from __future__ import annotations

from arbor.core import explore
from arbor.core.types import Actor, ActorType
from tests.fixtures.canonical import C, E, G, seed_canonical_sheet

UNRELATED = G


def _human(user: str, *, is_admin: bool = False) -> Actor:
    return Actor(user, ActorType.HUMAN, is_admin=is_admin)


def _lock_budget_owner_only(fx) -> None:
    fx.repo.update_column(fx.sheet, fx.col_budget, {"read_level": "owner-only", "readers": []})


# ---------------------------------------------------------------------------
# sheet_overview.columns omits the forbidden column.
# ---------------------------------------------------------------------------
def test_sheet_overview_omits_forbidden_column():
    fx = seed_canonical_sheet()
    _lock_budget_owner_only(fx)
    ov = explore.sheet_overview(fx.repo, fx.sheet, actor=_human(UNRELATED))
    names = {c["name"] for c in ov["columns"]}
    assert fx.col_budget not in names
    # owner still sees it
    ov_owner = explore.sheet_overview(fx.repo, fx.sheet, actor=_human(C))
    assert fx.col_budget in {c["name"] for c in ov_owner["columns"]}


# ---------------------------------------------------------------------------
# list_children / get_subtree / get_node / search_nodes .values omit it.
# ---------------------------------------------------------------------------
def test_list_children_values_omit_forbidden_column():
    fx = seed_canonical_sheet()
    _lock_budget_owner_only(fx)
    res = explore.list_children(fx.repo, fx.sheet, parent=fx.P2, actor=_human(UNRELATED))
    for row in res["nodes"]:
        assert fx.col_budget not in row["values"]


def test_get_subtree_values_omit_forbidden_column():
    fx = seed_canonical_sheet()
    _lock_budget_owner_only(fx)
    res = explore.get_subtree(fx.repo, fx.sheet, fx.R, depth=3, actor=_human(UNRELATED))
    for row in res["nodes"]:
        assert fx.col_budget not in row["values"]


def test_get_node_values_omit_forbidden_column():
    fx = seed_canonical_sheet()
    _lock_budget_owner_only(fx)
    res = explore.get_node(fx.repo, fx.sheet, fx.X, actor=_human(UNRELATED))
    assert fx.col_budget not in res["values"]
    # label still present (label column always visible)
    assert res["label"] == "Task X"
    # owner sees the budget cell
    res_owner = explore.get_node(fx.repo, fx.sheet, fx.X, actor=_human(C))
    assert res_owner["values"][fx.col_budget] == 1000


def test_search_nodes_values_omit_forbidden_column():
    fx = seed_canonical_sheet()
    _lock_budget_owner_only(fx)
    res = explore.search_nodes(fx.repo, fx.sheet, "Task", actor=_human(UNRELATED))
    assert res["nodes"]  # label matches still found
    for row in res["nodes"]:
        assert fx.col_budget not in row["values"]


# ---------------------------------------------------------------------------
# search_nodes does NOT match on a forbidden column's VALUE for a restricted
# viewer (no value-existence leak through search).
# ---------------------------------------------------------------------------
def test_search_does_not_match_forbidden_column_value():
    fx = seed_canonical_sheet()
    _lock_budget_owner_only(fx)
    # 12000 is the budget of Z and unique to a forbidden column.
    res = explore.search_nodes(fx.repo, fx.sheet, "12000", actor=_human(UNRELATED))
    assert res["nodes"] == []
    # owner CAN find it (the value is readable to them)
    res_owner = explore.search_nodes(fx.repo, fx.sheet, "12000", actor=_human(C))
    assert {n["name"] for n in res_owner["nodes"]} == {fx.Z}


def test_search_scoped_to_forbidden_column_returns_nothing():
    fx = seed_canonical_sheet()
    _lock_budget_owner_only(fx)
    # Explicitly scoping search to a forbidden column must not leak — omit/no match.
    res = explore.search_nodes(
        fx.repo, fx.sheet, "12000", column=fx.col_budget, actor=_human(UNRELATED)
    )
    assert res["nodes"] == []


# ---------------------------------------------------------------------------
# get_cells: requested forbidden column name is OMITTED, not an error (existence
# is hidden); readable columns still returned.
# ---------------------------------------------------------------------------
def test_get_cells_omits_forbidden_column_without_error():
    fx = seed_canonical_sheet()
    _lock_budget_owner_only(fx)
    res = explore.get_cells(
        fx.repo,
        fx.sheet,
        nodes=[fx.X, fx.Y, fx.Z],
        columns=[fx.col_name, fx.col_budget],  # budget forbidden to UNRELATED
        actor=_human(UNRELATED),
    )
    for node, row in res["cells"].items():
        assert fx.col_budget not in row, node
        assert fx.col_name in row, node  # readable column kept


def test_get_cells_owner_gets_forbidden_column():
    fx = seed_canonical_sheet()
    _lock_budget_owner_only(fx)
    res = explore.get_cells(
        fx.repo,
        fx.sheet,
        nodes=[fx.X],
        columns=[fx.col_budget],
        actor=_human(C),
    )
    assert res["cells"][fx.X][fx.col_budget] == 1000


# ---------------------------------------------------------------------------
# explicit-readers viewer (E) sees a column an unrelated viewer cannot.
# ---------------------------------------------------------------------------
def test_explicit_reader_sees_column_in_explore():
    fx = seed_canonical_sheet()
    fx.repo.update_column(fx.sheet, fx.col_budget, {"read_level": "explicit-readers", "readers": [E]})
    res_reader = explore.get_node(fx.repo, fx.sheet, fx.X, actor=_human(E))
    assert res_reader["values"][fx.col_budget] == 1000
    res_outsider = explore.get_node(fx.repo, fx.sheet, fx.X, actor=_human(UNRELATED))
    assert fx.col_budget not in res_outsider["values"]
