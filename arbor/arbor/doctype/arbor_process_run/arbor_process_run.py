# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Arbor Process Run controller (process DAG dataModel).

Per-row process state: which node (the 'row') is running which process and the
per-(rule, expected-column) expectation ledger (the ``expectations`` child
table). Runs are created/updated by the dispatch-lane consumer off the Tree
Event stream (NODE_CREATED starts a run + fires row rules; a NODE_VALUE_UPDATED
on a column satisfies pending expectations on it and fires column rules) — never
by a user capability. This controller stays thin: it only guards the (process,
node) uniqueness the schema cannot express.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class ArborProcessRun(Document):
	def validate(self) -> None:
		# The run's process link FIELD is ``arbor_process`` (NOT ``process``); the
		# uniqueness guard the schema cannot express is (arbor_process, node).
		dupes = frappe.get_all(
			"Arbor Process Run",
			filters={
				"arbor_process": self.arbor_process,
				"node": self.node,
				"name": ["!=", self.name or ""],
			},
			pluck="name",
		)
		if dupes:
			frappe.throw(
				f"A process run already exists for process {self.arbor_process!r} "
				f"and node {self.node!r} ({dupes[0]}); (arbor_process, node) is unique."
			)
