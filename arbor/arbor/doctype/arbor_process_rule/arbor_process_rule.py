# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Arbor Process Rule child table controller.

One trigger->expectation rule of an Arbor Process (the DAG replacement for the
old ordered stage list). A rule reads "On <trigger>: expect (colA [and colB...])
filled within <within_seconds>", where trigger is a row create/update or a
specific ``trigger_column`` create/update. Rules compose into a DAG because a
column EXPECTED by one rule can be the ``trigger_column`` of another.

Pure data; the DAG evaluator + cycle/reachability validation live in
``arbor.core.process`` / ``arbor.core.process_graph``. This controller stays thin.
"""

from __future__ import annotations

from frappe.model.document import Document


class ArborProcessRule(Document):
	pass
