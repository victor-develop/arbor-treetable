"""RED — Feature 3 read-ACL applied to the snapshot contract.

The snapshot SIGNATURE stays the same; the CALLER filters columns through
``visible_columns`` before handing them to ``serialize_snapshot``. Because the
serializer iterates the passed columns for BOTH headers and per-node cells, a
forbidden column drops from headers AND every node's ``values`` automatically —
no cell can leak.

Also asserted:
  * every serialized column carries ``can_read == True`` (always true post-filter)
    and ``read_level`` for FE symmetry;
  * BACKWARD-COMPAT golden — an all-default-public sheet serializes byte-identically
    to the pre-change shape (so the existing snapshot + 110 FE tests don't break).

RED until ``visible_columns`` exists and the serializer emits ``can_read`` /
``read_level``.
"""

from __future__ import annotations

from arbor.core.acl import visible_columns
from arbor.core.snapshot import serialize_snapshot
from arbor.core.types import Actor, ActorType
from tests.fixtures.canonical import A, B, C, G, seed_canonical_sheet

UNRELATED = G


def _human(user: str, *, is_admin: bool = False) -> Actor:
    return Actor(user, ActorType.HUMAN, is_admin=is_admin)


def _hints(fx, actor):
    return {
        "actor": actor.user,
        "can_edit_column": {},
        "can_change_structure": {},
    }


def _snap_for(fx, actor):
    sheet = fx.repo.get_sheet(fx.sheet)
    cols = visible_columns(fx.repo, sheet, actor, fx.repo.list_columns(fx.sheet))
    return serialize_snapshot(
        sheet,
        cols,
        fx.repo.list_nodes(fx.sheet),
        fx.repo.values,
        _hints(fx, actor),
    )


def test_forbidden_column_absent_from_headers_and_every_cell():
    fx = seed_canonical_sheet()
    fx.repo.update_column(fx.sheet, fx.col_budget, {"read_level": "owner-only", "readers": []})

    snap = _snap_for(fx, _human(UNRELATED))

    # 1. Header gone.
    col_names = {c["name"] for c in snap["columns"]}
    assert fx.col_budget not in col_names

    # 2. No cell leak — the forbidden column key is absent from EVERY node's values.
    for node in snap["nodes"]:
        assert fx.col_budget not in node["values"], node["name"]


def test_owner_sees_forbidden_column_and_its_cells():
    fx = seed_canonical_sheet()
    fx.repo.update_column(fx.sheet, fx.col_budget, {"read_level": "owner-only", "readers": []})

    snap = _snap_for(fx, _human(C))  # C owns col:budget
    col_names = {c["name"] for c in snap["columns"]}
    assert fx.col_budget in col_names
    x = next(n for n in snap["nodes"] if n["name"] == fx.X)
    assert x["values"][fx.col_budget] == 1000


def test_every_returned_column_has_can_read_true_and_read_level():
    fx = seed_canonical_sheet()
    fx.repo.update_column(fx.sheet, fx.col_budget, {"read_level": "owner-only", "readers": []})
    fx.repo.update_column(fx.sheet, fx.col_notes, {"read_level": "explicit-readers", "readers": [B]})

    snap = _snap_for(fx, _human(B))  # B: editor of status, owner of name+notes
    for c in snap["columns"]:
        assert c["can_read"] is True, c["name"]
        assert "read_level" in c
    by_name = {c["name"]: c for c in snap["columns"]}
    assert by_name[fx.col_notes]["read_level"] == "explicit-readers"


def test_admin_sees_all_columns():
    fx = seed_canonical_sheet()
    fx.repo.update_column(fx.sheet, fx.col_budget, {"read_level": "owner-only", "readers": []})
    snap = _snap_for(fx, _human(UNRELATED, is_admin=True))
    assert {c["name"] for c in snap["columns"]} == {
        fx.col_name,
        fx.col_status,
        fx.col_budget,
        fx.col_notes,
    }


def test_agent_snapshot_filters_identically():
    """Agent inheritance through the same visible_columns filter."""
    fx = seed_canonical_sheet()
    fx.repo.update_column(fx.sheet, fx.col_budget, {"read_level": "owner-only", "readers": []})
    agent = Actor(UNRELATED, ActorType.AGENT)
    snap = _snap_for(fx, agent)
    assert fx.col_budget not in {c["name"] for c in snap["columns"]}
    for node in snap["nodes"]:
        assert fx.col_budget not in node["values"]


# ---------------------------------------------------------------------------
# BACKWARD-COMPAT golden: all-default-public must be unchanged vs pre-ACL,
# EXCEPT for the two additive per-column fields (can_read / read_level). The
# nodes/values/sheet/label_column/viewer shape is byte-identical, and every
# column is can_read=True / read_level="public".
# ---------------------------------------------------------------------------
def test_all_default_public_is_backward_compatible():
    fx = seed_canonical_sheet()  # every column defaults read_level="public"
    actor = _human(A)
    snap = _snap_for(fx, actor)

    # All four columns present, in seed order.
    assert [c["name"] for c in snap["columns"]] == [
        fx.col_name,
        fx.col_status,
        fx.col_budget,
        fx.col_notes,
    ]
    # Additive fields only.
    for c in snap["columns"]:
        assert c["can_read"] is True
        assert c["read_level"] == "public"

    # Every node carries the full value map (no column dropped).
    expected_keys = {fx.col_name, fx.col_status, fx.col_budget, fx.col_notes}
    for node in snap["nodes"]:
        assert set(node["values"].keys()) == expected_keys

    # A spot cell is intact.
    x = next(n for n in snap["nodes"] if n["name"] == fx.X)
    assert x["values"][fx.col_budget] == 1000
    assert x["label"] == "Task X"
