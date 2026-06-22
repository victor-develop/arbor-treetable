"""Arbor server-side Re-Act agent (the ADAPTER lane, ARCHITECTURE §8).

This package is the Frappe-side wiring for the agent. The reasoning loop itself
lives in the framework-free core (``arbor.core.agentloop.run_agent``); this lane
adds only:

- ``provider``  — a LiteLLM ``LLMProvider`` (provider-agnostic; default Claude;
  swappable + per-site key) implementing the core protocol.
- ``tools``     — binds ``arbor.core.registry.get_llm_tools`` to the agent
  (the ONE registry; nothing re-declared here).
- ``react``     — ``run_agent_session``: builds the Frappe Repository + EventSink,
  the ``execute_tool`` closure that routes every tool call through the ONE
  ``core.execute_action`` as the caller's OWN Frappe User, and the streamed
  Thought/Action/Observation transcript.
- ``chat``      — the ``arbor.agent.chat`` whitelisted endpoint.

The agent owns ZERO mutation/ACL logic. Because it acts as the caller's own User,
an unauthorized agent tool call becomes a Change Request via the same
``execute_action`` path a human non-owner would hit — never a privileged bypass
(ARCHITECTURE §8, PERMISSIONS invariant #5).
"""

from __future__ import annotations
