# Copyright (c) 2026, Arbor and contributors
# For license information, please see license.txt
"""Arbor Agent Token controller (external-agent auth, "two-tier auth" design).

The persisted half of an external LLM agent's credential: the keyed hash of a
token secret + the scope it grants. The plaintext secret is shown to the issuer
exactly once (arbor.arbor.api.issue_agent_token) and never stored, so this row
can authenticate a request but can never reconstruct the secret.

A token is a CEILING, never a widening: it resolves to ``user`` and every action
still runs under that user's two-axis ACL + mutate-or-suggest executor. The scope
here (:meth:`to_scope`) is an ADDITIONAL gate applied at dispatch. The scope
decision itself lives in the pure core (arbor.core.agent_scope); this controller
only turns stored fields into an :class:`~arbor.core.agent_scope.AgentScope`.
"""

from __future__ import annotations

import json

import frappe
from frappe.model.document import Document

try:  # pure core (framework-free); import lazily-safe for lint on a bench-free checkout
    from arbor.core.agent_scope import AgentScope
except (ModuleNotFoundError, ImportError):  # pragma: no cover
    from arbor.arbor.core.agent_scope import AgentScope  # type: ignore


class ArborAgentToken(Document):
	def to_scope(self) -> "AgentScope":
		"""Build the pure-core :class:`AgentScope` this token grants."""
		sheets = None
		raw = (self.sheets or "").strip()
		if raw:
			try:
				parsed = json.loads(raw)
				if isinstance(parsed, list) and parsed:
					sheets = frozenset(str(s) for s in parsed)
			except (ValueError, TypeError):
				# A malformed sheets field must FAIL CLOSED (deny everything sheet-
				# scoped) rather than silently widening to all sheets.
				sheets = frozenset()
		return AgentScope(mode=self.mode or "write", sheets=sheets)

	def is_live(self) -> bool:
		"""True iff the token may still authenticate (not revoked, not expired)."""
		if self.revoked:
			return False
		if self.expires_on and frappe.utils.get_datetime(self.expires_on) < frappe.utils.now_datetime():
			return False
		return True
