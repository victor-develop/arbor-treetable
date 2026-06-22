"""RED — Feature 3 read-ACL resolver (LEAN: 3 levels only).

Per /tmp/authz_tdd.txt FEATURE 3 and the SCOPE DECISIONS override:
    read_level ∈ {public, explicit-readers, owner-only}  (NO sheet-members, NO roles)

This file is the executable contract for ``arbor.core.acl.can_read_column`` and
``arbor.core.acl.visible_columns`` — the ONE place the read rule lives, reused by
snapshot AND explore. It is RED until those two functions exist.

Resolver order (LEAN):
    1. actor.is_admin            -> True
    2. col.is_label              -> True   (label always visible)
    3. actor.user in approvers   -> True   (editors-can-always-read; covers owner-only)
    4. dispatch on col.read_level:
         public           -> True
         owner-only        -> False        (only reached for non-approvers => False)
         explicit-readers  -> actor.user in col.readers
"""

from __future__ import annotations

import pytest

from arbor.core.acl import can_read_column, visible_columns
from arbor.core.types import Actor, ActorType
from tests.fixtures.canonical import (
    A,
    B,
    C,
    D,
    E,
    G,
    seed_canonical_sheet,
)

# An unrelated user with no ownership/editor/reader/grant relationship anywhere.
UNRELATED = G
# A persona who structurally owns a branch (P2 grantee) but is otherwise unrelated
# to the column axis — must NOT get reads from structure alone in the LEAN model.
BRANCH_GRANTEE = D


def _human(user: str, *, is_admin: bool = False) -> Actor:
    return Actor(user, ActorType.HUMAN, is_admin=is_admin)


def _set_read(fx, column: str, level: str, readers: list[str] | None = None) -> None:
    fx.repo.update_column(fx.sheet, column, {"read_level": level, "readers": list(readers or [])})


# ---------------------------------------------------------------------------
# (a) PER-LEVEL READ MATRIX
# ---------------------------------------------------------------------------
# col:budget — owner C, no editors. We flip its read_level per case and assert the
# read decision for a representative cast: owner / editor / explicit-reader /
# sheet structural_owner / branch grantee / unrelated / admin.


def test_public_level_readable_by_everyone():
    fx = seed_canonical_sheet()
    _set_read(fx, fx.col_budget, "public")
    col = fx.repo.get_column(fx.sheet, fx.col_budget)
    sheet = fx.repo.get_sheet(fx.sheet)
    for who in (C, B, E, A, BRANCH_GRANTEE, UNRELATED):
        assert can_read_column(fx.repo, sheet, col, _human(who)) is True
    # admin always
    assert can_read_column(fx.repo, sheet, col, _human(UNRELATED, is_admin=True)) is True


def test_owner_only_level_matrix():
    fx = seed_canonical_sheet()
    _set_read(fx, fx.col_budget, "owner-only")
    col = fx.repo.get_column(fx.sheet, fx.col_budget)
    sheet = fx.repo.get_sheet(fx.sheet)
    # owner reads
    assert can_read_column(fx.repo, sheet, col, _human(C)) is True
    # NON-owners denied: sheet owner, branch grantee, unrelated, an explicit
    # reader name that is meaningless at owner-only (readers list ignored here).
    assert can_read_column(fx.repo, sheet, col, _human(A)) is False
    assert can_read_column(fx.repo, sheet, col, _human(BRANCH_GRANTEE)) is False
    assert can_read_column(fx.repo, sheet, col, _human(UNRELATED)) is False
    # admin overrides
    assert can_read_column(fx.repo, sheet, col, _human(UNRELATED, is_admin=True)) is True


def test_explicit_readers_level_matrix():
    fx = seed_canonical_sheet()
    # col:budget owner C; grant explicit read to E only.
    _set_read(fx, fx.col_budget, "explicit-readers", readers=[E])
    col = fx.repo.get_column(fx.sheet, fx.col_budget)
    sheet = fx.repo.get_sheet(fx.sheet)
    # owner reads (approver short-circuit, step 3)
    assert can_read_column(fx.repo, sheet, col, _human(C)) is True
    # explicit reader reads
    assert can_read_column(fx.repo, sheet, col, _human(E)) is True
    # everyone else NOT in readers and not an approver: denied — incl sheet owner
    # and branch grantee (no structure-derived read in the LEAN model).
    assert can_read_column(fx.repo, sheet, col, _human(A)) is False
    assert can_read_column(fx.repo, sheet, col, _human(BRANCH_GRANTEE)) is False
    assert can_read_column(fx.repo, sheet, col, _human(UNRELATED)) is False
    # admin overrides
    assert can_read_column(fx.repo, sheet, col, _human(UNRELATED, is_admin=True)) is True


