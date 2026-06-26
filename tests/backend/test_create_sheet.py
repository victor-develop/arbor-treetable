"""``arbor.create_sheet`` — standalone whitelisted mutation (NOT a capability).

runnable: NEEDS FRAPPE BENCH (``@pytest.mark.bench``; auto-skips bench-free). A
sheet has no per-sheet ACL yet, so create_sheet is a plain authenticated mutation
(not routed through the registry/executor). It must:

* create the Tree Sheet with the caller as ``structural_owner`` (so the creator
  immediately gets can_add_column / can_change_structure on it),
* create a default LABEL Tree Column (is_label, type=text, owned by the creator)
  so the new sheet is immediately usable,
* reject a duplicate name (409 / ValidationError),
* let ANY authenticated non-Guest user create + own a sheet.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.bench

frappe = pytest.importorskip("frappe")

from arbor import api  # noqa: E402

from tests.backend import _helpers as h  # noqa: E402


@pytest.fixture()
def cleanup():
    """Yield, then drop any sheets this test created (rollback also covers it,
    but be explicit so a half-committed run leaves nothing behind)."""
    created: list[str] = []
    yield created
    frappe.set_user("Administrator")


def _unique(name: str) -> str:
    return f"{name}-{frappe.generate_hash(length=8)}"


def test_create_sheet_makes_owned_sheet_with_label_column(cleanup):
    h.login_as("A")
    me = h.user("A")
    name = _unique("CS-OWNED")

    out = api.create_sheet(name=name, title="A Brand New Sheet", label="Initiative")
    cleanup.append(name)

    assert out == {"sheet": name}
    assert frappe.db.exists("Tree Sheet", name)
    # Caller is the structural owner -> they can add columns/nodes directly.
    assert frappe.db.get_value("Tree Sheet", name, "structural_owner") == me

    # Exactly one column, and it is the default LABEL column owned by the caller.
    cols = frappe.get_all(
        "Tree Column",
        filters={"sheet": name},
        fields=["name", "is_label", "type", "column_owner", "label"],
    )
    assert len(cols) == 1
    col = cols[0]
    assert bool(col.is_label) is True
    assert col.type == "text"
    assert col.column_owner == me
    assert col.label == "Initiative"

    # The snapshot reflects the owner affordances immediately.
    snap = api.get_sheet_snapshot(sheet=name)
    assert snap["viewer"]["can_add_column"] is True


def test_create_sheet_defaults_label_arg_to_item(cleanup):
    h.login_as("A")
    name = _unique("CS-DEFAULT-LABEL")
    api.create_sheet(name=name)
    cleanup.append(name)

    col = frappe.get_all(
        "Tree Column", filters={"sheet": name, "is_label": 1}, fields=["label"]
    )[0]
    assert col.label == "Item"


def test_create_sheet_duplicate_name_errors(cleanup):
    h.login_as("A")
    name = _unique("CS-DUP")
    api.create_sheet(name=name)
    cleanup.append(name)

    # A second create with the same name is a 409 / ValidationError.
    with pytest.raises(frappe.ValidationError):
        api.create_sheet(name=name)
    assert frappe.local.response.get("http_status_code") == 409


def test_create_sheet_rejects_empty_name(cleanup):
    h.login_as("A")
    with pytest.raises(frappe.ValidationError):
        api.create_sheet(name="   ")


def test_fresh_non_admin_caller_can_create_and_owns(cleanup):
    """A brand-new, non-admin, non-System-Manager user may create a sheet and
    becomes its structural owner."""
    fresh = h.ensure_user("freshcreator")
    assert "System Manager" not in set(frappe.get_roles(fresh))
    frappe.set_user(fresh)

    name = _unique("CS-FRESH")
    out = api.create_sheet(name=name, label="Thing")
    cleanup.append(name)

    assert out == {"sheet": name}
    assert frappe.db.get_value("Tree Sheet", name, "structural_owner") == fresh
    col = frappe.get_all(
        "Tree Column", filters={"sheet": name, "is_label": 1},
        fields=["column_owner", "type"],
    )[0]
    assert col.column_owner == fresh
    assert col.type == "text"
