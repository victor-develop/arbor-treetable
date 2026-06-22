# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Notification controller (DATA-MODEL §8).

One row per (tree_event, recipient), produced by the notification dispatcher off
the Tree Event stream — NEVER by capabilities directly. The accountability report
counts Notification rows with requires_ack=1 vs Acknowledgement rows. Pure data;
dispatch logic lives in the adapter's single notification dispatcher.

NOTE (integration): the DocType name "Notification" collides with Frappe's
built-in Notification DocType. See the lane manifest — the integrator must
resolve the name collision at install time.
"""

from __future__ import annotations

from frappe.model.document import Document


class Notification(Document):
	pass
