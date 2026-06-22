"""Snapshot serializer shape (ARCHITECTURE §4.3) — the one shape web/api/agent
consume."""

from __future__ import annotations

from arbor.core.snapshot import serialize_snapshot
from tests.fixtures.canonical import B, seed_canonical_sheet


def _hints(fx):
    return {
        "actor": B,
        "can_edit_column": {fx.col_name: True, fx.col_notes: True, fx.col_status: False},
        "can_change_structure": {fx.X: False, fx.Y: False},
    }


def test_snapshot_top_level_shape():
    fx = seed_canonical_sheet()
    snap = serialize_snapshot(
        fx.repo.get_sheet(fx.sheet),
        fx.repo.list_columns(fx.sheet),
        fx.repo.list_nodes(fx.sheet),
        fx.repo.values,
        _hints(fx),
    )
    assert set(snap.keys()) == {"sheet", "columns", "nodes", "label_column", "actor", "viewer"}
    assert snap["sheet"]["name"] == fx.sheet
    assert snap["actor"] == B
    assert snap["label_column"] == fx.col_name


def test_snapshot_nodes_ordered_by_lft_with_labels_and_values():
    fx = seed_canonical_sheet()
    snap = serialize_snapshot(
        fx.repo.get_sheet(fx.sheet),
        fx.repo.list_columns(fx.sheet),
        fx.repo.list_nodes(fx.sheet),
        fx.repo.values,
        _hints(fx),
    )
    names = [n["name"] for n in snap["nodes"]]
    assert names[0] == fx.R  # root first (preorder)
    x = next(n for n in snap["nodes"] if n["name"] == fx.X)
    assert x["label"] == "Task X"
    assert x["values"][fx.col_budget] == 1000


def test_snapshot_emits_per_cell_pending_marks_sparsely():
    """``pending`` (keyed by (node, column)) surfaces open suggestions per cell.
    Sparse: a cell with no mark is simply absent from the node's pending map, so
    the FE lights the marker only where a Change Request actually targets."""
    fx = seed_canonical_sheet()
    mark = {"change_request": "CR-1", "requester": "dev@a.example", "value": "proposed"}
    snap = serialize_snapshot(
        fx.repo.get_sheet(fx.sheet),
        fx.repo.list_columns(fx.sheet),
        fx.repo.list_nodes(fx.sheet),
        fx.repo.values,
        _hints(fx),
        pending={(fx.X, fx.col_status): [mark]},
    )
    x = next(n for n in snap["nodes"] if n["name"] == fx.X)
    y = next(n for n in snap["nodes"] if n["name"] == fx.Y)
    assert x["pending"] == {fx.col_status: [mark]}  # only the targeted cell
    assert fx.col_budget not in x["pending"]  # untargeted cell absent (sparse)
    assert y["pending"] == {}  # untouched node → empty


def test_snapshot_pending_defaults_empty_when_omitted():
    fx = seed_canonical_sheet()
    snap = serialize_snapshot(
        fx.repo.get_sheet(fx.sheet),
        fx.repo.list_columns(fx.sheet),
        fx.repo.list_nodes(fx.sheet),
        fx.repo.values,
        _hints(fx),
    )
    assert all(n["pending"] == {} for n in snap["nodes"])


def test_snapshot_carries_acl_affordances():
    fx = seed_canonical_sheet()
    snap = serialize_snapshot(
        fx.repo.get_sheet(fx.sheet),
        fx.repo.list_columns(fx.sheet),
        fx.repo.list_nodes(fx.sheet),
        fx.repo.values,
        _hints(fx),
    )
    cols = {c["name"]: c for c in snap["columns"]}
    assert cols[fx.col_name]["can_edit"] is True
    assert cols[fx.col_status]["can_edit"] is False
    assert cols[fx.col_status]["editors"] == [B]
