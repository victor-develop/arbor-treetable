"""Shared bench-side helpers for the backend integration suite (DRY).

NEEDS A FRAPPE BENCH + SITE. Imported only from ``@pytest.mark.bench`` modules,
so it imports ``frappe`` at module load; the package's ``conftest`` skips bench
tests (and therefore never imports this) on a bench-free checkout.

Everything here binds to the REAL seams the lanes shipped:

* ``arbor.api``                      — the whitelisted REST funnel (one method per
  capability) → ``arbor.core.executor.execute_action``.
* ``arbor.adapter.seed``             — the canonical bench seed (mirrors the pure
  fixture ``tests/fixtures/canonical.py`` field-for-field; proven by
  ``tests/adapter/test_seed_parity.py``).
* ``arbor.dispatch.frappe_dispatch`` — the notification dispatcher + the
  ``accountability`` aggregate.

NO governance/ACL/dispatch logic is re-implemented here; helpers only *invoke*
the shipped code and *query* the resulting rows.
"""

from __future__ import annotations

from typing import Any, Optional

import frappe

try:  # ``arbor.adapter`` on a bench; ``arbor.arbor.adapter`` in the dev repo.
    from arbor.adapter.seed import seed_canonical_sheet
except ModuleNotFoundError:  # pragma: no cover - dev-layout fallback
    from arbor.arbor.adapter.seed import seed_canonical_sheet  # type: ignore

try:
    from arbor.dispatch import frappe_dispatch
except ModuleNotFoundError:  # pragma: no cover - dev-layout fallback
    from arbor.arbor.dispatch import frappe_dispatch  # type: ignore


# ---------------------------------------------------------------------------
# Persona identity
# ---------------------------------------------------------------------------
def user(persona: str) -> str:
    """Persona label → Frappe User name (the seed creates ``<P>@arbor.example``).

    Lowercased to match Frappe's email canonicalization (and the seed's ``_user``),
    so ``set_user``/ACL comparisons line up with the stored Link values.
    """
    return (persona if "@" in persona else f"{persona}@arbor.example").lower()


def login_as(persona: str) -> None:
    """Switch the session to a persona (the actor every capability funnels)."""
    frappe.set_user(user(persona))


def ensure_user(persona: str) -> str:
    """Create an auxiliary persona User if absent (idempotent). The canonical seed
    only creates A..G, EXT, AGENT; the nested-delegation (``D2``) and
    multi-watcher (``G2``) cases need one more identity each. Returns the User
    name. Mirrors ``arbor.adapter.seed.ensure_personas`` for a single user."""
    email = user(persona)
    if not frappe.db.exists("User", email):
        doc = frappe.new_doc("User")
        doc.email = email
        doc.first_name = persona
        doc.send_welcome_email = 0
        doc.enabled = 1
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
    return email


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
def seed(settings: dict | None = None) -> dict[str, Any]:
    """Build the ONE canonical sheet `S` on the current site and return the record
    names dict ``{sheet, nodes, columns, grant_P2, personas}``."""
    return seed_canonical_sheet(settings=settings)


# ---------------------------------------------------------------------------
# Tree Event stream queries (read-only; the sink is the only writer)
# ---------------------------------------------------------------------------
def events_for_sheet(sheet: str) -> list[dict[str, Any]]:
    return frappe.get_all(
        "Tree Event",
        filters={"sheet": sheet},
        fields=["name", "type", "actor", "actor_type", "change_request", "payload", "creation"],
        order_by="creation asc, name asc",
    )


def events_of_cr(change_request: str) -> list[dict[str, Any]]:
    """Tree Events linked to a CR, chronological. NOTE: the mutation event a CR
    replays carries ``change_request=None`` on the event row (it is a normal
    direct mutation); it is linked instead via ``CR.resulting_event``. So this
    returns the lifecycle events (PROPOSED / APPROVED / REJECTED)."""
    return frappe.get_all(
        "Tree Event",
        filters={"change_request": change_request},
        fields=["name", "type", "actor", "actor_type", "payload", "creation"],
        order_by="creation asc, name asc",
    )


def last_event(sheet: str) -> Optional[dict[str, Any]]:
    rows = events_for_sheet(sheet)
    return rows[-1] if rows else None


def event_count(sheet: str) -> int:
    return frappe.db.count("Tree Event", {"sheet": sheet})


def cr_row(change_request: str) -> dict[str, Any]:
    return frappe.db.get_value(
        "Change Request",
        change_request,
        ["name", "status", "requester", "resolved_approver", "decided_by",
         "resulting_event", "target_kind", "operation"],
        as_dict=True,
    )


def cr_payload(change_request: str) -> dict[str, Any]:
    raw = frappe.db.get_value("Change Request", change_request, "payload")
    return frappe.parse_json(raw) if isinstance(raw, str) and raw else (raw or {})


# ---------------------------------------------------------------------------
# Cell values
# ---------------------------------------------------------------------------
def cell_value(node: str, column: str) -> Any:
    name = frappe.db.get_value("Tree Node Value", {"node": node, "column": column}, "name")
    if not name:
        return None
    raw = frappe.db.get_value("Tree Node Value", name, "value")
    return frappe.parse_json(raw) if isinstance(raw, str) and raw else raw


def cell_version(node: str, column: str) -> Optional[int]:
    name = frappe.db.get_value("Tree Node Value", {"node": node, "column": column}, "name")
    if not name:
        return None
    return int(frappe.db.get_value("Tree Node Value", name, "version") or 0)


# ---------------------------------------------------------------------------
# Dispatcher (notification fan-out) — hook-independent driver
# ---------------------------------------------------------------------------
def dispatch_pending_events(sheet: str) -> None:
    """Run the notification dispatcher over every Tree Event of ``sheet`` that has
    no Notification rows yet, so tests do not depend on whether the integrator has
    wired the ``after_insert`` doc_event into ``hooks.py``.

    Dedup is the dispatcher's own job (one Notification per
    ``(tree_event, recipient, channel)``), so this is safe to call repeatedly and
    safe even if a real hook also fired.
    """
    for ev in frappe.get_all("Tree Event", filters={"sheet": sheet}, pluck="name"):
        doc = frappe.get_doc("Tree Event", ev)
        frappe_dispatch.on_tree_event_insert(doc)


def notifications_for(tree_event: str) -> list[dict[str, Any]]:
    return frappe.get_all(
        "Notification",
        filters={"tree_event": tree_event},
        fields=["name", "tree_event", "change_request", "recipient", "channel", "requires_ack"],
        order_by="creation asc",
    )


def notifications_for_recipient(tree_event: str, recipient: str) -> list[dict[str, Any]]:
    return frappe.get_all(
        "Notification",
        filters={"tree_event": tree_event, "recipient": user(recipient)},
        fields=["name", "tree_event", "change_request", "recipient", "channel", "requires_ack"],
    )


def notifications_for_cr(change_request: str) -> list[dict[str, Any]]:
    return frappe.get_all(
        "Notification",
        filters={"change_request": change_request},
        fields=["name", "tree_event", "change_request", "recipient", "channel", "requires_ack"],
        order_by="creation asc",
    )


def acks_for(notification: str) -> list[dict[str, Any]]:
    return frappe.get_all(
        "Acknowledgement",
        filters={"notification": notification},
        fields=["name", "notification", "user", "acked_at"],
    )


def accountability(*, tree_event: str | None = None, change_request: str | None = None) -> dict[str, int]:
    """The shipped "N notified / M acked" aggregate (only ``requires_ack`` rows
    count toward the denominator)."""
    return frappe_dispatch.accountability(tree_event=tree_event, change_request=change_request)
