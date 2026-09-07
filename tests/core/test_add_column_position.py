"""addColumn ``after`` — the STORED insert-to-the-right position.

The grid's per-header "+" means "insert a column right of THIS one", and the
resulting order has to be real: everyone (API + external agents included) reads
back the same ``list_columns`` order, so the position lives in the columns'
ordering field, not in a per-viewer view overlay.

Runs against the in-memory oracle AND (re-collected by
tests/standalone/sqlcore) against the SQL repository, which is the point: the
SQL lane's ``idx`` normalization is only correct if it produces the same order
the oracle does. Columns seeded through ``add_column`` deliberately carry the
ordering field's DEFAULT in both lanes — the legacy state of every sheet
created before this feature — so the last test here is the migration proof.
"""

from __future__ import annotations

import pytest

from arbor.core.executor import execute_action
from arbor.core.registry import get_capability
from arbor.core.testing import InMemoryRepository, RecordingEventSink
from arbor.core.types import Actor, ActorType

OWNER = "owner@example.com"


def _sheet_with(*fields: str):
    """A sheet owned by OWNER whose columns were all seeded (ordering field at
    its default), plus the actor/sink the executor needs."""
    repo = InMemoryRepository()
    repo.add_sheet("S", structural_owner=OWNER)
    repo.add_column("c-title", "S", "title", OWNER, is_label=True)
    for f in fields:
        repo.add_column(f"c-{f}", "S", f, OWNER)
    return repo, RecordingEventSink(), Actor(OWNER, ActorType.HUMAN)


def _add(repo, sink, actor, field: str, after: str | None = None):
    params = {"sheet": "S", "field": field, "label": field.title(), "type": "text"}
    if after is not None:
        params["after"] = after
    out = execute_action("addColumn", params, actor, repo, sink)
    assert out.kind == "executed"  # OWNER is the structural owner — a real write
    return out.data["column"]


def _fields(repo) -> list[str]:
    return [c.field for c in repo.list_columns("S")]


# --- the schema advertises the parameter (skill.md is generated from it) -----
def test_after_is_an_optional_string_param():
    props = get_capability("addColumn").params_schema["properties"]
    assert props["after"]["type"] == ["string", "null"]
    assert "after" not in get_capability("addColumn").params_schema["required"]
    # skill.md dumps the schema verbatim, so the description IS the agent's doc.
    assert "immediately to the RIGHT" in props["after"]["description"]


# --- append (today's behavior) ----------------------------------------------
def test_no_after_appends_last():
    repo, sink, actor = _sheet_with("status")
    _add(repo, sink, actor, "owner_name")
    _add(repo, sink, actor, "due")
    assert _fields(repo) == ["title", "status", "owner_name", "due"]


def test_after_the_last_column_is_also_an_append():
    repo, sink, actor = _sheet_with("status", "due")
    _add(repo, sink, actor, "notes", after="due")
    assert _fields(repo) == ["title", "status", "due", "notes"]


# --- insert ------------------------------------------------------------------
def test_after_the_first_column_lands_second():
    repo, sink, actor = _sheet_with("status", "due")
    _add(repo, sink, actor, "notes", after="title")
    assert _fields(repo) == ["title", "notes", "status", "due"]


def test_after_a_middle_column_lands_immediately_right_of_it():
    repo, sink, actor = _sheet_with("status", "due", "risk")
    _add(repo, sink, actor, "notes", after="status")
    assert _fields(repo) == ["title", "status", "notes", "due", "risk"]


def test_after_accepts_the_column_id_as_well_as_the_field_key():
    repo, sink, actor = _sheet_with("status", "due")
    # The grid holds column IDs; the LLM contract only ever sees field keys.
    anchor = repo.get_column("S", "status").name
    _add(repo, sink, actor, "notes", after=anchor)
    assert _fields(repo) == ["title", "status", "notes", "due"]


def test_repeated_inserts_at_the_same_anchor_stack_rightwards():
    repo, sink, actor = _sheet_with("status", "due")
    _add(repo, sink, actor, "a", after="status")
    _add(repo, sink, actor, "b", after="status")
    # Each insert takes the slot directly after the anchor, pushing the previous
    # one right — the same result as inserting twice in a spreadsheet.
    assert _fields(repo) == ["title", "status", "b", "a", "due"]


# --- validation --------------------------------------------------------------
def test_unknown_after_is_a_validation_error_not_a_change_request():
    repo, sink, actor = _sheet_with("status")
    with pytest.raises(ValueError):
        _add(repo, sink, actor, "notes", after="nope")
    # Nothing was written and nothing was suggested.
    assert _fields(repo) == ["title", "status"]
    assert repo.change_requests == {}


def test_after_a_column_of_another_sheet_is_a_validation_error():
    repo, sink, actor = _sheet_with("status")
    repo.add_sheet("OTHER", structural_owner=OWNER)
    foreign = repo.create_column("OTHER", {"field": "elsewhere", "label": "Elsewhere"})
    with pytest.raises(ValueError):
        _add(repo, sink, actor, "notes", after=foreign)
    assert _fields(repo) == ["title", "status"]


# --- migration: columns that predate positioning -----------------------------
def test_inserting_into_a_legacy_sheet_keeps_the_existing_order_intact():
    # Every column here was seeded, i.e. shares the ordering field's default —
    # exactly a sheet created before ``after`` existed. Inserting must renumber
    # them in their CURRENT order first, or live sheets scramble.
    repo, sink, actor = _sheet_with("status", "due", "risk", "notes")
    assert _fields(repo) == ["title", "status", "due", "risk", "notes"]
    _add(repo, sink, actor, "owner_name", after="due")
    assert _fields(repo) == ["title", "status", "due", "owner_name", "risk", "notes"]
