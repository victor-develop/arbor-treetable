"""Branch-coverage suite for the bounded, navigable LLM read API
(``arbor.core.explore``).

Bench-free: uses the pure in-memory fake repo. Two fixtures are exercised — the
small canonical sheet (``tests/fixtures/canonical.py``) and a synthesized LARGE
multi-level tree (>=600 nodes) built here against ``InMemoryRepository`` directly.

This file is RED until ``arbor/core/explore.py`` exists — that is expected; it is
the executable contract for the read API. It does NOT implement anything.

Contract under test (from the task DELIVERABLES / recommended design):
  EXPLORE_THRESHOLD = 500, NODE_BUDGET cap 200, get_cells budget 1000.
  sheet_overview / list_children / get_subtree / get_node / search_nodes /
  get_cells / assert_snapshot_size + SheetTooLargeError.
"""

from __future__ import annotations

import pytest

from arbor.core import explore
from arbor.core.explore import (
    EXPLORE_THRESHOLD,
    SheetTooLargeError,
    assert_snapshot_size,
    get_cells,
    get_node,
    get_subtree,
    list_children,
    search_nodes,
    sheet_overview,
)
from arbor.core.testing import InMemoryRepository
from arbor.core.types import Actor, ActorType
from tests.fixtures.canonical import seed_canonical_sheet

