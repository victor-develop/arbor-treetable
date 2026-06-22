"""Agent provider configuration seam (ARCHITECTURE §8).

The agent is **provider-agnostic** via LiteLLM. The default model is **Claude**
(``claude-sonnet-4-6``); an org may swap providers/models and **brings its own
key**, stored per-site — never hardcoded in the open-source core or this lane.

This module is intentionally a thin, dependency-light reader so that:

- the provider can be unit-tested **without a bench** by passing an explicit
  ``conf`` dict (AGENT-036, AGENT-038), and
- on a live site it transparently reads ``frappe.conf`` / Site Config.

Recognized site-config keys (all optional; under an ``arbor_agent`` namespace or
flat with an ``arbor_agent_`` prefix):

    arbor_agent = {
        "provider_class": "arbor.arbor.agent.provider.LiteLLMProvider",
        "model":          "claude-sonnet-4-6",
        "api_key":        "<org key>",        # per-site; never in source
        "api_base":       "https://...",      # optional custom gateway
        "max_steps":      12,
        "extra":          { ... }              # passed through to litellm.completion
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

#: Default model id (LiteLLM-style). Claude per ARCHITECTURE §8. Configurable.
DEFAULT_MODEL = "claude-sonnet-4-6"

#: Default fully-qualified provider class (the LiteLLM adapter in this lane).
DEFAULT_PROVIDER_CLASS = "arbor.arbor.agent.provider.LiteLLMProvider"

#: Default Re-Act step budget (matches core ``run_agent`` default).
DEFAULT_MAX_STEPS = 12

_NAMESPACE = "arbor_agent"


@dataclass(frozen=True)
class AgentConfig:
    """Resolved, immutable agent configuration."""

    provider_class: str = DEFAULT_PROVIDER_CLASS
    model: str = DEFAULT_MODEL
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    max_steps: int = DEFAULT_MAX_STEPS
    extra: dict[str, Any] = field(default_factory=dict)


def _read_site_conf() -> dict[str, Any]:
    """Best-effort read of Frappe site config. Returns ``{}`` off-bench so the
    provider/config are importable and unit-testable without a site."""
    try:  # pragma: no cover - exercised only on a live bench
        import frappe  # type: ignore

        conf = getattr(frappe, "conf", None)
        if conf is None:
            return {}
        return dict(conf)
    except Exception:  # pragma: no cover - no bench in pure tests
        return {}


def load_config(conf: Optional[dict[str, Any]] = None) -> AgentConfig:
    """Resolve the agent config from ``conf`` (or the live site config).

    Accepts both a nested ``{"arbor_agent": {...}}`` block and flat
    ``arbor_agent_<key>`` entries; nested wins on conflict. Unspecified values
    fall back to the documented defaults (so an org that configures nothing gets
    the default Claude model — AGENT-036).
    """
    raw = dict(conf) if conf is not None else _read_site_conf()

    nested = raw.get(_NAMESPACE) or {}
    if not isinstance(nested, dict):
        nested = {}

    def pick(key: str, default: Any = None) -> Any:
        if key in nested and nested[key] is not None:
            return nested[key]
        flat = f"{_NAMESPACE}_{key}"
        if raw.get(flat) is not None:
            return raw[flat]
        return default

    return AgentConfig(
        provider_class=pick("provider_class", DEFAULT_PROVIDER_CLASS),
        model=pick("model", DEFAULT_MODEL),
        api_key=pick("api_key", None),
        api_base=pick("api_base", None),
        max_steps=int(pick("max_steps", DEFAULT_MAX_STEPS)),
        extra=dict(pick("extra", {}) or {}),
    )
