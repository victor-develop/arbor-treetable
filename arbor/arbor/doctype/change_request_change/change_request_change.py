# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Change Request Change — one deferred capability call within a (multi-change)
Change Request. A single-change CR uses the parent's scalar fields and leaves
``changes`` empty; a batch CR lists each change here and is approved/applied
atomically (DECISIONS: multi-change CRs)."""

from __future__ import annotations

from frappe.model.document import Document


class ChangeRequestChange(Document):
	pass
