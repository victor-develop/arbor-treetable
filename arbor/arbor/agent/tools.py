"""Agent tool binding (ARCHITECTURE §8, CAPABILITIES.md ``getLLMTools``).

DRY: the agent's tools are the ONE capability registry filtered to
``is_exposed_to_llm`` — nothing is re-declared here. This module exists only to
give the agent surface a stable import (``arbor.agent.tools.get_llm_tools``) that
forwards to ``arbor.core.registry.get_llm_tools`` (so ``internalReset`` is hidden
exactly because of the registry filter, not a missing record — AGENT-001/002).
"""

from __future__ import annotations

from typing import Any

from arbor.core.registry import get_llm_tools as _core_get_llm_tools


def get_llm_tools() -> list[dict[str, Any]]:
    """The LLM-exposed capabilities as tool defs (forwards to the core registry)."""
    return _core_get_llm_tools()


def exposed_tool_names() -> set[str]:
    """Convenience: the set of tool names the agent may dispatch. Used by the
    loop's unknown/hidden-tool guard so a hallucinated ``internalReset`` is
    refused instead of executed (AGENT-004)."""
    return {t["function"]["name"] for t in get_llm_tools()}
