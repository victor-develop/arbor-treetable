"""FEATURE 1 — optimistic concurrency (no lost update on save).

TESTS FIRST (RED). The contract (authz_tdd.txt FEATURE 1 + authz_spec.txt
CONCURRENCY WIRING):

(a) InMemoryRepository.set_value honours an ``expected_version`` guard:
    - matching version  -> succeeds, bumps to N+1
    - mismatched        -> raises StaleVersionError (carrying current_version +
                           current_value so the seam can build the conflict
                           payload without a second read)
    - expected_version=0 on a MISSING cell -> creates v1
    - non-zero expected on a MISSING cell  -> raises
    - two first-writers to an empty cell: first creates v1, the second (base 0)
      sees v1 and goes stale.
(b) update_cell_handler THREADS base_version into set_value; omitting it keeps
    today's no-check (opt-in) behavior. A successful write returns the new
    version in HandlerResult.data so the FE can fold it back.
(c) serialize_snapshot emits a parallel ``versions`` map per node (0 for an
    empty cell) WITHOUT changing the existing ``values`` dict (byte-identical
    golden, so the existing snapshot + FE tests don't break).
(d) move_node_handler threads ``expected_revision`` into repo.move_node so a
    vanished anchor sibling raises StaleMoveError.
"""

from __future__ import annotations

import copy

import pytest

from arbor.core import handlers
from arbor.core.snapshot import serialize_snapshot
from arbor.core.testing import InMemoryRepository
from arbor.core.types import Actor, StaleVersionError
from tests.fixtures.canonical import A, B, seed_canonical_sheet


# ---------------------------------------------------------------------------
# (a) InMemoryRepository.set_value expected_version semantics
# ---------------------------------------------------------------------------
def test_set_value_matching_version_bumps_to_n_plus_1():
    fx = seed_canonical_sheet()
    repo = fx.repo
    # canonical seed gives col_status@X = v1 (seed_value sets version 1)
    assert repo.versions[(fx.X, fx.col_status)] == 1
    new = repo.set_value(fx.sheet, fx.X, fx.col_status, "doing", expected_version=1)
    assert new == 2
    assert repo.versions[(fx.X, fx.col_status)] == 2
    assert repo.get_value(fx.X, fx.col_status) == "doing"


def test_set_value_mismatched_version_raises_stale_with_current_state():
    fx = seed_canonical_sheet()
    repo = fx.repo  # col_status@X is at v1
    with pytest.raises(StaleVersionError) as ei:
        repo.set_value(fx.sheet, fx.X, fx.col_status, "doing", expected_version=99)
    # the error carries the authoritative current state for the conflict payload
    assert ei.value.current_version == 1
    assert ei.value.current_value == "todo"
    # the rejected write must NOT have landed
    assert repo.get_value(fx.X, fx.col_status) == "todo"
    assert repo.versions[(fx.X, fx.col_status)] == 1


def test_set_value_expected_zero_on_missing_cell_creates_v1():
    fx = seed_canonical_sheet()
    repo = fx.repo
    key = (fx.X, fx.col_notes)
    assert key not in repo.versions  # never written
    new = repo.set_value(fx.sheet, fx.X, fx.col_notes, "hello", expected_version=0)
    assert new == 1
    assert repo.get_value(fx.X, fx.col_notes) == "hello"


def test_set_value_nonzero_expected_on_missing_cell_raises():
    fx = seed_canonical_sheet()
    repo = fx.repo
    with pytest.raises(StaleVersionError) as ei:
        repo.set_value(fx.sheet, fx.X, fx.col_notes, "hello", expected_version=3)
    # missing cell -> current version is 0, current value None
    assert ei.value.current_version == 0
    assert ei.value.current_value is None
    assert repo.get_value(fx.X, fx.col_notes) is None


def test_two_first_writers_second_goes_stale():
    fx = seed_canonical_sheet()
    repo = fx.repo
    key = (fx.X, fx.col_notes)
    assert key not in repo.versions
    # first writer: base 0 succeeds, creates v1
    assert repo.set_value(fx.sheet, fx.X, fx.col_notes, "first", expected_version=0) == 1
    # second writer raced with the SAME base 0 -> now sees v1 -> stale
    with pytest.raises(StaleVersionError) as ei:
        repo.set_value(fx.sheet, fx.X, fx.col_notes, "second", expected_version=0)
    assert ei.value.current_version == 1
    assert ei.value.current_value == "first"
    # the first write stands
    assert repo.get_value(fx.X, fx.col_notes) == "first"


def test_set_value_omitted_expected_version_never_checks():
    fx = seed_canonical_sheet()
    repo = fx.repo  # col_status@X v1
    # no expected_version -> blind overwrite (today's behavior), still bumps
    assert repo.set_value(fx.sheet, fx.X, fx.col_status, "blind") == 2
    assert repo.get_value(fx.X, fx.col_status) == "blind"


# ---------------------------------------------------------------------------
# (b) update_cell_handler threads base_version (opt-in)
# ---------------------------------------------------------------------------
def test_update_cell_handler_threads_base_version_match():
    fx = seed_canonical_sheet()
    actor = Actor(user=B)
    result = handlers.update_cell_handler(
        {"sheet": fx.sheet, "node": fx.X, "column": fx.col_status,
         "value": "doing", "base_version": 1},
        actor,
        fx.repo,
    )
    assert result.data["version"] == 2
    assert fx.repo.get_value(fx.X, fx.col_status) == "doing"


