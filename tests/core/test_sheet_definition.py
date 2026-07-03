"""getSheetDefinition — the cheap schema/config (governance) read.

A LEAN read of a sheet's DEFINITION (governance + schema) with NO row/cell data:
the sheet block (name/title/structural_owner/label_column/settings), the columns
(id + field + label + type + owner + editors + is_label + options + can_edit),
and the process block (enabled/row_scope/rules) or None.

Columns are read-ACL FILTERED via the SAME ``acl.visible_columns`` rule the
snapshot + explore surface use, so a viewer who cannot read a column never sees
it in the definition. It carries NO node/cell VALUES at all.

These tests drive the pure ``explore.sheet_definition(repo, sheet, actor)``
directly (the single source of truth both the human panel and the LLM agent read
through the executor / REST shim).
"""

from __future__ import annotations

from arbor.core import explore
from arbor.core.types import Actor, ActorType
from tests.fixtures.canonical import A, B, C, E, G, seed_canonical_sheet

UNRELATED = G


def _human(user: str, *, is_admin: bool = False) -> Actor:
    return Actor(user, ActorType.HUMAN, is_admin=is_admin)


def _lock_budget_owner_only(fx) -> None:
    fx.repo.update_column(fx.sheet, fx.col_budget, {"read_level": "owner-only", "readers": []})


# ---------------------------------------------------------------------------
# The sheet block: name/title/structural_owner/label_column/settings.
# ---------------------------------------------------------------------------
def test_sheet_block_carries_governance_not_rows():
    fx = seed_canonical_sheet(settings={"owners_must_use_change_requests": True})
    d = explore.sheet_definition(fx.repo, fx.sheet, actor=_human(A))
    sheet = d["sheet"]
    assert sheet["name"] == fx.sheet
    assert sheet["structural_owner"] == A
    assert sheet["label_column"] == fx.col_name  # the is_label column
    assert sheet["settings"] == {"owners_must_use_change_requests": True}
    assert "title" in sheet  # present (falls back to name when unset)
    # NO row/cell payload anywhere in the definition.
    assert "nodes" not in d
    assert "values" not in d
    assert "total_nodes" not in d


# ---------------------------------------------------------------------------
# columns: id + field + label + type + owner + editors + is_label + can_edit.
# NO cell values.
# ---------------------------------------------------------------------------
def test_columns_carry_id_label_owner_type_editors_no_values():
    fx = seed_canonical_sheet()
    d = explore.sheet_definition(fx.repo, fx.sheet, actor=_human(A))
    by_id = {c["name"]: c for c in d["columns"]}
    status = by_id[fx.col_status]
    assert status["name"] == fx.col_status  # the ID
    assert status["field"] == "status"
    assert status["type"] == "single-select-split"
    assert status["column_owner"] == C
    assert status["editors"] == [B]
    assert status["is_label"] is False
    # label column flagged
    assert by_id[fx.col_name]["is_label"] is True
    # NEVER a cell value on a column entry.
    for c in d["columns"]:
        assert "value" not in c
        assert "values" not in c


def test_can_edit_reflects_column_approvers():
    fx = seed_canonical_sheet()
    # C owns col:budget + col:status; B owns col:name + col:notes and edits status.
    d_c = {c["name"]: c for c in explore.sheet_definition(fx.repo, fx.sheet, actor=_human(C))["columns"]}
    assert d_c[fx.col_budget]["can_edit"] is True
    assert d_c[fx.col_status]["can_edit"] is True
    assert d_c[fx.col_notes]["can_edit"] is False  # C is not an approver of notes

    d_b = {c["name"]: c for c in explore.sheet_definition(fx.repo, fx.sheet, actor=_human(B))["columns"]}
    assert d_b[fx.col_status]["can_edit"] is True  # B is an editor of status
    assert d_b[fx.col_budget]["can_edit"] is False


# ---------------------------------------------------------------------------
# read-ACL filtered: a viewer who cannot read a column does not see it.
# ---------------------------------------------------------------------------
def test_forbidden_column_omitted_from_definition():
    fx = seed_canonical_sheet()
    _lock_budget_owner_only(fx)
    d_out = explore.sheet_definition(fx.repo, fx.sheet, actor=_human(UNRELATED))
    assert fx.col_budget not in {c["name"] for c in d_out["columns"]}
    # owner still sees it
    d_owner = explore.sheet_definition(fx.repo, fx.sheet, actor=_human(C))
    assert fx.col_budget in {c["name"] for c in d_owner["columns"]}


def test_admin_sees_all_columns():
    fx = seed_canonical_sheet()
    _lock_budget_owner_only(fx)
    d = explore.sheet_definition(fx.repo, fx.sheet, actor=_human(UNRELATED, is_admin=True))
    assert fx.col_budget in {c["name"] for c in d["columns"]}


def test_explicit_reader_sees_column_in_definition():
    fx = seed_canonical_sheet()
    fx.repo.update_column(fx.sheet, fx.col_budget, {"read_level": "explicit-readers", "readers": [E]})
    d_reader = explore.sheet_definition(fx.repo, fx.sheet, actor=_human(E))
    assert fx.col_budget in {c["name"] for c in d_reader["columns"]}
    d_outsider = explore.sheet_definition(fx.repo, fx.sheet, actor=_human(UNRELATED))
    assert fx.col_budget not in {c["name"] for c in d_outsider["columns"]}


# ---------------------------------------------------------------------------
# process block: None when no process; the rule view when one is defined; the
# process rule LABELS are read-ACL redacted like get_process.
# ---------------------------------------------------------------------------
def test_process_block_none_when_no_process():
    fx = seed_canonical_sheet()
    d = explore.sheet_definition(fx.repo, fx.sheet, actor=_human(A))
    assert d["process"] is None


def _define_process(fx) -> None:
    fx.repo.upsert_process(
        {
            "sheet": fx.sheet,
            "title": "Intake",
            "row_scope": "root-children",
            "rules": [
                {"trigger_kind": "row", "trigger_op": "created", "expected_columns": [fx.col_status]},
                {
                    "trigger_kind": "column",
                    "trigger_column": fx.col_status,
                    "trigger_op": "updated",
                    "expected_columns": [fx.col_budget],
                    "within_seconds": 3600,
                },
            ],
        }
    )


def test_process_block_carries_rules_and_scope():
    fx = seed_canonical_sheet()
    _define_process(fx)
    d = explore.sheet_definition(fx.repo, fx.sheet, actor=_human(C))
    proc = d["process"]
    assert proc is not None
    assert proc["row_scope"] == "root-children"
    assert proc["enabled"] is False
    assert len(proc["rules"]) == 2
    r1 = proc["rules"][1]
    assert r1["trigger_column"] == fx.col_status
    assert r1["expected_columns"] == [fx.col_budget]
    # LIVE-resolved owner of the expected column (C owns budget).
    assert r1["expected_owners"][fx.col_budget] == C


def test_process_rule_labels_redacted_for_forbidden_column():
    fx = seed_canonical_sheet()
    _define_process(fx)
    _lock_budget_owner_only(fx)  # budget now unreadable to UNRELATED
    d = explore.sheet_definition(fx.repo, fx.sheet, actor=_human(UNRELATED))
    proc = d["process"]
    r1 = proc["rules"][1]
    # the expected budget column key + label are redacted (None), never leaked.
    assert r1["expected_columns"] == [None]
    assert r1["expected_labels"] == [None]
