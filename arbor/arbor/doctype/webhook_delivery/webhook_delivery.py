# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Webhook Delivery controller (DATA-MODEL §11).

One delivery-attempt log row. The webhook dispatcher + retry worker (adapter)
drive status transitions using the backoff schedule in ``arbor.core.backoff``
(0s, 30s, 5m, 30m, 2h, 12h; up to MAX_ATTEMPTS). ``delivered`` on 2xx; otherwise
reschedule until ``exhausted``. Pure log row; no logic here.
"""

from __future__ import annotations

from frappe.model.document import Document


class WebhookDelivery(Document):
	pass
