# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Arbor Role controller (Feature: role management).

A site-wide persona (PM/Developer/Marketing...). NOT sheet-scoped. ``applicable``
gates user self-application; ``active`` soft-retires it. The catalog is plain
admin-seeded data — never derived from the auth provider. Resolution of who
HOLDS a role lives in Arbor Role Grant (the fact), not here.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class ArborRole(Document):
	def validate(self) -> None:
		# Normalize the key so it is a stable, link-safe docname.
		if self.role:
			self.role = self.role.strip()
