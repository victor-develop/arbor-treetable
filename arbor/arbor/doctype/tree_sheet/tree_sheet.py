# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Tree Sheet controller (thin).

A Tree Sheet is the root governance object (DATA-MODEL §1). It carries the
``structural_owner`` (Axis-1 terminal approver) and a ``settings`` JSON of policy
flags. NO mutation/ACL logic lives here — that is owned by ``arbor.core`` and the
Frappe adapter (FrappeRepository / execute_action). The controller only enforces
local field integrity.
"""

from __future__ import annotations

import json

import frappe
from frappe.model.document import Document


class TreeSheet(Document):
	def validate(self) -> None:
		_normalize_json_field(self, "settings", default={})


def _normalize_json_field(doc: Document, fieldname: str, default) -> None:
	"""Coerce a JSON field to a serialized object, defaulting empties."""
	value = doc.get(fieldname)
	if value in (None, ""):
		doc.set(fieldname, json.dumps(default))
		return
	if isinstance(value, (dict, list)):
		doc.set(fieldname, json.dumps(value))
		return
	# string: validate it parses
	try:
		json.loads(value)
	except (ValueError, TypeError):
		frappe.throw(f"{fieldname} must be valid JSON")
