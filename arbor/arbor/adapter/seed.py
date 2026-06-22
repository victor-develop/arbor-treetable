"""Frappe-side canonical seed (TEST-PLAN §2 / PERMISSIONS §2).

Builds the SAME canonical sheet `S` as the pure fixture
``tests/fixtures/canonical.py`` — but as real Frappe DocTypes on a bench — so
integration/e2e tests and demo sites share one definition with the bench-free
core tests. The tree shape, personas, column ownership and initial cell values
are imported from the pure fixture's *spec* (``_CANONICAL_SPEC`` below mirrors
it field-for-field) so the two can never silently diverge.

This is the ADAPTER's seed: it goes through ``FrappeRepository`` for nodes,
columns, values and the Branch Grant, exactly as ``execute_action`` would, so
the seeded data is byte-identical in structure to runtime-created data.

Usage on a bench::

    bench --site <site> execute arbor.adapter.seed.seed_canonical_sheet

Returns a dict of the created record names (sheet/nodes/columns/grant), the
analogue of ``CanonicalFixture``.
"""

from __future__ import annotations

from typing import Any

import frappe

try:  # ``arbor.adapter`` on a bench; ``arbor.arbor.adapter`` in the dev repo.
    from arbor.adapter.repository import FrappeRepository
    from arbor.adapter import canonical_spec as spec
except ModuleNotFoundError:  # pragma: no cover - dev-layout fallback
    from arbor.arbor.adapter.repository import FrappeRepository  # type: ignore
    from arbor.arbor.adapter import canonical_spec as spec  # type: ignore

# Re-export the frappe-free spec so existing callers/tests can use either path.
A, B, C, D, E, F, G = spec.A, spec.B, spec.C, spec.D, spec.E, spec.F, spec.G
EXT, AGENT = spec.EXT, spec.AGENT
PERSONAS = spec.PERSONAS
SHEET_TITLE = spec.SHEET_TITLE
_COLUMNS = spec.COLUMNS
_TREE = spec.TREE
_VALUES = spec.VALUES
_GRANT = spec.GRANT


def ensure_personas(personas: tuple[str, ...] = PERSONAS) -> None:
    """Create the persona Frappe Users if absent (idempotent)."""
    for u in personas:
        email = _user(u)
        if not frappe.db.exists("User", email):
            doc = frappe.new_doc("User")
            doc.email = email
            doc.first_name = u
            doc.send_welcome_email = 0
            doc.enabled = 1
            doc.flags.ignore_permissions = True
            doc.insert(ignore_permissions=True)


def _user(u: str) -> str:
    # Frappe canonicalizes User email/name to lowercase on insert; mirror that so
    # the python-side persona ids (personas map, ACL comparisons) match the stored
    # Link values exactly. Otherwise "A@arbor.example" != stored "a@arbor.example".
    return (u if "@" in u else f"{u}@arbor.example").lower()


def seed_canonical_sheet(settings: dict | None = None) -> dict[str, Any]:
    """Build the canonical sheet `S` on the current site; return record names.

    Idempotent-ish: intended for a fresh/reset test site. Mirrors
    ``tests/fixtures/canonical.py`` exactly.
    """
    ensure_personas()
    repo = FrappeRepository()

    # ----- Tree Sheet -----
    sheet_doc = frappe.new_doc("Tree Sheet")
    sheet_doc.title = SHEET_TITLE
    sheet_doc.structural_owner = _user(A)
    sheet_doc.status = "active"
    sheet_doc.settings = settings or {}
    sheet_doc.insert(ignore_permissions=True)
    sheet = sheet_doc.name

    # ----- Columns -----
    columns: dict[str, str] = {}
    for spec in _COLUMNS:
        col_spec = {
            "field": spec["field"],
            "label": spec["label"],
            "type": spec["type"],
            "is_label": spec.get("is_label", False),
            "column_owner": _user(spec["column_owner"]),
            "editors": [_user(e) for e in spec.get("editors", [])],
            "options": spec.get("options"),
        }
        columns[spec["field"]] = repo.create_column(sheet, col_spec)

    # ----- Tree (parent links resolved by label) -----
    nodes: dict[str, str] = {}
    for label, parent_label in _TREE:
        parent = nodes[parent_label] if parent_label else None
        nodes[label] = repo.create_node(sheet=sheet, parent=parent)

    # ----- Branch Grant on P2 -----
    grant = repo.create_branch_grant(
        sheet=sheet,
        branch_root=nodes[_GRANT["branch_root"]],
        grantee=_user(_GRANT["grantee"]),
        granted_by=_user(_GRANT["granted_by"]),
    )

    # ----- Initial cell values -----
    for node_label, col_field, value in _VALUES:
        repo.set_value(sheet, nodes[node_label], columns[col_field], value)

    frappe.db.commit()

    return {
        "sheet": sheet,
        "nodes": nodes,
        "columns": columns,
        "grant_P2": grant,
        "personas": {p: _user(p) for p in PERSONAS},
    }
