# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""E2e test support — a deterministic, logical-name seed + a reset endpoint.

The browser e2e specs (``tests/e2e``) drive a LIVE stack, so unlike the bench
integration tests they get no per-test transaction rollback. This module gives
them isolation: ``reset_e2e`` drops and rebuilds the canonical sheet ``S`` with
the **logical names** the specs reference (sheet ``S``; nodes
``R/P1/X/P2/Y/Z``; columns ``col:name/status/budget/notes``), so every test
starts from the same known tree.

SAFETY: ``reset_e2e`` is whitelisted but refuses to run unless the site is in
``developer_mode`` — it is a test fixture, never a production capability, and is
not part of the governed capability surface.
"""

from __future__ import annotations

import frappe

from arbor.arbor.adapter.seed import seed_canonical_sheet

_DT_CHILDREN = ("Tree Node Value", "Branch Grant", "Change Request", "Tree Event")
_DT_GLOBAL = ("Acknowledgement", "Notification", "Subscription")


def _rename(dt: str, old: str, new: str) -> None:
    if old != new and frappe.db.exists(dt, old) and not frappe.db.exists(dt, new):
        frappe.rename_doc(dt, old, new, force=True)


def seed_e2e() -> dict:
    """(Re)build sheet ``S`` with logical names + an empty ``S2`` import target.
    Idempotent: drops any prior ``S``/``S2`` first. Returns the record map."""
    # Global child tables (not sheet-columned) are cleared wholesale (test site).
    for dt in ("Acknowledgement", "Notification", "Subscription"):
        for n in frappe.get_all(dt, pluck="name"):
            frappe.db.delete(dt, {"name": n})
    for name in ("S", "S2"):
        _drop_sheet(name)
    frappe.db.commit()

    fx = seed_canonical_sheet()
    _rename("Tree Sheet", fx["sheet"], "S")
    for field, name in fx["columns"].items():
        _rename("Tree Column", name, f"col:{field}")
    for label, name in fx["nodes"].items():
        _rename("Tree Node", name, label)

    # Empty target sheet for the import round-trip (WEB_UI-082), owned by A.
    s2 = frappe.new_doc("Tree Sheet")
    s2.title = "S2"
    s2.structural_owner = "a@arbor.example"
    s2.status = "active"
    s2.insert(ignore_permissions=True)
    frappe.rename_doc("Tree Sheet", s2.name, "S2", force=True)

    frappe.db.commit()
    # The raw SQL deletes above bypass Frappe's document-cache invalidation, so
    # clear the cache or a subsequent snapshot read could serve stale rows.
    frappe.clear_cache()
    return {"sheet": "S", "import_target": "S2"}


def _drop_sheet(name: str) -> None:
    """Raw-SQL nuke a sheet + all its children (bypasses NestedSet on_trash)."""
    if not frappe.db.exists("Tree Sheet", name):
        return
    cr_names = frappe.get_all("Change Request", filters={"sheet": name}, pluck="name")
    for dt, names in (
        ("Tree Node Value", frappe.get_all("Tree Node Value", filters={"sheet": name}, pluck="name")),
        ("Branch Grant", frappe.get_all("Branch Grant", filters={"sheet": name}, pluck="name")),
        ("Change Request", cr_names),
        ("Tree Event", frappe.get_all("Tree Event", filters={"sheet": name}, pluck="name")),
        ("Tree Node", frappe.get_all("Tree Node", filters={"sheet": name}, pluck="name")),
        ("Tree Column", frappe.get_all("Tree Column", filters={"sheet": name}, pluck="name")),
        ("Tree Sheet", [name]),
    ):
        if names:
            frappe.db.delete(dt, {"name": ["in", names]})
    if cr_names:
        frappe.db.delete("Change Request Approval", {"parent": ["in", cr_names]})


@frappe.whitelist()
def reset_e2e() -> dict:
    """Whitelisted reset for the e2e harness. Developer-mode only.

    Retries on a transient MariaDB deadlock/lock-timeout (the raw bulk deletes can
    momentarily contend with a just-finished request's row locks under suite load).
    """
    import time

    if not frappe.conf.get("developer_mode"):
        frappe.throw("reset_e2e is only available in developer_mode")

    # The seed renames/drops sheets that may be owned by other users (or left
    # over by a prior failed run). Over HTTP this runs as the API key's user
    # (e.g. a@arbor.example), who lacks rename permission on those records, so
    # rename_doc(force=True) raises ValidationError. Elevate to Administrator
    # for the duration — this is a developer-mode-only fixture that already
    # nukes rows via raw SQL.
    original_user = frappe.session.user
    frappe.set_user("Administrator")
    try:
        last_exc = None
        for attempt in range(4):
            try:
                return seed_e2e()
            except (frappe.QueryDeadlockError, frappe.QueryTimeoutError) as exc:
                last_exc = exc
                frappe.db.rollback()
                time.sleep(0.3 * (attempt + 1))
        raise last_exc
    finally:
        frappe.set_user(original_user)
