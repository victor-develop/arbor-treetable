# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Arbor Process Run controller (Area 3 dataModel).

Per-row process state: which node (the 'row') is running which process, the
active stage, and the per-stage timestamp ledger (the ``run_stages`` child
table). Runs are created/advanced by the dispatch-lane consumer off the Tree
Event stream (NODE_CREATED starts a run; a NODE_VALUE_UPDATED on the current
stage column advances it) — never by a user capability. This controller stays
thin: it only guards the (process, node) uniqueness the schema cannot express.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class ArborProcessRun(Document):
	def validate(self) -> None:
		dupes = frappe.get_all(
			"Arbor Process Run",
			filters={
				"process": self.process,
				"node": self.node,
				"name": ["!=", self.name or ""],
			},
			pluck="name",
		)
		if dupes:
			frappe.throw(
				f"A process run already exists for process {self.process!r} "
				f"and node {self.node!r} ({dupes[0]}); (process, node) is unique."
			)
