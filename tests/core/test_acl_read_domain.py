"""Domain principals in the read ACL: ``readers = ["domain:example.com"]``.

The point of the shape is to open a column to a whole company without listing
people — and because commenting is defined as "you may discuss any cell you can
read", and an unauthorized write degrades to a Change Request rather than
failing, read is the ONLY grant needed to give a domain read + comment +
suggest. Those two consequences are asserted here rather than assumed.

A domain is a MEMBERSHIP TEST, never an expansion: Arbor provisions a user on
their first SSO login, so tomorrow's colleague is already at the domain but is
not yet a row anywhere. That is also why the approver slots reject it — see
test_column_principal_validation.
"""

from __future__ import annotations

import pytest

from arbor.core.acl import (
    can_add_comment,
    can_read_column,
    resolve_column_approvers,
    visible_columns,
)
from arbor.core.executor import execute_action
from arbor.core.testing import RecordingEventSink
from arbor.core.types import Actor, ActorType
from tests.fixtures.canonical import C, E, G, seed_canonical_sheet

HOST = "example.com"
INSIDER = f"insider@{HOST}"
OTHER_INSIDER = f"someone.else@{HOST}"
OUTSIDER = "outsider@other.com"
LOOKALIKE = f"attacker@evil-{HOST}"  # a suffix match would wrongly admit this
SUBDOMAIN = f"person@corp.{HOST}"  # a different organization boundary


def _human(user: str, *, is_admin: bool = False) -> Actor:
    return Actor(user, ActorType.HUMAN, is_admin=is_admin)


def _open_to_domain(fx, column: str, host: str = HOST) -> None:
    fx.repo.update_column(
        fx.sheet, column, {"read_level": "explicit-readers", "readers": [f"domain:{host}"]}
    )


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------
def test_anyone_at_the_domain_reads_and_nobody_else_does():
    fx = seed_canonical_sheet()
    _open_to_domain(fx, fx.col_budget)
    col = fx.repo.get_column(fx.sheet, fx.col_budget)
    sheet = fx.repo.get_sheet(fx.sheet)

    # Never named anywhere, yet both read — that is the whole feature.
    assert can_read_column(fx.repo, sheet, col, _human(INSIDER)) is True
    assert can_read_column(fx.repo, sheet, col, _human(OTHER_INSIDER)) is True

    assert can_read_column(fx.repo, sheet, col, _human(OUTSIDER)) is False
    # The owner still reads via the approver short-circuit.
    assert can_read_column(fx.repo, sheet, col, _human(C)) is True


def test_the_match_is_exact_not_a_suffix_and_not_a_subdomain():
    """A suffix test would hand ``evil-example.com`` everything, and admitting a
    subdomain silently widens the grant past the organization that was named."""
    fx = seed_canonical_sheet()
    _open_to_domain(fx, fx.col_budget)
    col = fx.repo.get_column(fx.sheet, fx.col_budget)
    sheet = fx.repo.get_sheet(fx.sheet)

    assert can_read_column(fx.repo, sheet, col, _human(LOOKALIKE)) is False
    assert can_read_column(fx.repo, sheet, col, _human(SUBDOMAIN)) is False


def test_the_match_ignores_case_on_both_sides():
    fx = seed_canonical_sheet()
    _open_to_domain(fx, fx.col_budget, host="Example.COM")
    col = fx.repo.get_column(fx.sheet, fx.col_budget)
    sheet = fx.repo.get_sheet(fx.sheet)
    assert can_read_column(fx.repo, sheet, col, _human("Insider@EXAMPLE.com")) is True


def test_a_user_without_an_email_is_never_at_a_domain():
    """The canonical personas are bare names; a domain grant must not admit them
    (nor crash on the missing '@')."""
    fx = seed_canonical_sheet()
    _open_to_domain(fx, fx.col_budget)
    col = fx.repo.get_column(fx.sheet, fx.col_budget)
    sheet = fx.repo.get_sheet(fx.sheet)
    for who in (G, E):
        assert can_read_column(fx.repo, sheet, col, _human(who)) is False


def test_a_domain_reader_mixes_with_users_and_roles():
    fx = seed_canonical_sheet()
    fx.repo.update_column(
        fx.sheet,
        fx.col_budget,
        {"read_level": "explicit-readers", "readers": [E, f"domain:{HOST}"]},
    )
    col = fx.repo.get_column(fx.sheet, fx.col_budget)
    sheet = fx.repo.get_sheet(fx.sheet)
    assert can_read_column(fx.repo, sheet, col, _human(E)) is True
    assert can_read_column(fx.repo, sheet, col, _human(INSIDER)) is True
    assert can_read_column(fx.repo, sheet, col, _human(OUTSIDER)) is False


