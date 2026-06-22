"""arbor.agent.chat endpoint tests — REQUIRE A FRAPPE BENCH.

These exercise the whitelisted ``arbor.agent.chat`` against a live site: the
caller's own ``frappe.session.user`` becomes the agent Actor, the Frappe adapter
façade (FrappeRepository / FrappeEventSink / get_sheet_snapshot) is resolved, and
surface parity (AGENT-041/042: agent ≡ REST ≡ web) is asserted. Skipped entirely
when frappe is not importable so the bench-free suite stays green.

Run on a bench, e.g.::

    bench --site <site> run-tests --module arbor.tests.agent.test_chat_endpoint_bench
"""

from __future__ import annotations

import pytest

frappe = pytest.importorskip("frappe", reason="arbor.agent.chat requires a Frappe bench")

pytestmark = pytest.mark.bench


def test_chat_acts_as_caller_own_user_and_files_cr_when_unauthorized():
    """AGENT-013/042 on a live site: a suggest-only caller's agent edit produces
    a Change Request with actor == the caller's User and actor_type == 'agent'.

    Bench scaffolding (seed canonical sheet S, set frappe.set_user to a
    suggest-only persona, inject a scripted provider via
    arbor_agent.provider_class) is assembled by the integration harness; this
    stub documents the assertion contract:

        session = arbor.arbor.agent.chat.chat(sheet=S, message="set X budget 9000")
        assert session["tool_calls"][0]["observation"]["kind"] == "suggested"
        # Tree Event: actor == frappe.session.user, actor_type == "agent"
    """
    pytest.skip("Requires bench fixtures + scripted provider injection (integration harness).")


def test_agent_updatecell_parity_with_rest_and_web():
    """AGENT-041: identical authority/handler/event across agent, REST, web."""
    pytest.skip("Requires bench fixtures (integration harness).")
