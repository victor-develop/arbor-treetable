"""normalize_select_options — the write-path guard that keeps stored select
options in the ONE canonical shape ``{"groups": [{"label", "options"}]}``.

Regression anchor (2026-09-01, live): the LLM agent created a column with
``options={"choices": [...]}`` — the loose addColumn schema admitted it, both
adapters stored it raw, and the grid's Live view crashed on ``options.groups``.
"""

from __future__ import annotations

from arbor.core.handlers import normalize_select_options


def test_none_passes_through():
    assert normalize_select_options(None) is None


def test_canonical_shape_is_kept_and_cleaned():
    out = normalize_select_options(
        {"groups": [{"label": "Stage", "options": ["todo", "done", 3]}, {"bad": True}]}
    )
    assert out == {"groups": [{"label": "Stage", "options": ["todo", "done", "3"]}]}


def test_llm_choices_shape_is_normalized():
    out = normalize_select_options({"choices": ["todo", "doing", "done"]})
    assert out == {"groups": [{"label": "Options", "options": ["todo", "doing", "done"]}]}


def test_options_and_values_keys_normalize_too():
    for key in ("options", "values"):
        out = normalize_select_options({key: ["a", "b"]})
        assert out == {"groups": [{"label": "Options", "options": ["a", "b"]}]}


def test_bare_list_is_normalized():
    out = normalize_select_options(["a", "b"])
    assert out == {"groups": [{"label": "Options", "options": ["a", "b"]}]}


def test_junk_shapes_fall_back_to_none():
    assert normalize_select_options("todo,done") is None
    assert normalize_select_options({"foo": "bar"}) is None
    assert normalize_select_options({"groups": "nope"}) is None
    assert normalize_select_options([]) is None


def test_add_column_stores_normalized_options():
    # Through the real handler + InMemory repo: the stored spec is canonical.
    from arbor.core.handlers import add_column_handler
    from arbor.core.testing import InMemoryRepository
    from arbor.core.types import Actor, ActorType

    repo = InMemoryRepository()
    repo.add_sheet("S", structural_owner="a@x.com")
    actor = Actor(user="a@x.com", actor_type=ActorType.HUMAN)
    res = add_column_handler(
        {"sheet": "S", "field": "status", "label": "Status", "type": "single-select-split",
         "options": {"choices": ["todo", "done"]}},
        actor,
        repo,
    )
    col = repo.get_column("S", res.data["column"])
    assert col is not None
    stored = getattr(col, "options", None)
    assert stored == {"groups": [{"label": "Options", "options": ["todo", "done"]}]}