def test_a_domain_grant_only_bites_at_explicit_readers():
    """The other two levels ignore the readers list entirely, and a domain must
    not smuggle a read past owner-only."""
    fx = seed_canonical_sheet()
    fx.repo.update_column(
        fx.sheet, fx.col_budget, {"read_level": "owner-only", "readers": [f"domain:{HOST}"]}
    )
    col = fx.repo.get_column(fx.sheet, fx.col_budget)
    sheet = fx.repo.get_sheet(fx.sheet)
    assert can_read_column(fx.repo, sheet, col, _human(INSIDER)) is False


# ---------------------------------------------------------------------------
# What read carries with it: the column shows up, comments open, and an
# unauthorized write becomes a suggestion instead of a denial.
# ---------------------------------------------------------------------------
def test_the_column_becomes_visible_in_the_snapshot_filter():
    fx = seed_canonical_sheet()
    _open_to_domain(fx, fx.col_budget)
    sheet = fx.repo.get_sheet(fx.sheet)
    columns = fx.repo.list_columns(fx.sheet)

    visible = visible_columns(fx.repo, sheet, _human(INSIDER), columns)
    assert fx.col_budget in [c.name for c in visible]
    hidden = visible_columns(fx.repo, sheet, _human(OUTSIDER), columns)
    assert fx.col_budget not in [c.name for c in hidden]


def test_read_carries_commenting():
    """``can_add_comment`` is defined as ``can_read_column``, so a domain reader
    can discuss the cells they can see without any separate grant."""
    fx = seed_canonical_sheet()
    _open_to_domain(fx, fx.col_budget)
    assert can_add_comment(fx.repo, fx.sheet, fx.col_budget, _human(INSIDER)) is True
    assert can_add_comment(fx.repo, fx.sheet, fx.col_budget, _human(OUTSIDER)) is False


def test_a_domain_reader_is_not_an_approver_so_a_write_becomes_a_suggestion():
    """Read is the whole grant: the domain confers no authority, so the reader's
    edit takes the governed path (a Change Request) rather than being applied or
    refused."""
    fx = seed_canonical_sheet()
    _open_to_domain(fx, fx.col_budget)

    approvers = resolve_column_approvers(fx.repo, fx.sheet, fx.col_budget)
    assert INSIDER not in approvers, "a domain must never enter the approver set"

    outcome = execute_action(
        "updateCell",
        {"sheet": fx.sheet, "node": fx.X, "column": fx.col_budget, "value": 42},
        _human(INSIDER),
        fx.repo,
        RecordingEventSink(),
    )
    assert outcome.kind == "suggested"
    assert outcome.change_request


# ---------------------------------------------------------------------------
# Write-path validation: a domain in a slot that cannot use it must be refused,
# not silently ignored.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("slot", ["column_owner", "editors"])
def test_a_domain_is_refused_in_an_approver_slot(slot):
    """Expansion drops it, so accepting it would leave the granter believing the
    column was opened while the grantee's writes kept turning into CRs."""
    fx = seed_canonical_sheet()
    value = f"domain:{HOST}" if slot == "column_owner" else [f"domain:{HOST}"]
    with pytest.raises(ValueError, match="cannot be an email domain"):
        execute_action(
            "grantColumn",
            {"sheet": fx.sheet, "column": fx.col_budget, slot: value},
            _human(C),  # the column owner, so this is the authorized path
            fx.repo,
            RecordingEventSink(),
        )


@pytest.mark.parametrize(
    "bad",
    ["domain:victor@example.com", "domain:@example.com", "domain:example", "domain:"],
)
def test_a_malformed_domain_principal_is_refused(bad):
    """A typo that matches nobody is indistinguishable from a grant that works,
    so the shape is checked where it is written."""
    fx = seed_canonical_sheet()
    with pytest.raises(ValueError, match="malformed domain principal"):
        execute_action(
            "updateColumn",
            {
                "sheet": fx.sheet,
                "column": fx.col_budget,
                "patch": {"read_level": "explicit-readers", "readers": [bad]},
            },
            _human(C),
            fx.repo,
            RecordingEventSink(),
        )


# ---------------------------------------------------------------------------
# The governance read carries the config the UI needs — no wider than that.
# ---------------------------------------------------------------------------
def test_the_definition_exposes_read_level_to_readers_but_the_roster_only_to_approvers():
    """The Settings panel needs the current config to edit it. ``read_level`` is
    about the viewer themselves, so it rides along; the ``readers`` roster names
    OTHER people, so only an approver — who needs it to change the list — gets
    it."""
    from arbor.core.explore import sheet_definition

    fx = seed_canonical_sheet()
    _open_to_domain(fx, fx.col_budget)

    owner_view = sheet_definition(fx.repo, fx.sheet, _human(C))
    owner_col = next(c for c in owner_view["columns"] if c["name"] == fx.col_budget)
    assert owner_col["read_level"] == "explicit-readers"
    assert owner_col["readers"] == [f"domain:{HOST}"]

    reader_view = sheet_definition(fx.repo, fx.sheet, _human(INSIDER))
    reader_col = next(c for c in reader_view["columns"] if c["name"] == fx.col_budget)
    assert reader_col["read_level"] == "explicit-readers"
    assert "readers" not in reader_col, "the roster must not reach a non-approver"


