# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Arbor Role Application controller (Feature: role management).

The user self-application lifecycle — a CLEAN PARALLEL state machine to Change
Request (``proposed -> approved | rejected | withdrawn``). The transition logic
lives in ``arbor.core.role_app``; the controller only guards that a requester
has at most ONE open (proposed) application per role.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class ArborRoleApplication(Document):
	def validate(self) -> None:
		self._enforce_single_open()

	def _enforce_single_open(self) -> None:
		if self.status != "proposed":
			return
		dupe = frappe.db.exists(
			"Arbor Role Application",
			{
				"role": self.role,
				"requester": self.requester,
				"status": "proposed",
				"name": ["!=", self.name or ""],
			},
		)
		if dupe:
			raise frappe.DuplicateEntryError(
				f"{self.requester} already has an open application for role {self.role}."
			)
