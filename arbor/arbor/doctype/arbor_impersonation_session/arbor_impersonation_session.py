# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Arbor Impersonation Session controller (Feature: traceable "act as").

The single source of truth for the active impersonation OVERLAY. It is
server-persisted so an impersonation window survives page reload and leaves a
permanent, auditable trail (started_at / ended_at) INDEPENDENT of the Tree
Events produced during it.

Impersonation is deliberately DECOUPLED from authentication: this row never
calls ``frappe.set_user()``. The real Frappe session stays authoritative; the
row is what ``_actor()`` reads to build an Actor with ``user=<impersonated>``,
``real_user=<real>``, ``impersonated_as=<impersonated>``.

Invariant: at most ONE active row per ``real_user``. This is enforced in the
``beginImpersonation`` handler (which deactivates any prior active row before
inserting a new one), NOT in the schema — mirroring how Arbor Cell Draft keeps
its single-per-cell rule at the controller/endpoint layer. The controller adds
a defensive guard so a second active row for the same real_user is rejected.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class ArborImpersonationSession(Document):
    def validate(self) -> None:
        self._enforce_single_active_per_real_user()

    def _enforce_single_active_per_real_user(self) -> None:
        if not self.active:
            return
        dupe = frappe.db.exists(
            "Arbor Impersonation Session",
            {
                "real_user": self.real_user,
                "active": 1,
                "name": ["!=", self.name or ""],
            },
        )
        if dupe:
            raise frappe.DuplicateEntryError(
                f"{self.real_user} already has an active impersonation session."
            )
