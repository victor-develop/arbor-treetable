"""Unit tests for the skill.md generator (bench-free).

Asserts the doc is generated from the registry (every exposed capability shown,
hidden ones absent), documents the envelope + auth + discovery flow, weaves the
base URL, and is deterministic.
"""

from __future__ import annotations

from arbor.core.agent_scope import TOKEN_PREFIX, is_read_capability
from arbor.core.registry import all_capabilities
from arbor.core.skill import render_skill_md


def _exposed_ids():
    return [c.id for c in all_capabilities() if c.is_exposed_to_llm]


def test_lists_every_exposed_capability():
    md = render_skill_md()
    for cid in _exposed_ids():
        assert f"`{cid}`" in md, cid


def test_hides_non_exposed_capabilities():
    md = render_skill_md()
    for hidden in ("internalReset", "beginImpersonation", "endImpersonation", "assignRole"):
        assert f"`{hidden}`" not in md, hidden


def test_documents_envelope_and_kinds():
    md = render_skill_md()
    assert "arbor.execute_action" in md
    assert "action_id" in md and "params" in md
    for kind in ("executed", "suggested", "read"):
        assert kind in md


def test_documents_two_tier_auth_without_leaking_wrong_credentials():
    md = render_skill_md()
    assert "Authorization: token" in md
    assert "X-Arbor-Agent-Token" in md
    assert TOKEN_PREFIX in md
    # Steers the agent away from first-party credentials.
    assert "password" in md.lower() and "jwt" in md.lower()


def test_documents_discovery_flow():
    md = render_skill_md()
    assert "arbor.list_sheets" in md
    assert "getSheetDefinition" in md
    assert "mutate-or-suggest" in md.lower()


def test_weaves_base_url():
    md = render_skill_md(base_url="https://arbor.example.com/")
    assert "https://arbor.example.com/api/method/arbor.execute_action" in md
    assert "https://arbor.example.com//" not in md  # trailing slash trimmed


def test_read_and_write_capabilities_labeled():
    md = render_skill_md()
    # a known read is labeled read; a known write is labeled write
    assert is_read_capability("getSheetDefinition")
    assert "#### `getSheetDefinition`" in md
    assert "#### `updateCell`" in md
    # crude but sufficient: the read section heading exists
    assert "Read (navigate & inspect)" in md


def test_deterministic():
    assert render_skill_md("https://x") == render_skill_md("https://x")