def test_the_definition_still_hides_a_column_the_viewer_cannot_read():
    """The new fields must not widen what the definition lists."""
    from arbor.core.explore import sheet_definition

    fx = seed_canonical_sheet()
    fx.repo.update_column(fx.sheet, fx.col_budget, {"read_level": "owner-only", "readers": []})
    view = sheet_definition(fx.repo, fx.sheet, _human(OUTSIDER))
    assert fx.col_budget not in [c["name"] for c in view["columns"]]


# ---------------------------------------------------------------------------
# A bad principal must be refused on EVERY branch, not stored as a Change
# Request whose approval can only ever raise.
# ---------------------------------------------------------------------------
def test_an_unauthorized_caller_cannot_file_an_unapprovable_request():
    """Validating inside the handler only covered the authorized branch: an
    unauthorized caller's bad principal became a CR, and approving it raised —
    400 on every retry, the CR pinned in PROPOSED, Reject the only exit. And a
    domain reader can suggest, so anyone in the domain could plant one."""
    fx = seed_canonical_sheet()
    with pytest.raises(ValueError, match="malformed domain principal"):
        execute_action(
            "updateColumn",
            {
                "sheet": fx.sheet,
                "column": fx.col_budget,
                "patch": {"read_level": "explicit-readers", "readers": ["domain:@evil.com"]},
            },
            _human(E),  # not an approver -> would otherwise take the suggest branch
            fx.repo,
            RecordingEventSink(),
        )


def test_the_batch_suggest_door_is_guarded_too():
    """``suggestChanges`` is a second door onto the same capabilities."""
    fx = seed_canonical_sheet()
    with pytest.raises(ValueError, match="malformed domain principal"):
        execute_action(
            "suggestChanges",
            {
                "sheet": fx.sheet,
                "changes": [
                    {
                        "action": "updateColumn",
                        "params": {
                            "column": fx.col_budget,
                            "patch": {"readers": ["domain:@evil.com"]},
                        },
                    }
                ],
            },
            _human(E),
            fx.repo,
            RecordingEventSink(),
        )


@pytest.mark.parametrize("bad", ["domain:not a host", "domain:example.com:25"])
def test_a_host_with_a_space_or_a_port_is_refused(bad):
    """A port is what people paste out of a URL."""
    fx = seed_canonical_sheet()
    with pytest.raises(ValueError, match="malformed domain principal"):
        execute_action(
            "updateColumn",
            {"sheet": fx.sheet, "column": fx.col_budget, "patch": {"readers": [bad]}},
            _human(C),
            fx.repo,
            RecordingEventSink(),
        )


def test_readers_must_be_a_list():
    """A bare string was stored one CHARACTER per reader, and a single-letter
    'user' then matched a real one."""
    fx = seed_canonical_sheet()
    with pytest.raises(ValueError, match="readers must be a list"):
        execute_action(
            "updateColumn",
            {"sheet": fx.sheet, "column": fx.col_budget, "patch": {"readers": "domain:x.com"}},
            _human(C),
            fx.repo,
            RecordingEventSink(),
        )


def test_add_column_refuses_a_domain_in_an_approver_slot():
    """addColumn's schema accepts column_owner, and its handler validated the
    spec it builds rather than the params it was given."""
    fx = seed_canonical_sheet()
    with pytest.raises(ValueError, match="cannot be an email domain"):
        execute_action(
            "addColumn",
            {
                "sheet": fx.sheet,
                "field": "extra",
                "label": "Extra",
                "type": "text",
                "column_owner": f"domain:{HOST}",
            },
            _human(fx.repo.get_sheet(fx.sheet).structural_owner),
            fx.repo,
            RecordingEventSink(),
        )


# ---------------------------------------------------------------------------
# "read + comment + suggest" — and the NOT-more half.
# ---------------------------------------------------------------------------
def test_a_domain_reader_cannot_moderate_other_peoples_comments():
    """The grant is read; resolving and deleting are approver powers."""
    from arbor.core.acl import can_delete_comment, can_resolve_comment

    fx = seed_canonical_sheet()
    _open_to_domain(fx, fx.col_budget)
    assert can_resolve_comment(fx.repo, fx.sheet, fx.col_budget, _human(INSIDER)) is False
    # Someone else's comment: denied. (Their own is the author's right, not the
    # domain's.)
    assert (
        can_delete_comment(fx.repo, fx.sheet, fx.col_budget, C, _human(INSIDER)) is False
    )
    assert (
        can_delete_comment(fx.repo, fx.sheet, fx.col_budget, INSIDER, _human(INSIDER)) is True
    )
