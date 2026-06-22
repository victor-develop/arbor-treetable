"""ACL resolver: ancestor walk, nearest-grant-wins, root fallback, delegation,
column approvers (PERMISSIONS §1, §4)."""

from __future__ import annotations

from arbor.core.acl import (
    resolve_authority,
    resolve_column_approvers,
    resolve_structural_approver,
)
from arbor.core.registry import get_capability
from arbor.core.types import Actor
from tests.fixtures.canonical import (
    A,
    B,
    C,
    D,
    D2,
    E,
    apply_BG_Z,
    seed_canonical_sheet,
)


def test_structural_root_fallback():
    fx = seed_canonical_sheet()
    # X under P1/root: no grant on the chain → root owner A.
    assert resolve_structural_approver(fx.repo, fx.sheet, fx.X) == A


def test_structural_nearest_grant_on_P2():
    fx = seed_canonical_sheet()
    # Y under P2 (granted to D) → D.
    assert resolve_structural_approver(fx.repo, fx.sheet, fx.Y) == D
    assert resolve_structural_approver(fx.repo, fx.sheet, fx.P2) == D


def test_nearest_grant_wins_over_outer():
    fx = seed_canonical_sheet()
    apply_BG_Z(fx, grantee=D2)  # nested grant on Z
    # A structural change on Z resolves to the NEAREST grant D2, not D.
    assert resolve_structural_approver(fx.repo, fx.sheet, fx.Z) == D2
    # Y (sibling of Z, still under P2) still resolves to D.
    assert resolve_structural_approver(fx.repo, fx.sheet, fx.Y) == D


def test_add_at_root_is_sheet_owner():
    fx = seed_canonical_sheet()
    assert resolve_structural_approver(fx.repo, fx.sheet, None) == A


def test_column_approvers_owner_plus_editors():
    fx = seed_canonical_sheet()
    assert resolve_column_approvers(fx.repo, fx.sheet, fx.col_status) == {C, B}
    assert resolve_column_approvers(fx.repo, fx.sheet, fx.col_budget) == {C}


def test_authority_axis_independence_column_owner_in_foreign_branch():
    fx = seed_canonical_sheet()
    cap = get_capability("updateCell")
    # B owns col:name; Z lives in D's branch — B may still edit (Axis 2 ignores structure).
    auth = resolve_authority(
        cap,
        {"sheet": fx.sheet, "node": fx.Z, "column": fx.col_name, "value": "x"},
        Actor(B),
        fx.repo,
    )
    assert auth.is_authorized is True


def test_authority_branch_owner_cannot_edit_unowned_column():
    fx = seed_canonical_sheet()
    cap = get_capability("updateCell")
    # D owns P2 structurally but owns no columns → suggest to col owner C.
    auth = resolve_authority(
        cap,
        {"sheet": fx.sheet, "node": fx.Y, "column": fx.col_budget, "value": 1},
        Actor(D),
        fx.repo,
    )
    assert auth.is_authorized is False
    assert auth.resolved_approver == C


def test_move_node_requires_both_ends():
    fx = seed_canonical_sheet()
    cap = get_capability("moveNode")
    # A moves X (src parent P1 → A) into P2 (dest D). A authorizes src but not dest.
    auth = resolve_authority(
        cap,
        {"sheet": fx.sheet, "node": fx.X, "new_parent": fx.P2},
        Actor(A),
        fx.repo,
    )
    assert auth.is_authorized is False
    assert auth.resolved_approver == D  # route to dest
    assert auth.co_approvers == (A,)  # src as co-approver


def test_move_node_authorized_when_actor_owns_both_ends():
    fx = seed_canonical_sheet()
    cap = get_capability("moveNode")
    # A moves X within A's own structure (P1 → root). Both ends = A.
    auth = resolve_authority(
        cap,
        {"sheet": fx.sheet, "node": fx.X, "new_parent": fx.R},
        Actor(A),
        fx.repo,
    )
    assert auth.is_authorized is True
    assert auth.co_approvers == ()


def test_suggest_only_user_never_authorized_to_mutate():
    fx = seed_canonical_sheet()
    cap = get_capability("updateCell")
    auth = resolve_authority(
        cap,
        {"sheet": fx.sheet, "node": fx.X, "column": fx.col_status, "value": "done"},
        Actor(E),
        fx.repo,
    )
    assert auth.is_authorized is False
    assert auth.resolved_approver == C
