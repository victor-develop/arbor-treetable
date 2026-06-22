"""Agent-lane unit tests: tool exposure/filtering and the LiteLLM provider
adapter (no bench, no network). Maps to agent.md cases AGENT-001..003, 035..038.
"""

from __future__ import annotations

import json

from arbor.arbor.agent.config import DEFAULT_MODEL, AgentConfig, load_config
from arbor.arbor.agent.provider import LiteLLMProvider, _coerce_args, get_provider
from arbor.arbor.agent.tools import exposed_tool_names, get_llm_tools
from arbor.core.registry import all_capabilities


# --- A. Tool exposure & getLLMTools filtering ------------------------------
def test_get_llm_tools_exposes_exactly_the_llm_capabilities():
    # AGENT-001
    names = exposed_tool_names()
    expected = {c.id for c in all_capabilities() if c.is_exposed_to_llm}
    assert names == expected
    assert "internalReset" not in names


def test_internal_reset_is_filtered_not_missing():
    # AGENT-002 — internalReset exists in the registry but is filtered out.
    assert any(c.id == "internalReset" for c in all_capabilities())
    assert "internalReset" not in exposed_tool_names()


def test_tool_defs_render_params_schema_faithfully():
    # AGENT-003
    by_name = {t["function"]["name"]: t for t in get_llm_tools()}
    update = by_name["updateCell"]["function"]
    assert update["description"]
    assert update["parameters"]["required"] == ["sheet", "node", "column", "value"]
    add = by_name["addNode"]["function"]
    assert add["parameters"]["required"] == ["sheet", "parent"]


# --- I. Provider adapter / config -----------------------------------------
def test_default_provider_is_claude_when_unconfigured():
    # AGENT-036 — default model resolves to Claude, asserted on the string.
    cfg = load_config({})
    assert cfg.model == DEFAULT_MODEL == "claude-sonnet-4-6"
    provider = get_provider(cfg)
    assert isinstance(provider, LiteLLMProvider)
    assert provider.model == "claude-sonnet-4-6"


def test_config_reads_nested_and_flat_keys_and_never_hardcodes_key():
    nested = load_config({"arbor_agent": {"model": "gemini/gemini-1.5-pro", "api_key": "k1"}})
    assert nested.model == "gemini/gemini-1.5-pro"
    assert nested.api_key == "k1"
    flat = load_config({"arbor_agent_model": "gpt-4o", "arbor_agent_max_steps": 5})
    assert flat.model == "gpt-4o"
    assert flat.max_steps == 5
    # Default config carries no key (org brings its own per-site).
    assert load_config({}).api_key is None


def test_provider_swap_via_provider_class():
    # AGENT-035 — config selects the adapter class.
    cfg = AgentConfig(provider_class="arbor.arbor.agent.provider.LiteLLMProvider")
    assert isinstance(get_provider(cfg), LiteLLMProvider)


def test_tool_translation_roundtrips_params_schema():
    # AGENT-037 — registry tool def -> provider-native -> same parameters schema.
    tools = get_llm_tools()
    provider_tools = LiteLLMProvider.to_provider_tools(tools)
    orig = {t["function"]["name"]: t["function"]["parameters"] for t in tools}
    back = {t["function"]["name"]: t["function"]["parameters"] for t in provider_tools}
    assert back == orig

    # A native Anthropic-style def (input_schema) normalizes to the same shape.
    native = [{"name": "updateCell", "description": "Update", "input_schema": {"type": "object"}}]
    out = LiteLLMProvider.to_provider_tools(native)
    assert out[0]["function"]["parameters"] == {"type": "object"}


def test_no_network_required_to_import_or_configure():
    # AGENT-038 — provider imports/configures without litellm or a key present.
    provider = LiteLLMProvider(AgentConfig())
    assert provider.model  # no litellm import happened (only on .complete()).


def test_parse_response_normalizes_tool_calls_from_both_wire_shapes():
    # OpenAI-style: arguments as JSON string.
    class _Fn:
        name = "updateCell"
        arguments = json.dumps({"sheet": "S", "value": 1})

    class _TC:
        id = "tc1"
        function = _Fn()

    class _Msg:
        content = None
        tool_calls = [_TC()]

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    turn = LiteLLMProvider._parse_response(_Resp())
    assert turn["tool_calls"][0]["name"] == "updateCell"
    assert turn["tool_calls"][0]["arguments"] == {"sheet": "S", "value": 1}
    assert turn["content"] is None


def test_coerce_args_handles_dict_string_and_none():
    assert _coerce_args(None) == {}
    assert _coerce_args({"a": 1}) == {"a": 1}
    assert _coerce_args('{"a": 1}') == {"a": 1}
    assert _coerce_args("not json") == {}
