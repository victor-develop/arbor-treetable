# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Branch Grant controller (DATA-MODEL §5).

Delegable structural ownership of a sub-branch (Axis-1). Created by
``delegateBranch`` and deactivated by ``revokeDelegation`` (both emit
``DELEGATION_CHANGED``) through ``arbor.core`` + the adapter. The nearest active
grant on the ancestor chain wins — resolution lives in ``arbor.core.acl``, NOT
here. Controller only guards that branch_root belongs to the sheet.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class BranchGrant(Document):
	def validate(self) -> None:
		self._validate_branch_root_sheet()

	def _validate_branch_root_sheet(self) -> None:
		root_sheet = frappe.db.get_value("Tree Node", self.branch_root, "sheet")
		if root_sheet and root_sheet != self.sheet:
			frappe.throw(
				f"Branch root {self.branch_root} belongs to sheet {root_sheet}, not {self.sheet}."
			)
