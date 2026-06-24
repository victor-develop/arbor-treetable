# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Arbor Role Grant controller (Feature: role management).

The held-role FACT (analog of Branch Grant, role-scoped + site-wide) — the
SINGLE source of truth for who holds which role. Created by ``assignRole`` (admin
direct) or on approval of an Arbor Role Application, both through ``arbor.core``
+ the adapter. Enforces at most ONE active grant per (role, grantee).
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class ArborRoleGrant(Document):
	def validate(self) -> None:
		self._enforce_single_active()

	def _enforce_single_active(self) -> None:
		if not self.active:
			return
		dupe = frappe.db.exists(
			"Arbor Role Grant",
			{
				"role": self.role,
				"grantee": self.grantee,
				"active": 1,
				"name": ["!=", self.name or ""],
			},
		)
		if dupe:
			raise frappe.DuplicateEntryError(
				f"{self.grantee} already holds an active grant of role {self.role}."
			)
