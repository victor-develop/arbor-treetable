"""Explore read API — real-adapter (bench) coverage.

runnable: NEEDS FRAPPE BENCH (``@pytest.mark.bench``). AUTHOR-ONLY in the
backend lane: do not execute here (the concurrent frontend workflow owns the
bench DB). On a bench:

    bench --site <site> run-tests --module tests.backend.test_explore_bench
    # or, from a bench-activated venv:
    pytest tests/backend/test_explore_bench.py -m bench

What it proves against the REAL ``FrappeRepository`` + the whitelisted REST
funnel in :mod:`arbor.api` (the SAME executor + pure ``arbor.core.explore``
functions the bench-free core suite exercises against the in-memory double):

1. ``count_nodes`` is a cheap COUNT(*) that matches ``len(list_nodes)``.
2. ``sheet_overview`` is ALWAYS safe — it returns structure (no per-node cells)
   even on a sheet far over ``EXPLORE_THRESHOLD``.
3. Keyset pagination (``list_children`` / ``get_subtree`` / ``search_nodes``)
   walks a large tree page-by-page with stable, non-overlapping, exhaustive
   windows driven by the opaque ``next_cursor`` over real NestedSet ``lft``.
4. The >500 guard: ``get_sheet_snapshot`` on an over-threshold sheet raises a
   typed ``SheetTooLargeError`` surfaced as a 4xx (HTTP 422 + the explore-tool
   hint) — NEVER an unhandled 500. A sheet AT/below the threshold still returns
   the full snapshot.
5. ``get_cells`` returns the requested sparse matrix and rejects an
   over-budget request as a 4xx.

These tests build a LARGE sheet directly through ``FrappeRepository`` (the same
mutator path ``execute_action`` uses), so the seeded data is byte-identical in
structure to runtime-created data and the NestedSet ``lft/rgt`` are real.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.bench

frappe = pytest.importorskip("frappe")

from arbor.core.explore import (  # noqa: E402
    CELL_BUDGET,
    EXPLORE_THRESHOLD,
)

from arbor import api  # noqa: E402

from tests.backend import _helpers as h  # noqa: E402

try:  # ``arbor.adapter`` on a bench; ``arbor.arbor.adapter`` in the dev repo.
    from arbor.adapter.repository import FrappeRepository
except ModuleNotFoundError:  # pragma: no cover - dev-layout fallback
    from arbor.arbor.adapter.repository import FrappeRepository  # type: ignore


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def _new_sheet(title: str, owner: str = "A") -> str:
    """A bare sheet (no canonical tree) owned by ``owner``, with one label column."""
    h.ensure_user(owner)
    doc = frappe.new_doc("Tree Sheet")
    doc.title = title
    doc.structural_owner = h.user(owner)
    doc.status = "active"
    doc.settings = {}
    doc.insert(ignore_permissions=True)
    return doc.name


def _label_column(repo: FrappeRepository, sheet: str, owner: str = "A") -> str:
    return repo.create_column(
        sheet,
        {
            "field": "name",
            "label": "Name",
            "type": "text",
            "is_label": True,
            "column_owner": h.user(owner),
            "editors": [],
        },
    )


def _build_wide_sheet(n_children: int, owner: str = "A") -> dict:
    """A single root with ``n_children`` leaf children (total = n_children + 1).

    Used to drive the >500 guard and root-level keyset pagination over a real
    NestedSet. Each child gets a label so search/overview have content.
    """
    sheet = _new_sheet(f"explore-wide-{n_children}", owner=owner)
    repo = FrappeRepository()
    label_col = _label_column(repo, sheet, owner=owner)
    root = repo.create_node(sheet=sheet, parent=None)
    repo.set_value(sheet, root, label_col, "ROOT")
    children: list[str] = []
    for i in range(n_children):
        c = repo.create_node(sheet=sheet, parent=root)
        repo.set_value(sheet, c, label_col, f"child-{i:04d}")
        children.append(c)
    frappe.db.commit()
    return {"sheet": sheet, "root": root, "children": children, "label_col": label_col}


def _build_deep_sheet(depth: int, owner: str = "A") -> dict:
    """A single linear chain root->n1->...->n{depth} (total = depth + 1 nodes).

    Drives bounded ``get_subtree`` depth windows + breadcrumb paths.
    """
    sheet = _new_sheet(f"explore-deep-{depth}", owner=owner)
    repo = FrappeRepository()
    label_col = _label_column(repo, sheet, owner=owner)
    chain: list[str] = []
    parent = None
    for i in range(depth + 1):
        node = repo.create_node(sheet=sheet, parent=parent)
        repo.set_value(sheet, node, label_col, f"level-{i}")
        chain.append(node)
        parent = node
    frappe.db.commit()
    return {"sheet": sheet, "chain": chain, "label_col": label_col}


@pytest.fixture()
def small():
    """The canonical 6-node sheet (well under EXPLORE_THRESHOLD)."""
    data = h.seed()
    yield data
    frappe.set_user("Administrator")


@pytest.fixture()
def big():
    """A sheet OVER EXPLORE_THRESHOLD (root + THRESHOLD+10 children)."""
    data = _build_wide_sheet(EXPLORE_THRESHOLD + 10)
    yield data
    frappe.set_user("Administrator")


# ---------------------------------------------------------------------------
# count_nodes — cheap COUNT(*) matching list_nodes
# ---------------------------------------------------------------------------
def test_count_nodes_matches_list_nodes(big):
    repo = FrappeRepository()
    sheet = big["sheet"]
    assert repo.count_nodes(sheet) == len(repo.list_nodes(sheet))
    assert repo.count_nodes(sheet) == EXPLORE_THRESHOLD + 11  # root + children


# ---------------------------------------------------------------------------
# sheet_overview — always safe (no cells), correct structure
# ---------------------------------------------------------------------------
def test_overview_always_safe_on_huge_sheet(big):
    h.login_as("A")
    out = api.sheet_overview(sheet=big["sheet"])["data"]
    assert out["total_nodes"] == EXPLORE_THRESHOLD + 11
    assert out["root_node_ids"] == [big["root"]]
    assert out["max_depth"] == 1
    # top_branches are the root's direct children (label, no full cell payload).
    assert len(out["top_branches"]) == EXPLORE_THRESHOLD + 10
    assert all(set(b) == {"node", "label", "child_count"} for b in out["top_branches"])
    # column metadata only — no per-node values anywhere in the payload.
    assert all(set(c) >= {"name", "field", "label", "type", "column_owner"} for c in out["columns"])


# ---------------------------------------------------------------------------
# list_children — keyset pagination over real NestedSet lft
# ---------------------------------------------------------------------------
def test_list_children_paginates_exhaustively(big):
    h.login_as("A")
    sheet, root = big["sheet"], big["root"]
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        page = api.list_children(sheet=sheet, parent=root, cursor=cursor, limit=100)["data"]
        assert page["child_count"] == EXPLORE_THRESHOLD + 10  # total, not page size
        seen.extend(n["name"] for n in page["nodes"])
        pages += 1
        if not page["has_more"]:
            assert page["next_cursor"] is None
            break
        cursor = page["next_cursor"]
        assert cursor is not None
        assert pages < 20  # guard against a non-advancing cursor
    # exhaustive + no overlap + matches the true child set.
    assert len(seen) == EXPLORE_THRESHOLD + 10
    assert len(set(seen)) == len(seen)
    assert set(seen) == set(big["children"])


def test_list_children_clamps_limit(big):
    h.login_as("A")
    # limit over MAX_PAGE (200) is clamped; the call must still succeed and not
    # return more than the clamp.
    page = api.list_children(sheet=big["sheet"], parent=big["root"], limit=10_000)["data"]
    assert len(page["nodes"]) <= 200


def test_list_children_roots_when_parent_omitted(small):
    h.login_as("A")
    page = api.list_children(sheet=small["sheet"])["data"]  # parent omitted -> roots
    assert [n["name"] for n in page["nodes"]] == [small["nodes"]["R"]]
    assert page["nodes"][0]["child_count"] == 2  # P1, P2


# ---------------------------------------------------------------------------
# get_subtree — bounded depth window + keyset resume + breadcrumb
# ---------------------------------------------------------------------------
def test_get_subtree_depth_bounds_window():
    data = _build_deep_sheet(depth=5)
    h.login_as("A")
    sheet, chain = data["sheet"], data["chain"]
    # depth=2 from the root returns levels 0,1,2 only (3 nodes).
    out = api.get_subtree(sheet=sheet, node=chain[0], depth=2, limit=100)["data"]
    names = [n["name"] for n in out["nodes"]]
    assert names == chain[:3]
    assert out["has_more"] is False
    frappe.set_user("Administrator")


def test_get_subtree_keyset_resumes(big):
    h.login_as("A")
    sheet, root = big["sheet"], big["root"]
    # depth=1 from root = root + all children; page it small to force resume.
    seen: list[str] = []
    cursor = None
    while True:
        out = api.get_subtree(sheet=sheet, node=root, depth=1, cursor=cursor, limit=50)["data"]
        seen.extend(n["name"] for n in out["nodes"])
        if not out["has_more"]:
            break
        cursor = out["next_cursor"]
        assert cursor is not None
    # root + every child, exactly once.
    assert seen[0] == root
    assert set(seen) == {root} | set(big["children"])
    assert len(set(seen)) == len(seen)


def test_get_node_breadcrumb_path():
    data = _build_deep_sheet(depth=3)
    h.login_as("A")
    sheet, chain = data["sheet"], data["chain"]
    out = api.get_node(sheet=sheet, node=chain[-1])["data"]
    assert [p["name"] for p in out["path"]] == chain  # root..node inclusive
    assert out["parent"] == chain[-2]
    assert out["child_count"] == 0
    frappe.set_user("Administrator")


# ---------------------------------------------------------------------------
# search_nodes — substring over real values, paginated
# ---------------------------------------------------------------------------
def test_search_nodes_substring_paginates(big):
    h.login_as("A")
    sheet = big["sheet"]
    # every child label contains "child-"; the root ("ROOT") does not.
    seen: list[str] = []
    cursor = None
    while True:
        out = api.search_nodes(sheet=sheet, query="child-", cursor=cursor, limit=100)["data"]
        seen.extend(n["name"] for n in out["nodes"])
        if not out["has_more"]:
            break
        cursor = out["next_cursor"]
        assert cursor is not None
    assert set(seen) == set(big["children"])
    assert big["root"] not in seen


def test_search_nodes_scoped_to_column(small):
    h.login_as("A")
    # canonical label column is "title"-ish; scope to a known column and confirm
    # a label-only match is excluded when a specific column is given.
    label_field = next(iter(small["columns"]))
    out = api.search_nodes(
        sheet=small["sheet"], query="zzz-no-such-value", column=small["columns"][label_field]
    )["data"]
    assert out["nodes"] == []


# ---------------------------------------------------------------------------
# get_cells — sparse matrix + budget guard (4xx)
# ---------------------------------------------------------------------------
def test_get_cells_returns_sparse_matrix(small):
    h.login_as("A")
    sheet = small["sheet"]
    node = small["nodes"]["X"]
    cols = list(small["columns"].values())[:2]
    out = api.get_cells(sheet=sheet, nodes=[node], columns=cols)["data"]
    assert set(out["cells"]) == {node}
    assert set(out["cells"][node]) == set(cols)


def test_get_cells_over_budget_is_4xx(big):
    h.login_as("A")
    # len(nodes) * len(columns) > CELL_BUDGET must be refused as a 4xx, never 500.
    # The guard is a pre-fetch count check (len(nodes)*len(columns)), so cycle the
    # real children to exceed the budget (the wide sheet has a single column, and
    # only ~510 children — not enough on their own to reach 1000 cells).
    too_many = (big["children"] * 2)[: CELL_BUDGET + 1]
    assert len(too_many) == CELL_BUDGET + 1
    with pytest.raises(frappe.ValidationError):
        api.get_cells(sheet=big["sheet"], nodes=too_many, columns=[big["label_col"]])
    assert frappe.local.response.get("http_status_code") == 422


# ---------------------------------------------------------------------------
# The >500 guard — get_sheet_snapshot refuses over threshold (4xx, never 500)
# ---------------------------------------------------------------------------
def test_snapshot_over_threshold_is_4xx_not_500(big):
    h.login_as("A")
    with pytest.raises(frappe.ValidationError):
        api.get_sheet_snapshot(sheet=big["sheet"])
    # surfaced as a typed 4xx with the count/threshold + explore-tool hint.
    assert frappe.local.response.get("http_status_code") == 422
    hint = frappe.local.response.get("sheet_too_large")
    assert hint is not None
    assert hint["count"] == EXPLORE_THRESHOLD + 11
    assert hint["threshold"] == EXPLORE_THRESHOLD
    assert "getSheetOverview" in hint["explore_tools"]


def test_snapshot_at_threshold_still_serves():
    # A sheet AT the threshold (count == EXPLORE_THRESHOLD) is allowed: the guard
    # only trips STRICTLY above. root + (THRESHOLD-1) children == THRESHOLD nodes.
    data = _build_wide_sheet(EXPLORE_THRESHOLD - 1)
    h.login_as("A")
    repo = FrappeRepository()
    assert repo.count_nodes(data["sheet"]) == EXPLORE_THRESHOLD
    snap = api.get_sheet_snapshot(sheet=data["sheet"])
    assert len(snap["nodes"]) == EXPLORE_THRESHOLD
    frappe.set_user("Administrator")


def test_overview_works_where_snapshot_refuses(big):
    """The explore entrypoint stays available exactly where the snapshot refuses."""
    h.login_as("A")
    with pytest.raises(frappe.ValidationError):
        api.get_sheet_snapshot(sheet=big["sheet"])
    # same sheet, overview is fine.
    out = api.sheet_overview(sheet=big["sheet"])["data"]
    assert out["total_nodes"] == EXPLORE_THRESHOLD + 11
