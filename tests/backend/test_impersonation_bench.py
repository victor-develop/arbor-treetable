"""Traceable "act as" impersonation — the ADAPTER + auth surface (Area 1).

NEEDS A FRAPPE BENCH + SITE (``@pytest.mark.bench``; auto-skipped when frappe is
not importable so the bench-free suite stays green).

Proves the impersonation OVERLAY end-to-end against the live seams shipped by
WS-IMP-BE: ``arbor.api._actor`` (the request-scoped overlay), the
``begin_impersonation`` / ``end_impersonation`` REST shims, ``FrappeRepository``
impersonation-session CRUD, the ``FrappeEventSink`` real_user/impersonated_as
columns, ``arbor.auth.api.whoami``, and the ``_acl_hints`` viewer block.

Contract (docs/design/area-details.md AREA 1 + impersonation-comments-process.md
aclImplications). The load-bearing invariants asserted here:

* We NEVER ``frappe.set_user`` to become someone: ``frappe.session.user`` stays
  the REAL admin throughout an impersonated action, while ``_actor().user`` is the
  effective (impersonated) identity (risk #1: the silent set_user pitfall).
* Both identities travel into every Tree Event (``real_user`` = admin,
  ``impersonated_as`` = effective) and into a Change Request (``requester`` =
  effective, ``real_requester`` = admin) produced under the overlay.
* begin/end authority is the REAL user's admin, computed BEFORE the overlay is
  applied; a NON-admin begin is 403.
* Fail-safe (risk #2): an overlay persisted for a user who is no longer admin is
  IGNORED and force-ended on the next ``_actor()``.
* begin/end emit NO Tree Event — the Arbor Impersonation Session row IS the record.

Bench maps to area-details testStrategy items (6)-(11).

Run on a bench, e.g.::

    bench --site <site> run-tests --module tests.backend.test_impersonation_bench
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.bench

frappe = pytest.importorskip("frappe")

from arbor import api  # noqa: E402  (after importorskip)

from tests.backend import _helpers as h  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
@pytest.fixture()
def fx():
    """Seed the canonical sheet `S` (rolled back per-test by the bench harness)."""
    return h.seed()


# The canonical personas A..G are NOT platform admins. Impersonation authority is
# the REAL user's System Manager role, so an admin persona is minted on demand and
# granted the role in-transaction (rolled back with the rest of the test).
ADMIN = "adm"  # -> adm@arbor.example, granted System Manager below


def _make_admin(persona: str) -> str:
    """Ensure ``persona`` exists AND holds System Manager (the platform-admin
    signal ``_actor()`` reads). Returns the User name."""
    email = h.ensure_user(persona)
    doc = frappe.get_doc("User", email)
    if "System Manager" not in {r.role for r in doc.get("roles") or []}:
        doc.append("roles", {"role": "System Manager"})
        doc.flags.ignore_permissions = True
        doc.save(ignore_permissions=True)
    return email


def _last_event_row(sheet: str) -> dict:
    return frappe.db.get_value(
        "Tree Event",
        frappe.get_all(
            "Tree Event", filters={"sheet": sheet}, order_by="creation desc, name desc",
            limit_page_length=1, pluck="name",
        )[0],
        ["name", "type", "actor", "actor_type", "real_user", "impersonated_as", "change_request"],
        as_dict=True,
    )


# ===========================================================================
# (6) _actor() overlay: session.user stays admin; effective == impersonated
# ===========================================================================
def test_actor_overlay_effective_is_impersonated_real_is_admin(fx):
    """area (6): with an active session for an admin, ``frappe.session.user`` stays
    the admin but ``_actor().user`` == impersonated, ``real_user`` == admin, and the
    action is flagged impersonated."""
    admin = _make_admin(ADMIN)
    frappe.set_user(admin)
    api.begin_impersonation(impersonated_user=h.user("B"))

    # The framework boundary NEVER lies: session.user is still the real admin.
    assert frappe.session.user == admin
    actor = api._actor()
    assert actor.user == h.user("B")           # effective identity
    assert actor.real_user == admin            # traceable real principal
    assert actor.impersonated_as == h.user("B")
    assert actor.is_impersonated is True

    api.end_impersonation()
    after = api._actor()
    assert after.user == admin
    assert after.is_impersonated is False
    assert api.get_repository().get_active_impersonation(admin) is None


def test_acl_hints_reflect_impersonated_users_affordances(fx):
    """area (6): the snapshot viewer block is computed for the EFFECTIVE user, so an
    admin acting as B sees B's edit affordances (B owns col:name, not col:budget) and
    the impersonation banner fields are populated."""
    admin = _make_admin(ADMIN)
    frappe.set_user(admin)
    api.begin_impersonation(impersonated_user=h.user("B"), reason="reviewing B's view")

    snap = api.get_sheet_snapshot(sheet=fx["sheet"])
    viewer = snap["viewer"]
    assert viewer["impersonating"] is True
    assert viewer["real_user"] == admin
    assert viewer["effective_user"] == h.user("B")
    assert viewer["actor"] == h.user("B")

    by_field = {c["field"]: c for c in snap["columns"]}
    assert by_field["name"]["can_edit"] is True     # B owns col:name
    assert by_field["budget"]["can_edit"] is False   # C owns col:budget
    api.end_impersonation()


# ===========================================================================
# (7) full mutate under impersonation stamps BOTH Tree Event columns + CR
# ===========================================================================
def test_impersonated_mutate_stamps_tree_event_real_user(fx):
    """area (7): B owns col:name → an impersonated updateCell EXECUTES; the Tree Event
    row carries actor=B (effective) AND real_user=admin + impersonated_as=B."""
    admin = _make_admin(ADMIN)
    frappe.set_user(admin)
    api.begin_impersonation(impersonated_user=h.user("B"))

    out = api.update_cell(
        sheet=fx["sheet"], node=fx["nodes"]["X"],
        column=fx["columns"]["name"], value="renamed-by-admin-as-B",
    )
    assert out["kind"] == "executed"
    assert frappe.session.user == admin  # still the real admin at the boundary

    ev = _last_event_row(fx["sheet"])
    assert ev["type"] == "NODE_VALUE_UPDATED"
    assert ev["actor"] == h.user("B")          # effective identity on the event
    assert ev["real_user"] == admin            # traceable admin
    assert ev["impersonated_as"] == h.user("B")
    api.end_impersonation()


def test_impersonated_unauthorized_write_becomes_cr_with_real_requester(fx):
    """area (7): B does NOT own col:budget → an impersonated budget edit becomes a
    Change Request with requester=B (effective) + real_requester=admin; the linked
    CHANGE_PROPOSED event carries the same trace."""
    admin = _make_admin(ADMIN)
    frappe.set_user(admin)
    api.begin_impersonation(impersonated_user=h.user("B"))

    out = api.update_cell(
        sheet=fx["sheet"], node=fx["nodes"]["X"],
        column=fx["columns"]["budget"], value=123,
    )
    assert out["kind"] == "suggested"
    cr = frappe.get_doc("Change Request", out["change_request"])
    assert cr.requester == h.user("B")         # effective identity requests
    assert cr.real_requester == admin          # traceable admin
    assert cr.resolved_approver == h.user("C")  # C owns col:budget

    ev = _last_event_row(fx["sheet"])
    assert ev["type"] == "CHANGE_PROPOSED"
    assert ev["actor"] == h.user("B")
    assert ev["real_user"] == admin
    assert ev["impersonated_as"] == h.user("B")
    api.end_impersonation()


def test_normal_action_leaves_trace_columns_null(fx):
    """area (7) corollary: a NON-impersonated action leaves real_user/impersonated_as
    NULL — a normal event is byte-for-byte as before."""
    frappe.set_user(h.user("B"))
    out = api.update_cell(
        sheet=fx["sheet"], node=fx["nodes"]["Z"],
        column=fx["columns"]["name"], value="Zed",
    )
    assert out["kind"] == "executed"
    ev = _last_event_row(fx["sheet"])
    assert ev["actor"] == h.user("B")
    assert ev["real_user"] in (None, "")
    assert ev["impersonated_as"] in (None, "")


# ===========================================================================
# (8) fail-safe: revoke admin mid-session → overlay ignored + force-ended
# ===========================================================================
def test_admin_revocation_midsession_force_ends_overlay(fx):
    """area (8): an overlay persisted for a user who is NO LONGER admin must not grant
    lingering foreign identity — the next ``_actor()`` ignores it AND deactivates it."""
    admin = _make_admin(ADMIN)
    frappe.set_user(admin)
    api.begin_impersonation(impersonated_user=h.user("B"))
    assert api._actor().user == h.user("B")

    # Revoke System Manager mid-session (grant lost).
    doc = frappe.get_doc("User", admin)
    doc.set("roles", [r for r in doc.get("roles") if r.role != "System Manager"])
    doc.flags.ignore_permissions = True
    doc.save(ignore_permissions=True)
    frappe.clear_cache(user=admin)
    frappe.set_user(admin)

    actor = api._actor()
    assert actor.user == admin           # fell back to the real user
    assert actor.is_impersonated is False
    # The stale overlay was force-ended (fail-safe), not merely ignored.
    assert api.get_repository().get_active_impersonation(admin) is None


# ===========================================================================
# (9) REST parity + admin gate on begin/end
# ===========================================================================
def test_begin_impersonation_by_non_admin_is_403(fx):
    """area (9)/design: a NON-admin begin_impersonation → AuthorizationError → 403;
    no session row is written."""
    frappe.set_user(h.user("B"))  # a plain, non-admin persona
    # AuthorizationError → frappe.PermissionError (the 403 mapping; see _dispatch).
    # Matches the parity suite's convention of asserting the exception type (the
    # transport-level 403 status is set by Frappe's HTTP layer, not in-process).
    with pytest.raises(frappe.PermissionError):
        api.begin_impersonation(impersonated_user=h.user("C"))
    assert api.get_repository().get_active_impersonation(h.user("B")) is None


def test_begin_end_emit_no_tree_event(fx):
    """area (9)/design: begin/end are OFF the closed 11-event set — they emit NO Tree
    Event; the Arbor Impersonation Session row is the audit record instead."""
    admin = _make_admin(ADMIN)
    frappe.set_user(admin)
    before = frappe.db.count("Tree Event")
    out = api.begin_impersonation(impersonated_user=h.user("B"))
    assert out["kind"] == "executed"
    assert out["data"]["impersonating"] == h.user("B")
    sess = api.get_repository().get_active_impersonation(admin)
    assert sess and sess["impersonated_user"] == h.user("B")

    end = api.end_impersonation()
    assert end["data"]["impersonating"] is None
    assert frappe.db.count("Tree Event") == before  # no begin/end events landed


def test_end_impersonation_is_idempotent(fx):
    """area (9): end with no active overlay is a no-op success (idempotent)."""
    admin = _make_admin(ADMIN)
    frappe.set_user(admin)
    out = api.end_impersonation()
    assert out["kind"] == "executed"
    assert out["data"]["impersonating"] is None


# ===========================================================================
# (10) whoami returns real_user + impersonating
# ===========================================================================
def test_whoami_reports_impersonation_overlay(fx):
    """area (10): whoami reads the SAME overlay _actor() applies, so the banner + grid
    agree: user=effective, real_user=admin, impersonating=True."""
    try:
        from arbor.auth.api import whoami
    except (ModuleNotFoundError, ImportError):  # pragma: no cover
        from arbor.auth import api as _auth  # type: ignore
        whoami = _auth.whoami

    admin = _make_admin(ADMIN)
    frappe.set_user(admin)

    normal = whoami()
    assert normal["authenticated"] is True
    assert normal["user"] == admin
    assert normal["real_user"] == admin
    assert normal["impersonating"] is False

    api.begin_impersonation(impersonated_user=h.user("B"))
    imp = whoami()
    assert imp["user"] == h.user("B")       # effective identity
    assert imp["real_user"] == admin        # authenticated admin
    assert imp["impersonating"] is True
    api.end_impersonation()


# ===========================================================================
# (11) internalReset is blocked while impersonating (real_user != user)
# ===========================================================================
def test_internal_reset_blocked_while_impersonating(fx):
    """area (11): even an admin cannot nuke data under someone else's name — while
    impersonating, the effective identity (B) is not admin, so internalReset is 403."""
    admin = _make_admin(ADMIN)
    frappe.set_user(admin)
    api.begin_impersonation(impersonated_user=h.user("B"))
    with pytest.raises(frappe.PermissionError):
        api.execute_action(
            action_id="internalReset",
            params={"sheet": fx["sheet"], "confirm": True},
        )
    api.end_impersonation()
