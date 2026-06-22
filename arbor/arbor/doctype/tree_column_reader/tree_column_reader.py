# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Tree Column Reader child table controller (Feature 3 read-ACL, LEAN).

Each row names a user granted read access to the parent Tree Column when its
``read_level`` is ``explicit-readers``. Pure data; no logic.
"""

from __future__ import annotations

from frappe.model.document import Document


class TreeColumnReader(Document):
	pass
