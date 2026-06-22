"""LiteLLM ``LLMProvider`` adapter (ARCHITECTURE §8).

Implements the core ``arbor.core.ports.LLMProvider`` protocol over **LiteLLM**, so
the agent is provider-agnostic (Claude by default; Gemini/OpenAI/etc. by config).
The org **brings its own key** (per-site config — never hardcoded).

Responsibilities (and ONLY these):

1. Translate the registry tool defs (``get_llm_tools()`` — OpenAI function shape)
   into the provider-native schema and back, preserving ``name`` + JSON-schema
   (AGENT-037). LiteLLM consumes the OpenAI function shape natively and normalizes
   per-provider, so the round-trip is lossless.
2. Call ``litellm.completion`` and parse the assistant turn into the core's
   normalized shape ``{"content": str|None, "tool_calls":[{"id","name","arguments"}]}``
   that ``run_agent`` understands. Empty ``tool_calls`` ends the loop.

The adapter holds NO mutation/ACL/loop logic. ``litellm`` is imported lazily so
this module (and the config seam) import fine without the dependency or a network
— the mocked test-suite never imports it (AGENT-038).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .config import AgentConfig, load_config


class LiteLLMProvider:
    """Default provider adapter. Satisfies ``LLMProvider`` structurally."""

    def __init__(self, config: Optional[AgentConfig] = None) -> None:
        self.config = config or load_config()
        self.model = self.config.model

    # -- tool-schema translation (the registry <-> provider seam) -------------
    @staticmethod
    def to_provider_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Render core/registry tool defs into the provider-native schema.

        ``get_llm_tools()`` already emits the OpenAI ``{"type":"function",
        "function":{name,description,parameters}}`` shape, which LiteLLM accepts
        directly and re-maps per provider (e.g. Anthropic ``input_schema``). We
        pass it through unchanged but defensively normalize legacy/native shapes
        so any provider's tool def round-trips back to the same ``parameters``
        JSON-schema (AGENT-037).
        """
        out: list[dict[str, Any]] = []
        for t in tools:
            if "function" in t:  # already OpenAI/LiteLLM shape
                out.append(t)
                continue
            # Tolerate a native Anthropic-style def {name, description, input_schema}.
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", t["name"]),
                        "parameters": t.get("input_schema") or t.get("parameters", {}),
                    },
                }
            )
        return out

    # -- the LLMProvider protocol --------------------------------------------
    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """One assistant turn via LiteLLM, normalized to the core turn shape."""
        import litellm  # lazy: never imported by the mocked suite

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_provider_messages(messages),
            "tools": self.to_provider_tools(tools),
        }
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if self.config.api_base:
            kwargs["api_base"] = self.config.api_base
        kwargs.update(self.config.extra or {})

        response = litellm.completion(**kwargs)
        return self._parse_response(response)

    # -- (de)serialization helpers -------------------------------------------
    @staticmethod
    def _to_provider_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Make the loop's messages LiteLLM-safe.

        ``run_agent`` appends tool-result messages with a dict ``content`` (the
        observation). The OpenAI/LiteLLM wire format wants tool messages to carry
        a string ``content``, so JSON-encode any non-string tool content. Other
        roles pass through untouched.
        """
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.get("role") == "tool" and not isinstance(m.get("content"), str):
                m = dict(m, content=json.dumps(m.get("content")))
            elif m.get("role") == "assistant" and m.get("tool_calls"):
                # The core loop stores tool_calls in its own shape
                # ({id,name,arguments}); the OpenAI/LiteLLM wire format needs
                # {id,type:"function",function:{name,arguments(str)}} or providers
                # reject the follow-up turn ("Tool type cannot be empty").
                m = dict(
                    m,
                    tool_calls=[
                        c
                        if "function" in c
                        else {
                            "id": c.get("id") or c.get("name"),
                            "type": "function",
                            "function": {
                                "name": c.get("name"),
                                "arguments": c["arguments"]
                                if isinstance(c.get("arguments"), str)
                                else json.dumps(c.get("arguments") or {}),
                            },
                        }
                        for c in m["tool_calls"]
                    ],
                )
            out.append(m)
        return out

    @staticmethod
    def _parse_response(response: Any) -> dict[str, Any]:
        """Normalize a LiteLLM completion into ``{"content","tool_calls"}``."""
        choice = response.choices[0]
        msg = choice.message
        content = getattr(msg, "content", None)

        tool_calls: list[dict[str, Any]] = []
        for tc in getattr(msg, "tool_calls", None) or []:
            fn = getattr(tc, "function", None) or {}
            name = getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else None)
            raw_args = getattr(fn, "arguments", None)
            if raw_args is None and isinstance(fn, dict):
                raw_args = fn.get("arguments")
            tool_calls.append(
                {
                    "id": getattr(tc, "id", None) or name,
                    "name": name,
                    "arguments": _coerce_args(raw_args),
                }
            )
        return {"content": content, "tool_calls": tool_calls}


def _coerce_args(raw: Any) -> dict[str, Any]:
    """Tool-call arguments arrive as a JSON string (OpenAI) or a dict
    (Anthropic). Always hand the loop a dict."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


# ---------------------------------------------------------------------------
# Provider factory (the config-driven swap seam, AGENT-035/036).
# ---------------------------------------------------------------------------
def get_provider(config: Optional[AgentConfig] = None):
    """Instantiate the configured ``LLMProvider``.

    Honors ``provider_class`` from site config so an org can swap to a custom
    adapter without touching this lane. Defaults to ``LiteLLMProvider`` with the
    Claude model when nothing is configured (AGENT-036). The selected class is
    constructed with the resolved ``AgentConfig``.
    """
    cfg = config or load_config()
    cls = _import_provider_class(cfg.provider_class)
    try:
        return cls(cfg)
    except TypeError:
        # A custom provider with a no-arg constructor is still acceptable.
        return cls()


def _import_provider_class(path: str):
    """Resolve a dotted ``module.Class`` path; fall back to the default class for
    this lane's own path so it works whether imported as ``arbor.arbor.agent`` or
    ``arbor.agent`` (Frappe app aliasing)."""
    module_path, _, cls_name = path.rpartition(".")
    if cls_name == "LiteLLMProvider" and (
        module_path.endswith("agent.provider") or not module_path
    ):
        return LiteLLMProvider
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, cls_name)
