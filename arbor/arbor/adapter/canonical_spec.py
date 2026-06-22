"""The canonical seed SPEC — frappe-free, importable anywhere.

This is the single declarative description of the canonical sheet `S`
(TEST-PLAN §2 / PERMISSIONS §2): personas, columns + ownership, tree edges,
initial cell values, and the P2 Branch Grant. It mirrors the pure fixture
``tests/fixtures/canonical.py`` field-for-field.

Kept frappe-free (no imports) so:
- ``arbor.adapter.seed`` (bench) consumes it to build real DocTypes, and
- the bench-free parity test re-derives the pure fixture and compares against
  THIS spec — proving the two seeds cannot silently diverge.
"""

from __future__ import annotations

from typing import Any

# Persona Frappe-User names — MUST match tests/fixtures/canonical.py.
A, B, C, D, E, F, G = "A", "B", "C", "D", "E", "F", "G"
EXT, AGENT = "EXT", "AGENT"

PERSONAS: tuple[str, ...] = (A, B, C, D, E, F, G, EXT, AGENT)

SHEET_TITLE = "S"

COLUMNS: tuple[dict[str, Any], ...] = (
    {"field": "name", "label": "Name", "type": "text", "is_label": True, "column_owner": B},
    {
        "field": "status",
        "label": "Status",
        "type": "single-select-split",
        "column_owner": C,
        "editors": [B],
        "options": {"groups": [{"label": "Status", "options": ["todo", "doing", "done"]}]},
    },
    {"field": "budget", "label": "Budget", "type": "number", "column_owner": C},
    {"field": "notes", "label": "Notes", "type": "multiline-text", "column_owner": B},
)

# Tree edges (child label -> parent label). Root R has parent None.
TREE: tuple[tuple[str, str | None], ...] = (
    ("R", None),
    ("P1", "R"),
    ("X", "P1"),
    ("P2", "R"),
    ("Y", "P2"),
    ("Z", "P2"),
)

# Initial cell values: (node_label, column_field, value).
VALUES: tuple[tuple[str, str, Any], ...] = (
    ("R", "name", "Root"),
    ("P1", "name", "Phase 1"),
    ("X", "name", "Task X"),
    ("P2", "name", "Phase 2"),
    ("Y", "name", "Task Y"),
    ("Z", "name", "Task Z"),
    ("X", "status", "todo"),
    ("X", "budget", 1000),
    ("Y", "budget", 5000),
    ("Z", "budget", 12000),
)

# Branch Grant: P2 delegated to D, scope=structure, granted_by A.
GRANT: dict[str, str] = {"branch_root": "P2", "grantee": D, "granted_by": A}
