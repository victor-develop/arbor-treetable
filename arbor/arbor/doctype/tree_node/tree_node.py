# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Tree Node controller (DATA-MODEL §3).

The tree structure, backed by Frappe ``NestedSet`` (lft/rgt/parent_tree_node).
There is intentionally NO label/name content field — the human-visible label is
a Tree Node Value for the sheet's ``is_label`` column (ARCHITECTURE §2.3).

NestedSet maintains lft/rgt and the rebuild on parent change. Structural
mutation routing/ACL is owned by ``arbor.core`` + the FrappeRepository adapter
(create_node / move_node / delete_node); this controller stays thin and only
guards local integrity (parent must belong to the same sheet).
"""

from __future__ import annotations

import frappe
from frappe.utils.nestedset import NestedSet


class TreeNode(NestedSet):
	nsm_parent_field = "parent_tree_node"

	def validate(self) -> None:
		# NestedSet/Document expose no ``validate`` hook to chain to; run the
		# local integrity check directly.
		self._validate_parent_same_sheet()

	def _validate_parent_same_sheet(self) -> None:
		if not self.parent_tree_node:
			return
		parent_sheet = frappe.db.get_value("Tree Node", self.parent_tree_node, "sheet")
		if parent_sheet and parent_sheet != self.sheet:
			frappe.throw(
				f"Parent node {self.parent_tree_node} belongs to sheet {parent_sheet}, "
				f"not {self.sheet}; a node cannot cross sheets."
			)
