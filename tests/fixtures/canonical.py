"""The ONE canonical seed (TEST-PLAN §2), expressed against the pure in-memory
Repository so every layer reuses a single seed definition.

Sheet S, root structural owner A. Tree::

    R                         struct authority: A
    ├── P1                    struct authority: A
    │   └── X                 struct authority: A
    └── P2  ◄── Branch Grant (grantee=D, scope=structure, active)  struct: D
        ├── Y                 struct authority: D (inherited)
        └── Z                 struct authority: D (inherited)

Columns: name (is_label, owner B), status (owner C, editors=[B]),
budget (owner C), notes (owner B).

Personas A,B,C,D,E,F,G + EXT + AGENT. Named deltas in TEST-PLAN §2.3 are applied
by helper functions here, never by forking the base.
"""

from __future__ import annotations

from dataclasses import dataclass

from arbor.core.testing import InMemoryRepository

# Persona Frappe-User names.
A = "A"
B = "B"
C = "C"
D = "D"
E = "E"
F = "F"
G = "G"
EXT = "EXT"
AGENT = "AGENT"
D2 = "D2"  # used by the BG_Z nearest-grant-wins delta


@dataclass
class CanonicalFixture:
    repo: InMemoryRepository
    sheet: str
    # nodes
    R: str
    P1: str
    X: str
    P2: str
    Y: str
    Z: str
    # columns
    col_name: str
    col_status: str
    col_budget: str
    col_notes: str
    # grant
    grant_P2: str


def seed_canonical_sheet(settings: dict | None = None) -> CanonicalFixture:
    """Build the canonical seed in a fresh in-memory repo and return handles."""
    repo = InMemoryRepository()
    S = repo.add_sheet("S", structural_owner=A, settings=settings or {})

    col_name = repo.add_column("col:name", S, "name", column_owner=B, is_label=True, type="text")
    col_status = repo.add_column(
        "col:status", S, "status", column_owner=C, editors=[B], type="single-select-split"
    )
    col_budget = repo.add_column("col:budget", S, "budget", column_owner=C, type="number")
    col_notes = repo.add_column("col:notes", S, "notes", column_owner=B, type="multiline-text")

    R = repo.add_node("R", S, parent=None)
    P1 = repo.add_node("P1", S, parent=R)
    X = repo.add_node("X", S, parent=P1)
    P2 = repo.add_node("P2", S, parent=R)
    Y = repo.add_node("Y", S, parent=P2)
    Z = repo.add_node("Z", S, parent=P2)

    grant_P2 = repo.add_grant("grant:P2", S, branch_root=P2, grantee=D, granted_by=A)

    repo.seed_value(S, R, col_name, "Root")
    repo.seed_value(S, P1, col_name, "Phase 1")
    repo.seed_value(S, X, col_name, "Task X")
    repo.seed_value(S, P2, col_name, "Phase 2")
    repo.seed_value(S, Y, col_name, "Task Y")
    repo.seed_value(S, Z, col_name, "Task Z")
    repo.seed_value(S, X, col_status, "todo")
    repo.seed_value(S, X, col_budget, 1000)
    repo.seed_value(S, Y, col_budget, 5000)
    repo.seed_value(S, Z, col_budget, 12000)

    return CanonicalFixture(
        repo=repo,
        sheet=S,
        R=R,
        P1=P1,
        X=X,
        P2=P2,
        Y=Y,
        Z=Z,
        col_name=col_name,
        col_status=col_status,
        col_budget=col_budget,
        col_notes=col_notes,
        grant_P2=grant_P2,
    )


def apply_BG_Z(fx: CanonicalFixture, grantee: str = D2) -> str:
    """Delta ``BG_Z`` — a nested active Branch Grant on Z, grantee D2
    (nearest-grant-wins; TEST-PLAN §2.3)."""
    return fx.repo.add_grant("grant:Z", fx.sheet, branch_root=fx.Z, grantee=grantee, granted_by=D)
