# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Arbor Process Stage child table controller.

One row per stage of an Arbor Process, ordered by the Frappe child-table idx
(left->right fill order). Each stage names the Tree Column whose owner fills it
and an optional per-transition SLA (seconds). Pure data; the stage machine lives
in ``arbor.core.process``.
"""

from __future__ import annotations

from frappe.model.document import Document


class ArborProcessStage(Document):
	pass
