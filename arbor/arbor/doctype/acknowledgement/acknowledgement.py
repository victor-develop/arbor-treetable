# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Acknowledgement controller (DATA-MODEL §9).

One row per (notification, user), set by the ``acknowledge`` capability through
``arbor.core`` + the adapter (no Tree Event emitted). Enforces the
(notification, user) uniqueness constraint locally.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class Acknowledgement(Document):
	def validate(self) -> None:
		clash = frappe.db.exists(
			"Acknowledgement",
			{"notification": self.notification, "user": self.user, "name": ("!=", self.name)},
		)
		if clash:
			frappe.throw(
				f"User {self.user} has already acknowledged notification {self.notification}",
				frappe.DuplicateEntryError,
			)