# ---------------------------------------------------------------------------
# (b) EDITOR-CAN-ALWAYS-READ invariant at every level (incl owner-only)
# ---------------------------------------------------------------------------
# col:status owner C, editors=[B]. B must read at every level even when not an
# owner and not listed in readers.
@pytest.mark.parametrize("level", ["public", "owner-only", "explicit-readers"])
def test_editor_can_always_read(level):
    fx = seed_canonical_sheet()
    _set_read(fx, fx.col_status, level, readers=[])  # readers empty: B not a reader
    col = fx.repo.get_column(fx.sheet, fx.col_status)
    sheet = fx.repo.get_sheet(fx.sheet)
    assert B in col.editors  # precondition
    assert can_read_column(fx.repo, sheet, col, _human(B)) is True


# ---------------------------------------------------------------------------
# (c) LABEL-ALWAYS-VISIBLE — is_label readable by an unrelated non-reader even
# at owner-only (so nodes keep their display labels for everyone).
# ---------------------------------------------------------------------------
def test_label_column_always_visible():
    fx = seed_canonical_sheet()
    # col:name is the label column (owner B). Try to lock it down hard.
    _set_read(fx, fx.col_name, "owner-only", readers=[])
    col = fx.repo.get_column(fx.sheet, fx.col_name)
    sheet = fx.repo.get_sheet(fx.sheet)
    assert col.is_label  # precondition
    assert can_read_column(fx.repo, sheet, col, _human(UNRELATED)) is True


# ---------------------------------------------------------------------------
# (e) explicit-readers with an EMPTY readers list is still readable by
# owner + editors (the approver short-circuit), denied to all others.
# ---------------------------------------------------------------------------
def test_explicit_readers_empty_list_still_readable_by_owner_and_editors():
    fx = seed_canonical_sheet()
    _set_read(fx, fx.col_status, "explicit-readers", readers=[])  # status: owner C, editors [B]
    col = fx.repo.get_column(fx.sheet, fx.col_status)
    sheet = fx.repo.get_sheet(fx.sheet)
    assert can_read_column(fx.repo, sheet, col, _human(C)) is True  # owner
    assert can_read_column(fx.repo, sheet, col, _human(B)) is True  # editor
    assert can_read_column(fx.repo, sheet, col, _human(UNRELATED)) is False


# ---------------------------------------------------------------------------
# visible_columns — the single filter used by snapshot + explore.
# ---------------------------------------------------------------------------
def test_visible_columns_filters_to_readable_only():
    fx = seed_canonical_sheet()
    _set_read(fx, fx.col_budget, "owner-only")  # only C (owner) + admins
    cols = fx.repo.list_columns(fx.sheet)
    sheet = fx.repo.get_sheet(fx.sheet)

    # Unrelated viewer: budget dropped, label + public columns kept.
    vis_unrelated = {c.name for c in visible_columns(fx.repo, sheet, _human(UNRELATED), cols)}
    assert fx.col_budget not in vis_unrelated
    assert fx.col_name in vis_unrelated  # label always
    assert fx.col_status in vis_unrelated and fx.col_notes in vis_unrelated  # public

    # Owner C: budget visible.
    vis_owner = {c.name for c in visible_columns(fx.repo, sheet, _human(C), cols)}
    assert fx.col_budget in vis_owner

    # Admin: everything.
    vis_admin = {
        c.name for c in visible_columns(fx.repo, sheet, _human(UNRELATED, is_admin=True), cols)
    }
    assert vis_admin == {c.name for c in cols}


def test_visible_columns_preserves_input_order():
    fx = seed_canonical_sheet()
    cols = fx.repo.list_columns(fx.sheet)
    sheet = fx.repo.get_sheet(fx.sheet)
    vis = visible_columns(fx.repo, sheet, _human(A), cols)  # all public => identity
    assert [c.name for c in vis] == [c.name for c in cols]


def test_agent_actor_filters_identically_to_human():
    """Agent inheritance at the resolver level: an Actor(actor_type=AGENT) gets the
    SAME read decision as a human with the same user — read-ACL is actor_type-blind."""
    fx = seed_canonical_sheet()
    _set_read(fx, fx.col_budget, "explicit-readers", readers=[E])
    col = fx.repo.get_column(fx.sheet, fx.col_budget)
    sheet = fx.repo.get_sheet(fx.sheet)
    agent_reader = Actor(E, ActorType.AGENT)
    agent_outsider = Actor(UNRELATED, ActorType.AGENT)
    assert can_read_column(fx.repo, sheet, col, agent_reader) is True
    assert can_read_column(fx.repo, sheet, col, agent_outsider) is False
