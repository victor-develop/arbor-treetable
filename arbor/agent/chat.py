"""Public re-export shim: ``arbor.agent.chat`` -> ``arbor.arbor.agent.chat``.

Makes ``POST /api/method/arbor.agent.chat`` resolve to the single real Re-Act
agent endpoint. The ``chat`` callable self-decorates with ``frappe.whitelist()``
at import time on a live bench (see the real module), so importing it here is
enough to expose the documented path. No agent logic is duplicated.
"""

from __future__ import annotations

from arbor.arbor.agent.chat import chat  # noqa: F401

__all__ = ["chat"]
