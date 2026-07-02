"""Per-cell comments — the ACL contract, bench-free (Area 2 / WS-CMT-BE).

Runnable: **bench-free** (plain pytest, no Frappe bench, no running app).

Comments add NO new ACL resolver — they reuse the ONE resolver in
``arbor.core.acl``:

* READ a cell's comments + POST a comment  -> ``can_read_column``
* RESOLVE / REOPEN a thread                 -> ``resolve_column_approvers``
* DELETE                                     -> author OR a column approver
* @mentions are read-ACL-filtered            -> ``can_read_column`` per mention

So this module asserts the *reused* resolvers yield exactly the authority the
comment shims must enforce, over the canonical in-memory fixture with an added
owner-only column (so we exercise the DENIED side, which the all-public canonical
columns can't). The @mention-parsing helper is pure string work and is unit-tested
standalone (guarded import — the helper lives in the frappe-coupled api module).

No Tree Event, no registry capability is involved — comments are collaboration
metadata layered on top of the existing resolver.
"""

from __future__ import annotations

import pytest

from arbor.core.acl import can_read_column, resolve_column_approvers
from arbor.core.types import Actor, ActorType
from tests.fixtures.canonical import A, B, C, E, F, seed_canonical_sheet


def _actor(user: str, is_admin: bool = False) -> Actor:
    return Actor(user=user, actor_type=ActorType.HUMAN, is_admin=is_admin)


def _fx_with_owner_only():
    """Canonical fixture + an owner-only ``secret`` column owned by C (editors=[B]).

    Read matrix on ``secret``: C (owner) yes, B (editor) yes, admin yes; E/F
    (no grant) no. This is the cell the DENY-side comment tests key on."""
    fx = seed_canonical_sheet()
    fx.repo.add_column(
        "col:secret", fx.sheet, "secret", column_owner=C, editors=[B],
        type="text", read_level="owner-only",
    )
    return fx


# ---------------------------------------------------------------------------
# READ / POST authority == can_read_column
# ---------------------------------------------------------------------------
def test_public_cell_is_readable_by_any_actor():
    """Any actor may read (and therefore discuss) a public column's cell."""
    fx = seed_canonical_sheet()
    budget = fx.repo.get_column(fx.sheet, fx.col_budget)  # public, owner C
    for u in (A, B, C, E, F):
        assert can_read_column(fx.repo, fx.sheet, budget, _actor(u)) is True


def test_owner_only_cell_read_gate_matches_comment_authority():
    """POST/READ on an owner-only cell: owner + editors + admin yes; strangers no.
    This is exactly what ``add_cell_comment`` / ``list_cell_comments`` enforce."""
    fx = _fx_with_owner_only()
    secret = fx.repo.get_column(fx.sheet, "col:secret")

    assert can_read_column(fx.repo, fx.sheet, secret, _actor(C)) is True   # owner
    assert can_read_column(fx.repo, fx.sheet, secret, _actor(B)) is True   # editor
    assert can_read_column(fx.repo, fx.sheet, secret, _actor("root", is_admin=True)) is True
    assert can_read_column(fx.repo, fx.sheet, secret, _actor(E)) is False  # stranger
    assert can_read_column(fx.repo, fx.sheet, secret, _actor(F)) is False  # stranger


# ---------------------------------------------------------------------------
# RESOLVE authority == resolve_column_approvers (owner + editors)
# ---------------------------------------------------------------------------
def test_resolve_authority_is_column_approvers():
    """A thread is settled only by the column's owner or its editors — a
    suggest-only reader (E) may discuss but not resolve."""
    fx = seed_canonical_sheet()
    # col:status owned by C with editor B.
    approvers = resolve_column_approvers(fx.repo, fx.sheet, fx.col_status)
    assert approvers == {C, B}
    assert C in approvers  # owner may resolve
    assert B in approvers  # editor may resolve
    assert E not in approvers  # suggest-only reader may NOT resolve


def test_delete_authority_author_or_approver():
    """DELETE = author-or-approver. Model the shim's rule directly: E (a reader,
    non-approver) may delete ONLY their own comment; C (approver) may delete
    anyone's; F (neither author nor approver) may delete neither."""
    fx = seed_canonical_sheet()
    approvers = resolve_column_approvers(fx.repo, fx.sheet, fx.col_budget)  # {C}

    def can_delete(actor_user: str, author: str) -> bool:
        return actor_user == author or actor_user in approvers

    assert can_delete(E, author=E) is True    # own comment
    assert can_delete(E, author=F) is False   # someone else's, E not approver
    assert can_delete(C, author=E) is True    # approver moderates
    assert can_delete(F, author=E) is False   # neither author nor approver


# ---------------------------------------------------------------------------
# @mention read-ACL filtering: a mention of a non-reader is dropped
# ---------------------------------------------------------------------------
def test_mention_of_non_reader_is_dropped_on_owner_only_column():
    """An @mention resolves to a notification recipient ONLY if the mentioned user
    can still read the column — so mentioning E on an owner-only cell drops E
    (never signal an owner-only cell's existence to a non-reader)."""
    fx = _fx_with_owner_only()
    secret = fx.repo.get_column(fx.sheet, "col:secret")

    def filter_mentions(candidates):
        return [
            u for u in candidates
            if can_read_column(fx.repo, fx.sheet, secret, _actor(u))
        ]

    # B (editor) survives; E (stranger) is dropped.
    assert filter_mentions([B, E]) == [B]
    # On a public column everyone survives.
    budget = fx.repo.get_column(fx.sheet, fx.col_budget)
    assert [
        u for u in [B, E, F] if can_read_column(fx.repo, fx.sheet, budget, _actor(u))
    ] == [B, E, F]


# ---------------------------------------------------------------------------
# @mention-parsing helper — pure string work (guarded import; helper lives in the
# frappe-coupled api module, so skip when frappe is absent).
# ---------------------------------------------------------------------------
def _extract_mentions():
    try:
        from arbor.arbor.api import _extract_mentions as fn  # dev layout (needs frappe)
    except Exception:
        try:
            from arbor.api import _extract_mentions as fn  # bench layout
        except Exception:
            pytest.skip("_extract_mentions requires the frappe-coupled api module")
    return fn


def test_extract_mentions_bare_handles_and_emails():
    fn = _extract_mentions()
    assert fn("hey @alice and @bob, look here") == ["alice", "bob"]
    assert fn("ping @c@arbor.example please") == ["c@arbor.example"]


def test_extract_mentions_dedup_and_no_midword_match():
    fn = _extract_mentions()
    # De-duplicated, order-preserving.
    assert fn("@x @y @x") == ["x", "y"]
    # A token mid-word (an email-like a@b written WITHOUT a leading space) is not a
    # mention; empty / no-mention bodies yield [].
    assert fn("email alice@arbor.example inline") == []
    assert fn("no mentions here") == []
    assert fn("") == []
