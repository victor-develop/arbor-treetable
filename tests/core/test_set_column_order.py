"""setColumnOrder — the SHARED stored column order.

A viewer's drag reorders their own view (presentation state, no round-trip);
this capability is the explicit "save it for everyone", so the result has to be
real: every viewer, the REST API and every external agent read back the same
``list_columns`` order. That lives in the columns' ordering field, never in a
per-viewer overlay.

Runs against the in-memory oracle AND (re-collected by tests/standalone/sqlcore)
against the SQL repository — which is the point: the SQL lane's ``idx``
renumbering is only correct if it produces the same order the oracle does.
Columns seeded through ``add_column`` deliberately carry the ordering field's
DEFAULT in both lanes (the legacy state of every sheet created before ordering
existed), so the reorder path has to normalize before it assigns.
"""

from __future__ import annotations

import pytest

from arbor.core.executor import execute_action
from arbor.core.registry import get_capability
from arbor.core.testing import InMemoryRepository, RecordingEventSink
from arbor.core.types import Actor, ActorType

OWNER = "owner@example.com"
OUTSIDER = "nobody@example.com"


def _sheet_with(*fields: str):
    """A sheet owned by OWNER whose columns were all seeded (ordering field at
    its default), plus the actor/sink the executor needs."""
    repo = InMemoryRepository()
    repo.add_sheet("S", structural_owner=OWNER)
    repo.add_column("c-title", "S", "title", OWNER, is_label=True)
    for f in fields:
        repo.add_column(f"c-{f}", "S", f, OWNER)
    return repo, RecordingEventSink(), Actor(OWNER, ActorType.HUMAN)


def _reorder(repo, sink, actor, order: list[str]):
    return execute_action("setColumnOrder", {"sheet": "S", "order": order}, actor, repo, sink)


def _fields(repo) -> list[str]:
    return [c.field for c in repo.list_columns("S")]


# --- the schema advertises the contract (skill.md ships it verbatim) --------
def test_the_schema_documents_the_completeness_requirement():
    schema = get_capability("setColumnOrder").params_schema
    assert schema["required"] == ["sheet", "order"]
    desc = schema["properties"]["order"]["description"]
    # skill.md dumps the schema verbatim, so the description IS the agent's doc:
    # it must say the list is complete, that a bad list is a 400 (not a
    # suggestion), and that the label column is not an entry.
    assert "COMPLETE" in desc
    assert "exactly once" in desc
    assert "400" in desc
    assert "label column" in desc


def test_it_is_a_meta_column_schema_update_exposed_to_the_agent():
    from arbor.core.types import Axis, Operation, TargetKind

    cap = get_capability("setColumnOrder")
    assert cap.axis == Axis.META
    assert cap.target_kind == TargetKind.COLUMN_SCHEMA
    assert cap.operation == Operation.UPDATE
    assert cap.emits == ("COLUMN_CONFIG_UPDATED",)
    # An agent may reorder too, and the pre-pass is wired (not just the handler).
    assert cap.is_exposed_to_llm is True
    assert cap.resolve_params is not None


# --- the order is applied and read back -------------------------------------
def test_the_new_order_is_stored_and_read_back():
    repo, sink, actor = _sheet_with("status", "due", "risk")
    out = _reorder(repo, sink, actor, ["risk", "status", "due"])
    assert out.kind == "executed"  # OWNER is the structural owner — a real write
    # The label column keeps the leading slot: it is always the first column.
    assert _fields(repo) == ["title", "risk", "status", "due"]
    assert sink.events[-1].type == "COLUMN_CONFIG_UPDATED"
    assert sink.events[-1].payload["op"] == "reorder"


def test_order_accepts_column_ids_as_well_as_field_keys():
    repo, sink, actor = _sheet_with("status", "due")
    # The grid holds column IDs; the LLM contract only ever sees field keys.
    ids = [repo.get_column("S", f).name for f in ("due", "status")]
    _reorder(repo, sink, actor, ids)
    assert _fields(repo) == ["title", "due", "status"]


def test_reordering_twice_is_stable():
    repo, sink, actor = _sheet_with("a", "b", "c")
    _reorder(repo, sink, actor, ["c", "b", "a"])
    _reorder(repo, sink, actor, ["b", "a", "c"])
    assert _fields(repo) == ["title", "b", "a", "c"]


