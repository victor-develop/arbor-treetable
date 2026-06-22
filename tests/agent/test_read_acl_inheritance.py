"""RED — Feature 3 agent-inheritance: an Actor(actor_type=AGENT) reading through
the centralized executor / ``_dispatch_read`` inherits the SAME read-ACL filter
as a human.

The agent builds ``Actor(user=agent_user, actor_type=AGENT)`` and funnels every
read through ``execute_action`` -> ``_dispatch_control`` -> ``_dispatch_read`` ->
``explore.*``. Once ``_dispatch_read`` threads ``actor`` into the explore calls,
a column the agent's user cannot read is absent from its explore reads (and, by
the same ``visible_columns`` filter, its snapshot reads).

RED until the executor threads ``actor`` into the explore functions.
"""

from __future__ import annotations

from arbor.core.executor import execute_action
from arbor.core.testing import RecordingEventSink
from arbor.core.types import Actor, ActorType
from tests.fixtures.canonical import C, G, seed_canonical_sheet

AGENT_OUTSIDER = G  # an agent user with no read grant on col:budget


def _agent(user: str) -> Actor:
    return Actor(user, ActorType.AGENT)


def _lock_budget_owner_only(fx) -> None:
    fx.repo.update_column(fx.sheet, fx.col_budget, {"read_level": "owner-only", "readers": []})


def test_agent_getnode_omits_forbidden_column():
    fx = seed_canonical_sheet()
    _lock_budget_owner_only(fx)
    sink = RecordingEventSink()
    out = execute_action(
        "getNode",
        {"sheet": fx.sheet, "node": fx.X},
        _agent(AGENT_OUTSIDER),
        fx.repo,
        sink,
    )
    assert out.kind == "read"
    assert fx.col_budget not in out.data["values"]
    assert out.data["label"] == "Task X"  # label still visible


def test_agent_with_read_grant_sees_column():
    fx = seed_canonical_sheet()
    _lock_budget_owner_only(fx)  # C owns col:budget
    sink = RecordingEventSink()
    out = execute_action(
        "getNode",
        {"sheet": fx.sheet, "node": fx.X},
        _agent(C),  # agent acting as the column owner
        fx.repo,
        sink,
    )
    assert out.data["values"][fx.col_budget] == 1000


def test_agent_getcells_drops_forbidden_column():
    fx = seed_canonical_sheet()
    _lock_budget_owner_only(fx)
    sink = RecordingEventSink()
    out = execute_action(
        "getCells",
        {
            "sheet": fx.sheet,
            "nodes": [fx.X, fx.Y, fx.Z],
            "columns": [fx.col_name, fx.col_budget],
        },
        _agent(AGENT_OUTSIDER),
        fx.repo,
        sink,
    )
    for node, row in out.data["cells"].items():
        assert fx.col_budget not in row, node
        assert fx.col_name in row, node


def test_agent_search_does_not_leak_forbidden_value():
    fx = seed_canonical_sheet()
    _lock_budget_owner_only(fx)
    sink = RecordingEventSink()
    out = execute_action(
        "searchNodes",
        {"sheet": fx.sheet, "query": "12000"},  # a col:budget value
        _agent(AGENT_OUTSIDER),
        fx.repo,
        sink,
    )
    assert out.data["nodes"] == []