def test_update_cell_handler_base_version_mismatch_raises_stale():
    fx = seed_canonical_sheet()
    actor = Actor(user=B)
    with pytest.raises(StaleVersionError) as ei:
        handlers.update_cell_handler(
            {"sheet": fx.sheet, "node": fx.X, "column": fx.col_status,
             "value": "doing", "base_version": 0},
            actor,
            fx.repo,
        )
    assert ei.value.current_version == 1
    assert ei.value.current_value == "todo"
    # rejected -> unchanged
    assert fx.repo.get_value(fx.X, fx.col_status) == "todo"


def test_update_cell_handler_omitted_base_version_is_opt_in_no_check():
    fx = seed_canonical_sheet()
    actor = Actor(user=B)
    # bump out-of-band so any latent check would mismatch a stale base
    fx.repo.set_value(fx.sheet, fx.X, fx.col_status, "x")  # now v2
    # handler called WITHOUT base_version -> no check, succeeds (today's behavior)
    result = handlers.update_cell_handler(
        {"sheet": fx.sheet, "node": fx.X, "column": fx.col_status, "value": "doing"},
        actor,
        fx.repo,
    )
    assert result.data["version"] == 3
    assert fx.repo.get_value(fx.X, fx.col_status) == "doing"


# ---------------------------------------------------------------------------
# (c) snapshot emits a parallel versions map; values byte-identical golden
# ---------------------------------------------------------------------------
def _hints(fx):
    return {
        "actor": B,
        "can_edit_column": {fx.col_name: True, fx.col_notes: True, fx.col_status: False},
        "can_change_structure": {fx.X: False, fx.Y: False},
    }


def _snapshot(fx, versions=None):
    return serialize_snapshot(
        fx.repo.get_sheet(fx.sheet),
        fx.repo.list_columns(fx.sheet),
        fx.repo.list_nodes(fx.sheet),
        fx.repo.values,
        _hints(fx),
        versions=versions,
    )


def test_snapshot_emits_parallel_versions_map():
    fx = seed_canonical_sheet()
    snap = _snapshot(fx, versions=fx.repo.versions)
    x = next(n for n in snap["nodes"] if n["name"] == fx.X)
    # populated cells carry their stored version (seed -> v1)
    assert x["versions"][fx.col_status] == 1
    assert x["versions"][fx.col_budget] == 1
    # empty cell -> 0 (parallel to values, never absent)
    assert x["versions"][fx.col_notes] == 0
    # the versions map covers exactly the same columns as values
    assert set(x["versions"].keys()) == set(x["values"].keys())


def test_snapshot_versions_default_all_zero_when_param_omitted():
    fx = seed_canonical_sheet()
    snap = _snapshot(fx, versions=None)
    x = next(n for n in snap["nodes"] if n["name"] == fx.X)
    # versions param omitted -> a safe all-zero map (still present for FE symmetry)
    assert x["versions"][fx.col_status] == 0
    assert set(x["versions"].keys()) == set(x["values"].keys())


def test_snapshot_values_byte_identical_to_pre_feature(monkeypatch):
    """The existing ``values`` dict + every other key must be byte-identical
    whether or not the new ``versions`` param is supplied (golden — protects the
    existing snapshot + 110 FE tests)."""
    fx = seed_canonical_sheet()
    with_versions = _snapshot(fx, versions=fx.repo.versions)
    without_versions = _snapshot(fx, versions=None)

    def strip_versions(snap):
        s = copy.deepcopy(snap)
        for n in s["nodes"]:
            n.pop("versions", None)
        return s

    # everything EXCEPT the new versions key is identical regardless of the param
    assert strip_versions(with_versions) == strip_versions(without_versions)
    # and the values dict specifically is untouched node-for-node
    for a, b in zip(with_versions["nodes"], without_versions["nodes"]):
        assert a["values"] == b["values"]


# ---------------------------------------------------------------------------
# (d) move_node_handler threads expected_revision -> StaleMoveError
# ---------------------------------------------------------------------------
def test_move_node_handler_threads_expected_revision():
    """move_node_handler must forward ``expected_revision`` to repo.move_node so
    the adapter's vanished-anchor StaleMoveError fires. The in-memory double
    records the threaded value so the wiring is observable without a bench."""
    fx = seed_canonical_sheet()
    calls = {}
    orig = fx.repo.move_node

    def spy(node, new_parent, after=None, expected_revision=None):
        calls["expected_revision"] = expected_revision
        calls["after"] = after
        return orig(node, new_parent, after=after, expected_revision=expected_revision)

    fx.repo.move_node = spy  # type: ignore[assignment]
    handlers.move_node_handler(
        {"sheet": fx.sheet, "node": fx.Y, "new_parent": fx.P1,
         "after": fx.X, "expected_revision": "rev-7"},
        Actor(user=A),
        fx.repo,
    )
    assert calls["expected_revision"] == "rev-7"
    assert calls["after"] == fx.X