def test_the_same_order_again_is_a_no_op_not_a_scramble():
    repo, sink, actor = _sheet_with("a", "b", "c")
    _reorder(repo, sink, actor, ["a", "b", "c"])
    assert _fields(repo) == ["title", "a", "b", "c"]


# --- validation --------------------------------------------------------------
def test_an_unknown_column_is_a_validation_error():
    repo, sink, actor = _sheet_with("status", "due")
    with pytest.raises(ValueError):
        _reorder(repo, sink, actor, ["due", "nope"])
    assert _fields(repo) == ["title", "status", "due"]
    assert repo.change_requests == {}


def test_a_duplicate_entry_is_a_validation_error():
    repo, sink, actor = _sheet_with("status", "due")
    with pytest.raises(ValueError):
        _reorder(repo, sink, actor, ["due", "due", "status"])
    assert _fields(repo) == ["title", "status", "due"]


def test_an_incomplete_list_is_a_validation_error():
    # Deliberate: a partial list has no single obvious meaning (prepend? append?
    # leave the rest where they are?), so the contract requires the whole set.
    repo, sink, actor = _sheet_with("status", "due", "risk")
    with pytest.raises(ValueError):
        _reorder(repo, sink, actor, ["risk", "status"])
    assert _fields(repo) == ["title", "status", "due", "risk"]


def test_naming_the_label_column_is_a_validation_error():
    # The label column is always the FIRST grid column and never reorderable;
    # rejecting it is the honest answer (the frontend never sends it either).
    repo, sink, actor = _sheet_with("status", "due")
    with pytest.raises(ValueError):
        _reorder(repo, sink, actor, ["title", "status", "due"])
    assert _fields(repo) == ["title", "status", "due"]


def test_a_column_of_another_sheet_is_a_validation_error():
    repo, sink, actor = _sheet_with("status")
    repo.add_sheet("OTHER", structural_owner=OWNER)
    foreign = repo.create_column("OTHER", {"field": "elsewhere", "label": "Elsewhere"})
    with pytest.raises(ValueError):
        _reorder(repo, sink, actor, [foreign])
    assert _fields(repo) == ["title", "status"]


def test_an_empty_order_on_a_sheet_with_data_columns_is_incomplete():
    repo, sink, actor = _sheet_with("status")
    with pytest.raises(ValueError):
        _reorder(repo, sink, actor, [])
    assert _fields(repo) == ["title", "status"]


def test_an_empty_order_is_fine_on_a_sheet_with_no_data_columns():
    # Degenerate but legal: nothing to order, so the empty list IS complete.
    repo, sink, actor = _sheet_with()
    out = _reorder(repo, sink, actor, [])
    assert out.kind == "executed"
    assert _fields(repo) == ["title"]


# --- validation applies to the SUGGEST branch too ----------------------------
# `order` is resolved BEFORE authorize-or-suggest, so an unauthorized caller
# gets the same 400 an owner does. Validating inside the handler (which only
# runs once authorized) would let a non-owner file a Change Request whose
# approval raised forever: 400 on every retry, CR pinned in PROPOSED, Reject the
# only way out.
def test_an_unauthorized_caller_cannot_suggest_a_bad_order():
    repo, sink, _ = _sheet_with("status", "due")
    outsider = Actor(OUTSIDER, ActorType.HUMAN)
    with pytest.raises(ValueError):
        execute_action(
            "setColumnOrder", {"sheet": "S", "order": ["due", "nope"]}, outsider, repo, sink
        )
    assert _fields(repo) == ["title", "status", "due"]
    assert repo.change_requests == {}


def test_a_non_owner_reorder_degrades_to_a_change_request_and_applies_on_approval():
    repo, sink, owner = _sheet_with("status", "due")
    outsider = Actor(OUTSIDER, ActorType.HUMAN)
    out = execute_action(
        "setColumnOrder", {"sheet": "S", "order": ["due", "status"]}, outsider, repo, sink
    )
    assert out.kind == "suggested"
    assert out.resolved_approver == OWNER
    # Nothing moved while the CR is pending.
    assert _fields(repo) == ["title", "status", "due"]
    # The CR carries RESOLVED ids, not the field keys the caller sent — replay
    # never has to re-resolve a spelling.
    stored = repo.change_requests[out.change_request]["payload"]["order"]
    assert stored == [repo.get_column("S", f).name for f in ("due", "status")]
    execute_action("approveChange", {"change_request": out.change_request}, owner, repo, sink)
    assert _fields(repo) == ["title", "due", "status"]


