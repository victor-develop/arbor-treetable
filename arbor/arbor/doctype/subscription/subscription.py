# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Subscription controller (DATA-MODEL §7).

Who watches what. Created/removed by ``subscribe`` / ``unsubscribe`` (emit
``SUBSCRIPTION_CHANGED``) through ``arbor.core`` + the adapter. The notification
dispatcher matches events against subscriptions (branch-scope uses the NestedSet
descendant range). This controller only keeps the Dynamic Link's
``target_doctype`` consistent with ``scope`` so the FK resolves correctly.
"""

from __future__ import annotations

from frappe.model.document import Document

_SCOPE_TO_DOCTYPE = {
	"sheet": "Tree Sheet",
	"branch": "Tree Node",
	"column": "Tree Column",
}


class Subscription(Document):
	def validate(self) -> None:
		# Keep the Dynamic Link target_doctype in lockstep with the scope.
		self.target_doctype = _SCOPE_TO_DOCTYPE.get(self.scope, "Tree Sheet")