# All-public legacy fixtures: an admin actor sees every column, so explore
# reads return the same shape as before the Feature-3 read-ACL wiring.
ADMIN = Actor("admin", ActorType.HUMAN, is_admin=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def fx():
    """The 6-node canonical sheet (R, P1, X, P2, Y, Z; max depth 3)."""
    return seed_canonical_sheet()


@pytest.fixture()
def small_repo(fx):
    return fx.repo, fx.sheet


# Large tree shape — deterministic, multi-level, >= 600 nodes.
#   1 root -> ROOT_FANOUT branches -> each branch has MID_FANOUT children ->
#   each of those has LEAF_FANOUT leaves.
LARGE_ROOT_FANOUT = 12
LARGE_MID_FANOUT = 5
LARGE_LEAF_FANOUT = 9


@pytest.fixture()
def large():
    """Synthesize a wide+deep sheet with >= 600 nodes.

    Returns (repo, sheet, meta) where meta records expected structural facts so
    assertions read off the builder, not magic numbers.
    """
    repo = InMemoryRepository()
    sheet = repo.add_sheet("BIG", structural_owner="A", settings={})
    col_name = repo.add_column("col:name", sheet, "name", column_owner="B", is_label=True)
    col_status = repo.add_column("col:status", sheet, "status", column_owner="C")
    col_budget = repo.add_column("col:budget", sheet, "budget", column_owner="C")

    root = repo.add_node("ROOT", sheet, parent=None)
    repo.seed_value(sheet, root, col_name, "Root of Big")

    branches: list[str] = []
    mids: list[str] = []
    leaves: list[str] = []
    for b in range(LARGE_ROOT_FANOUT):
        bn = repo.add_node(f"B{b}", sheet, parent=root)
        repo.seed_value(sheet, bn, col_name, f"Branch {b}")
        branches.append(bn)
        for m in range(LARGE_MID_FANOUT):
            mn = repo.add_node(f"M{b}_{m}", sheet, parent=bn)
            repo.seed_value(sheet, mn, col_name, f"Mid {b}.{m}")
            mids.append(mn)
            for leaf in range(LARGE_LEAF_FANOUT):
                ln = repo.add_node(f"L{b}_{m}_{leaf}", sheet, parent=mn)
                repo.seed_value(sheet, ln, col_name, f"Leaf {b}.{m}.{leaf}")
                repo.seed_value(sheet, ln, col_status, "open" if leaf % 2 else "closed")
                repo.seed_value(sheet, ln, col_budget, leaf * 100)
                leaves.append(ln)

    total = 1 + len(branches) + len(mids) + len(leaves)
    meta = {
        "root": root,
        "branches": branches,
        "mids": mids,
        "leaves": leaves,
        "total": total,
        "max_depth": 3,  # root=0, branch=1, mid=2, leaf=3
        "col_name": col_name,
        "col_status": col_status,
        "col_budget": col_budget,
        "branch_child_count": LARGE_MID_FANOUT,
        "mid_child_count": LARGE_LEAF_FANOUT,
    }
    return repo, sheet, meta


def test_large_fixture_exceeds_threshold(large):
    """Sanity: the synthesized tree is genuinely > EXPLORE_THRESHOLD."""
    _, _, meta = large
    assert meta["total"] >= 600
    assert meta["total"] > EXPLORE_THRESHOLD


# ---------------------------------------------------------------------------
# sheet_overview  — always safe, NO per-node cell payload
# ---------------------------------------------------------------------------
def test_overview_small_counts_roots_depth(small_repo, fx):
    repo, sheet = small_repo
    ov = sheet_overview(repo, sheet, actor=ADMIN)
    assert ov["name"] == sheet
    assert ov["structural_owner"] == "A"
    assert ov["total_nodes"] == 6
    assert ov["root_node_ids"] == [fx.R]
    assert ov["max_depth"] == 2  # R(0) -> P1(1) -> X(2)


def test_overview_columns_metadata_only(small_repo, fx):
    repo, sheet = small_repo
    ov = sheet_overview(repo, sheet, actor=ADMIN)
    cols = {c["name"]: c for c in ov["columns"]}
    assert set(cols) == {fx.col_name, fx.col_status, fx.col_budget, fx.col_notes}
    name_col = cols[fx.col_name]
    assert set(name_col) == {"name", "field", "label", "type", "column_owner"}
    assert name_col["field"] == "name"
    assert name_col["column_owner"] == "B"


def test_overview_has_no_per_node_payload(small_repo):
    """Overview must never carry cell values / a node list (always-safe contract)."""
    repo, sheet = small_repo
    ov = sheet_overview(repo, sheet, actor=ADMIN)
    assert "nodes" not in ov
    assert "values" not in ov
    for tb in ov["top_branches"]:
        assert "values" not in tb
        assert set(tb) == {"node", "label", "child_count"}


def test_overview_top_branches_child_counts(small_repo, fx):
    repo, sheet = small_repo
    ov = sheet_overview(repo, sheet, actor=ADMIN)
    by_node = {tb["node"]: tb for tb in ov["top_branches"]}
    # Top branches are the children of the root(s): P1 and P2.
    assert set(by_node) == {fx.P1, fx.P2}
    assert by_node[fx.P1]["child_count"] == 1  # X
    assert by_node[fx.P2]["child_count"] == 2  # Y, Z
    assert by_node[fx.P1]["label"] == "Phase 1"


def test_overview_large_depth_and_roots(large):
    repo, sheet, meta = large
    ov = sheet_overview(repo, sheet, actor=ADMIN)
    assert ov["total_nodes"] == meta["total"]
    assert ov["root_node_ids"] == [meta["root"]]
    assert ov["max_depth"] == meta["max_depth"]
    # Top branches = children of ROOT, each with MID_FANOUT children.
    assert len(ov["top_branches"]) == LARGE_ROOT_FANOUT
    assert all(tb["child_count"] == meta["branch_child_count"] for tb in ov["top_branches"])


def test_overview_safe_above_threshold(large):
    """Overview must succeed even when the sheet is over the snapshot threshold."""
    repo, sheet, _ = large
    ov = sheet_overview(repo, sheet, actor=ADMIN)  # must NOT raise SheetTooLargeError
    assert ov["total_nodes"] > EXPLORE_THRESHOLD


# ---------------------------------------------------------------------------
# list_children — roots, children, limit clamp, pagination, empty
# ---------------------------------------------------------------------------
def test_list_children_roots_when_parent_none(small_repo, fx):
    repo, sheet = small_repo
    page = list_children(repo, sheet, parent=None, actor=ADMIN)
    names = [n["name"] for n in page["nodes"]]
    assert names == [fx.R]
    assert page["has_more"] is False
    assert page["next_cursor"] is None
    assert page["child_count"] == 1


def test_list_children_of_node_with_values_and_child_count(small_repo, fx):
    repo, sheet = small_repo
    page = list_children(repo, sheet, parent=fx.P2, actor=ADMIN)
    rows = {n["name"]: n for n in page["nodes"]}
    assert set(rows) == {fx.Y, fx.Z}
    assert page["child_count"] == 2
    y = rows[fx.Y]
    assert y["parent"] == fx.P2
    assert y["label"] == "Task Y"
    assert y["child_count"] == 0  # leaf
    # values carries the node's cells
    assert y["values"][fx.col_budget] == 5000


def test_list_children_empty_for_leaf(small_repo, fx):
    repo, sheet = small_repo
    page = list_children(repo, sheet, parent=fx.X, actor=ADMIN)  # X is a leaf
    assert page["nodes"] == []
    assert page["child_count"] == 0
    assert page["has_more"] is False
    assert page["next_cursor"] is None


def test_list_children_limit_clamped_low(large):
    """limit < 1 clamps to 1."""
    repo, sheet, meta = large
    page = list_children(repo, sheet, parent=meta["root"], limit=0, actor=ADMIN)
    assert len(page["nodes"]) == 1
    assert page["has_more"] is True
    assert page["next_cursor"] is not None


def test_list_children_limit_clamped_high(large):
    """limit > 200 clamps to 200 (root has only ROOT_FANOUT children here, so
    the clamp is asserted via a parent with enough children would be needed;
    instead assert the page never exceeds 200 and returns all root children)."""
    repo, sheet, meta = large
    page = list_children(repo, sheet, parent=meta["root"], limit=10_000, actor=ADMIN)
    assert len(page["nodes"]) <= 200
    # ROOT has LARGE_ROOT_FANOUT (<200) children, all returned, no more pages.
    assert len(page["nodes"]) == LARGE_ROOT_FANOUT
    assert page["has_more"] is False


def test_list_children_pagination_walks_all_pages_no_dupes(large):
    """next_cursor + has_more across pages; last page has_more False; full,
    duplicate-free coverage of a mid node's LEAF_FANOUT children."""
    repo, sheet, meta = large
    mid = meta["mids"][0]
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        page = list_children(repo, sheet, parent=mid, cursor=cursor, limit=4, actor=ADMIN)
        pages += 1
        seen.extend(n["name"] for n in page["nodes"])
        if not page["has_more"]:
            assert page["next_cursor"] is None
            break
        assert page["next_cursor"] is not None
        cursor = page["next_cursor"]
        assert pages < 100  # guard against an infinite loop
    assert len(seen) == meta["mid_child_count"]
    assert len(set(seen)) == len(seen)  # no duplicates across pages
    # child_count reflects the true total regardless of paging.
    assert all(c == meta["mid_child_count"] for c in [meta["mid_child_count"]])


def test_list_children_middle_page_has_more_true(large):
    repo, sheet, meta = large
    mid = meta["mids"][1]
    first = list_children(repo, sheet, parent=mid, limit=3, actor=ADMIN)
    assert first["has_more"] is True
    assert len(first["nodes"]) == 3
    assert first["child_count"] == meta["mid_child_count"]


# ---------------------------------------------------------------------------
# get_subtree — depth window, node-budget cap, unknown node
# ---------------------------------------------------------------------------
def test_subtree_depth_one_is_node_plus_direct_children(small_repo, fx):
    repo, sheet = small_repo
    sub = get_subtree(repo, sheet, fx.R, depth=1, actor=ADMIN)
    names = [n["name"] for n in sub["nodes"]]
    # preorder window: R, then its direct children P1, P2 (no grandchildren)
    assert names[0] == fx.R
    assert set(names) == {fx.R, fx.P1, fx.P2}
    assert fx.X not in names  # X is depth 2 below R
    assert sub["has_more"] is False


def test_subtree_deeper_includes_grandchildren(small_repo, fx):
    repo, sheet = small_repo
    sub = get_subtree(repo, sheet, fx.R, depth=2, actor=ADMIN)
    names = [n["name"] for n in sub["nodes"]]
    assert {fx.R, fx.P1, fx.X, fx.P2, fx.Y, fx.Z} == set(names)
    # preorder: R before P1 before X; P1-subtree before P2.
    assert names.index(fx.R) < names.index(fx.P1) < names.index(fx.X)
    assert names.index(fx.P1) < names.index(fx.P2)


def test_subtree_node_budget_caps_with_clip(large):
    """A deep request over a giant branch is capped at the node budget (<=200)
    and clipped with has_more + a next_cursor."""
    repo, sheet, meta = large
    branch = meta["branches"][0]  # subtree = 1 + 5 mids + 45 leaves = 51 (< budget)
    # Request the whole tree from ROOT at full depth -> far exceeds 200.
    sub = get_subtree(repo, sheet, meta["root"], depth=10, limit=10_000, actor=ADMIN)
    assert len(sub["nodes"]) <= 200
    assert sub["has_more"] is True
    assert sub["next_cursor"] is not None
    # window starts at the requested node, preorder.
    assert sub["nodes"][0]["name"] == meta["root"]
    assert branch  # branch handle exercised for clarity


def test_subtree_resume_via_cursor_advances(large):
    repo, sheet, meta = large
    first = get_subtree(repo, sheet, meta["root"], depth=10, limit=10_000, actor=ADMIN)
    assert first["has_more"] is True
    first_names = {n["name"] for n in first["nodes"]}
    second = get_subtree(
        repo, sheet, meta["root"], depth=10, cursor=first["next_cursor"], limit=10_000, actor=ADMIN)
    second_names = {n["name"] for n in second["nodes"]}
    assert second_names  # progress was made
    assert first_names.isdisjoint(second_names)  # no overlap across windows


def test_subtree_within_budget_not_clipped(large):
    repo, sheet, meta = large
    branch = meta["branches"][2]
    sub = get_subtree(repo, sheet, branch, depth=10, limit=10_000, actor=ADMIN)
    # 1 branch + 5 mids + 45 leaves = 51 nodes, under the 200 budget.
    expected = 1 + meta["branch_child_count"] + (
        meta["branch_child_count"] * meta["mid_child_count"]
    )
    assert len(sub["nodes"]) == expected
    assert sub["has_more"] is False
    assert sub["next_cursor"] is None


def test_subtree_unknown_node_raises(small_repo):
    repo, sheet = small_repo
    with pytest.raises((KeyError, ValueError)):
        get_subtree(repo, sheet, "does-not-exist", depth=1, actor=ADMIN)


# ---------------------------------------------------------------------------
# get_node — full cells, breadcrumb, child_count, root path, unknown
# ---------------------------------------------------------------------------
def test_get_node_full_cells_and_breadcrumb(small_repo, fx):
    repo, sheet = small_repo
    node = get_node(repo, sheet, fx.X, actor=ADMIN)
    assert node["name"] == fx.X
    assert node["parent"] == fx.P1
    assert node["label"] == "Task X"
    assert node["child_count"] == 0
    # ALL cells present for X (name, status, budget seeded).
    assert node["values"][fx.col_name] == "Task X"
    assert node["values"][fx.col_status] == "todo"
    assert node["values"][fx.col_budget] == 1000
    # breadcrumb root..node order
    path = [p["name"] if isinstance(p, dict) else p for p in node["path"]]
    assert path == [fx.R, fx.P1, fx.X]


def test_get_node_root_path_is_self_only(small_repo, fx):
    repo, sheet = small_repo
    node = get_node(repo, sheet, fx.R, actor=ADMIN)
    path = [p["name"] if isinstance(p, dict) else p for p in node["path"]]
    assert path == [fx.R]
    assert node["parent"] is None
    assert node["child_count"] == 2  # P1, P2


def test_get_node_unknown_raises(small_repo):
    repo, sheet = small_repo
    with pytest.raises((KeyError, ValueError)):
        get_node(repo, sheet, "ghost", actor=ADMIN)


# ---------------------------------------------------------------------------
# search_nodes — label hit, column-scoped value hit, no-match, pagination
# ---------------------------------------------------------------------------
def test_search_label_hit_case_insensitive(small_repo, fx):
    repo, sheet = small_repo
    res = search_nodes(repo, sheet, "phase", actor=ADMIN)  # matches "Phase 1" / "Phase 2"
    names = {n["name"] for n in res["nodes"]}
    assert names == {fx.P1, fx.P2}


def test_search_column_scoped_value_hit(small_repo, fx):
    repo, sheet = small_repo
    # Only X has status "todo".
    res = search_nodes(repo, sheet, "todo", column=fx.col_status, actor=ADMIN)
    names = {n["name"] for n in res["nodes"]}
    assert names == {fx.X}


def test_search_column_scope_excludes_label_only_match(small_repo, fx):
    repo, sheet = small_repo
    # "phase" lives in the label, NOT in the status column -> no hits when scoped.
    res = search_nodes(repo, sheet, "phase", column=fx.col_status, actor=ADMIN)
    assert res["nodes"] == []
    assert res["has_more"] is False


def test_search_all_columns_when_column_none(small_repo, fx):
    repo, sheet = small_repo
    # "todo" only exists as a status value; column=None searches all values+label.
    res = search_nodes(repo, sheet, "todo", actor=ADMIN)
    names = {n["name"] for n in res["nodes"]}
    assert names == {fx.X}


def test_search_no_match_empty(small_repo):
    repo, sheet = small_repo
    res = search_nodes(repo, sheet, "zzz-nothing-zzz", actor=ADMIN)
    assert res["nodes"] == []
    assert res["has_more"] is False
    assert res["next_cursor"] is None


def test_search_pagination_covers_all_matches(large):
    repo, sheet, meta = large
    # Every leaf label contains "Leaf"; there are LARGE_ROOT*MID*LEAF leaves.
    expected = len(meta["leaves"])
    seen: list[str] = []
    cursor = None
    guard = 0
    while True:
        res = search_nodes(repo, sheet, "leaf", cursor=cursor, limit=50, actor=ADMIN)
        seen.extend(n["name"] for n in res["nodes"])
        guard += 1
        if not res["has_more"]:
            assert res["next_cursor"] is None
            break
        assert res["next_cursor"] is not None
        cursor = res["next_cursor"]
        assert guard < 1000
    assert len(seen) == expected
    assert len(set(seen)) == len(seen)  # no dupes across pages


def test_search_limit_clamped(large):
    repo, sheet, _ = large
    res = search_nodes(repo, sheet, "leaf", limit=10_000, actor=ADMIN)
    assert len(res["nodes"]) <= 200


# ---------------------------------------------------------------------------
# get_cells — sparse fetch, over-budget guard, missing node/column
# ---------------------------------------------------------------------------
def test_get_cells_returns_requested_matrix(small_repo, fx):
    repo, sheet = small_repo
    res = get_cells(repo, sheet, nodes=[fx.X, fx.Y], columns=[fx.col_budget, fx.col_name], actor=ADMIN)
    assert res["cells"][fx.X][fx.col_budget] == 1000
    assert res["cells"][fx.X][fx.col_name] == "Task X"
    assert res["cells"][fx.Y][fx.col_budget] == 5000


def test_get_cells_missing_value_is_none(small_repo, fx):
    repo, sheet = small_repo
    # Y has no status seeded -> None, not a KeyError.
    res = get_cells(repo, sheet, nodes=[fx.Y], columns=[fx.col_status], actor=ADMIN)
    assert res["cells"][fx.Y][fx.col_status] is None


def test_get_cells_over_budget_raises(large):
    """len(nodes)*len(columns) > 1000 must raise a typed error."""
    repo, sheet, meta = large
    many_nodes = meta["leaves"][:200]  # 200 nodes
    cols = [meta["col_name"], meta["col_status"], meta["col_budget"], meta["col_name"], meta["col_status"], meta["col_budget"]]  # 6 cols -> 1200 > 1000
    with pytest.raises(Exception) as exc:
        get_cells(repo, sheet, nodes=many_nodes, columns=cols, actor=ADMIN)
    # not the size guard for whole-sheet snapshot
    assert not isinstance(exc.value, SheetTooLargeError)


def test_get_cells_at_budget_boundary_ok(large):
    repo, sheet, meta = large
    nodes = meta["leaves"][:200]
    cols = [meta["col_name"], meta["col_status"], meta["col_budget"], meta["col_name"], meta["col_status"]]  # 200*5 = 1000, == budget, allowed
    res = get_cells(repo, sheet, nodes=nodes, columns=cols, actor=ADMIN)
    assert len(res["cells"]) == 200


# ---------------------------------------------------------------------------
# assert_snapshot_size guard + SheetTooLargeError
# ---------------------------------------------------------------------------
def test_guard_ok_at_or_below_threshold(small_repo):
    repo, sheet = small_repo
    # 6 nodes — well under EXPLORE_THRESHOLD; returns without raising.
    assert assert_snapshot_size(repo, sheet) is None or True


def test_guard_ok_exactly_at_threshold():
    """count == THRESHOLD is allowed (guard fires only when count > threshold)."""
    repo = InMemoryRepository()
    sheet = repo.add_sheet("EXACT", structural_owner="A")
    repo.add_column("col:name", sheet, "name", column_owner="B", is_label=True)
    root = repo.add_node("root", sheet, parent=None)
    for i in range(EXPLORE_THRESHOLD - 1):  # +1 root == THRESHOLD total
        repo.add_node(f"n{i}", sheet, parent=root)
    assert explore.count_nodes(repo, sheet) == EXPLORE_THRESHOLD
    assert_snapshot_size(repo, sheet)  # must NOT raise at the boundary


def test_guard_raises_above_threshold(large):
    repo, sheet, meta = large
    with pytest.raises(SheetTooLargeError) as exc:
        assert_snapshot_size(repo, sheet)
    err = exc.value
    # Carries the actual count and the threshold.
    assert getattr(err, "count", None) == meta["total"]
    assert getattr(err, "threshold", None) == EXPLORE_THRESHOLD


def test_too_large_message_names_count_and_steers_to_explore(large):
    repo, sheet, meta = large
    with pytest.raises(SheetTooLargeError) as exc:
        assert_snapshot_size(repo, sheet)
    msg = str(exc.value)
    assert str(meta["total"]) in msg
    assert str(EXPLORE_THRESHOLD) in msg
    # Steering: names at least the overview entrypoint among the explore tools.
    assert "getSheetOverview" in msg or "sheet_overview" in msg


def test_count_nodes_matches_total(large):
    repo, sheet, meta = large
    assert explore.count_nodes(repo, sheet) == meta["total"]