def test_approving_an_order_whose_column_was_deleted_still_applies():
    # A CR is applied later than it is filed, and replay SKIPS the pre-pass. If a
    # vanished column raised at approval time the CR would be unapprovable, so
    # the stale entry is dropped and the rest of the arrangement still lands.
    repo, sink, owner = _sheet_with("status", "due", "risk")
    outsider = Actor(OUTSIDER, ActorType.HUMAN)
    out = execute_action(
        "setColumnOrder", {"sheet": "S", "order": ["risk", "due", "status"]}, outsider, repo, sink
    )
    assert out.kind == "suggested"
    execute_action("deleteColumn", {"sheet": "S", "column": "due"}, owner, repo, sink)
    execute_action("approveChange", {"change_request": out.change_request}, owner, repo, sink)
    assert _fields(repo) == ["title", "risk", "status"]


def test_approving_an_order_that_predates_a_new_column_keeps_the_newcomer():
    # The mirror case: a column added between propose and approve is not named
    # in the CR at all. It must survive (appended after the named ones), not
    # vanish and not wedge the approval.
    repo, sink, owner = _sheet_with("status", "due")
    outsider = Actor(OUTSIDER, ActorType.HUMAN)
    out = execute_action(
        "setColumnOrder", {"sheet": "S", "order": ["due", "status"]}, outsider, repo, sink
    )
    assert out.kind == "suggested"
    execute_action(
        "addColumn", {"sheet": "S", "field": "risk", "label": "Risk", "type": "text"}, owner, repo, sink
    )
    execute_action("approveChange", {"change_request": out.change_request}, owner, repo, sink)
    assert _fields(repo) == ["title", "due", "status", "risk"]


# --- composition with addColumn.after ---------------------------------------
def test_addColumn_after_positions_against_the_REORDERED_order():
    # The two features must compose: after a reorder, "insert to the right of X"
    # means the right of X's NEW slot. If the reorder left stale positions
    # behind, this lands in the wrong place.
    repo, sink, actor = _sheet_with("status", "due", "risk")
    _reorder(repo, sink, actor, ["risk", "status", "due"])
    execute_action(
        "addColumn",
        {"sheet": "S", "field": "notes", "label": "Notes", "type": "text", "after": "risk"},
        actor,
        repo,
        sink,
    )
    assert _fields(repo) == ["title", "risk", "notes", "status", "due"]


def test_a_reorder_after_an_insert_also_holds():
    repo, sink, actor = _sheet_with("status", "due")
    execute_action(
        "addColumn",
        {"sheet": "S", "field": "notes", "label": "Notes", "type": "text", "after": "status"},
        actor,
        repo,
        sink,
    )
    assert _fields(repo) == ["title", "status", "notes", "due"]
    _reorder(repo, sink, actor, ["due", "notes", "status"])
    assert _fields(repo) == ["title", "due", "notes", "status"]


# --- migration: columns that predate positioning -----------------------------
def test_reordering_a_legacy_sheet_whose_columns_share_the_default_position():
    # Every column here was seeded, i.e. shares the ordering field's default —
    # exactly a sheet created before ordering existed. The reorder has to
    # renumber the whole sheet, not position against a wall of equal values.
    repo, sink, actor = _sheet_with("status", "due", "risk", "notes")
    assert _fields(repo) == ["title", "status", "due", "risk", "notes"]
    _reorder(repo, sink, actor, ["notes", "risk", "due", "status"])
    assert _fields(repo) == ["title", "notes", "risk", "due", "status"]


def test_a_legacy_sheet_reorder_survives_a_later_partial_rearrangement():
    repo, sink, actor = _sheet_with("a", "b", "c", "d")
    _reorder(repo, sink, actor, ["d", "c", "b", "a"])
    # Move just "b" to the front; everything else keeps its relative order.
    _reorder(repo, sink, actor, ["b", "d", "c", "a"])
    assert _fields(repo) == ["title", "b", "d", "c", "a"]
