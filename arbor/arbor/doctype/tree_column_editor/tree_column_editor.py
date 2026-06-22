# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Tree Column Editor child table controller (DATA-MODEL §2.1).

Each row names an additional owner-equivalent editor/approver for the parent
Tree Column (Axis-2 authority). Pure data; no logic.
"""

from __future__ import annotations

from frappe.model.document import Document


class TreeColumnEditor(Document):
	pass
