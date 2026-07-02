"""Process DAG / SLA / dashboard / inbox — real-adapter (bench) round-trip
(process DAG / WS-B1).

runnable: NEEDS FRAPPE BENCH (``@pytest.mark.bench``; auto-skips bench-free).

Exercises the WHOLE process lane on a live site through the shipped seams:

* ``arbor.api.define_process`` / ``enable_process`` (registry capabilities routed
  through the ONE executor by the structural owner A of the canonical sheet S),
  passing a trigger->expectation RULE set (NOT ordered stages);
* the dispatch-lane consumer wired by ``hooks.doc_events["Tree Event"]
  ["after_insert"]`` → ``on_tree_event_insert`` → the process consumer, driven off
  the SAME Tree Event stream: NODE_CREATED starts a run + opens the row rule's
  expectation (stageA, owner C) + notifies C; a NODE_VALUE_UPDATED on stageA
  satisfies it + fires the column rule opening stageB (owner B) + notifies B; the
  terminal fill of stageB completes the run (no notify);
* the SLA sweep marking an over-due OPEN expectation breached + notifying once;
* the read shims ``get_process`` (rules) / ``process_dashboard`` (edges) /
  ``list_process_runs`` (expectations) and the cross-sheet ``inbox()``.

Canonical sheet S: A is the structural owner; C owns col:budget; B owns col:notes.
The 2-rule DAG: row-trigger expects budget (stageA, owner C) within T; a
budget-column trigger expects notes (stageB, owner B) within T2. The column
EXPECTED by rule 1 (budget) is the TRIGGER of rule 2 — that composition IS the
DAG. Start / satisfy / complete run off the LIVE after_insert hook (so the wiring
itself is under test); the SLA sweep is scheduler-driven, so it is invoked
directly via ``ProcessDispatcher`` with a fixed test clock for determinism. The
bench harness rolls the transaction back between tests, so nothing persists.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.bench

frappe = pytest.importorskip("frappe")

from arbor import api  # noqa: E402

try:  # ``arbor.dispatch`` on a bench; ``arbor.arbor.dispatch`` in the dev repo.
    from arbor.dispatch import frappe_dispatch as fd
except ModuleNotFoundError:  # pragma: no cover - dev-layout fallback
    from arbor.arbor.dispatch import frappe_dispatch as fd  # type: ignore

from tests.backend import _helpers as h  # noqa: E402


class _FixedClock:
    """A clock pinned to a fixed ISO-8601 instant (string) so the SLA sweep is
    deterministic against the Datetime-typed expectation ledger."""

    def __init__(self, iso):
        self._iso = iso

    def now(self):
        return self._iso


@pytest.fixture()
def fx():
    data = h.seed()
    yield data
    frappe.set_user("Administrator")


def _rules(fx, within=(0, 0)):
    """The 2-rule DAG: row -> expect budget (stageA) within within[0];
    on budget -> expect notes (stageB) within within[1]."""
    return [
        {
            "rule_key": "stageA",
            "trigger_kind": "row",
            "trigger_op": "created",
            "expected_columns": [fx["columns"]["budget"]],
            "within_seconds": within[0],
        },
        {
            "rule_key": "stageB",
            "trigger_kind": "column",
            "trigger_column": fx["columns"]["budget"],
            "trigger_op": "created-or-updated",
            "expected_columns": [fx["columns"]["notes"]],
            "within_seconds": within[1],
        },
    ]


def _define_enable(fx, within=(0, 0)):
    """Define + enable the 2-rule DAG (budget -> notes) on S as owner A."""
    h.login_as("A")
    api.define_process(sheet=fx["sheet"], rules=_rules(fx, within), title="Fill order")
    api.enable_process(sheet=fx["sheet"])


def _run(process_name, node, *fields):
    rows = frappe.get_all(
        "Arbor Process Run",
        filters={"arbor_process": process_name, "node": node},
        fields=["name", *fields],
    )
    return rows[0] if rows else None


def _run_expectations(run_name):
    doc = frappe.get_doc("Arbor Process Run", run_name)
    return {
        (e.rule_key, e.expected_column): e for e in (doc.get("expectations") or [])
    }


def _process_notifs(recipient):
    return frappe.get_all(
        "Notification",
        filters={"recipient": h.user(recipient), "source": ["in", ["process", "sla"]]},
        fields=["name", "source", "requires_ack"],
    )


# ---------------------------------------------------------------------------
# define_process persists the rule DAG; get_process returns ProcessRuleView rows
# ---------------------------------------------------------------------------
def test_define_process_returns_rule_views(fx):
    _define_enable(fx)
    process = api.get_process(sheet=fx["sheet"])
    assert process["enabled"] is True
    assert {r["rule_key"] for r in process["rules"]} == {"stageA", "stageB"}
    by_key = {r["rule_key"]: r for r in process["rules"]}
    # row rule -> budget (owner C is a live-resolved expected owner; readable label).
    assert by_key["stageA"]["trigger_kind"] == "row"
    assert by_key["stageA"]["expected_columns"] == [fx["columns"]["budget"]]
    assert by_key["stageA"]["expected_labels"] == ["Budget"]
    assert h.user("C") in (by_key["stageA"]["expected_owners"][fx["columns"]["budget"]] or "")
    # column rule -> notes; the trigger column (budget) is the row rule's expected.
    assert by_key["stageB"]["trigger_kind"] == "column"
    assert by_key["stageB"]["trigger_column"] == fx["columns"]["budget"]
    assert by_key["stageB"]["expected_columns"] == [fx["columns"]["notes"]]


# ---------------------------------------------------------------------------
# start: NODE_CREATED (via the live after_insert hook) -> run + OPEN stageA
# expectation (owner C) notified
# ---------------------------------------------------------------------------
def test_node_created_opens_stageA_expectation_and_notifies_owner(fx):
    _define_enable(fx)
    process = api.get_process(sheet=fx["sheet"])

    h.login_as("A")
    node = api.add_node(sheet=fx["sheet"], parent=fx["nodes"]["R"])["data"]["node"]

    run = _run(process["name"], node, "status")
    assert run is not None and run["status"] == "active"
    exps = _run_expectations(run["name"])
    # stageA (budget) is OPEN (row created with no prefilled budget); stageB not yet.
    stageA = exps[("stageA", fx["columns"]["budget"])]
    assert stageA.satisfied_at is None and stageA.breached == 0
    assert ("stageB", fx["columns"]["notes"]) not in exps
    # stageA owner C notified via a source='process' FYI (requires_ack=0).
    notifs = _process_notifs("C")
    assert len(notifs) == 1
    assert notifs[0]["source"] == "process" and notifs[0]["requires_ack"] == 0


# ---------------------------------------------------------------------------
# prefilled-at-creation: adding a node WITH budget already filled satisfies
# stageA at creation AND cascades to open stageB
# ---------------------------------------------------------------------------
def test_node_created_with_prefilled_stageA_satisfies_and_cascades(fx):
    _define_enable(fx)
    process = api.get_process(sheet=fx["sheet"])

    h.login_as("A")
    node = api.add_node(
        sheet=fx["sheet"], parent=fx["nodes"]["R"], values={"budget": 999}
    )["data"]["node"]

    run = _run(process["name"], node, "status")
    exps = _run_expectations(run["name"])
    # stageA satisfied at creation (prefilled budget counts as filled) ...
    stageA = exps[("stageA", fx["columns"]["budget"])]
    assert stageA.satisfied_at is not None
    # ... and the budget-trigger cascaded to open stageB (notes), still open.
    stageB = exps[("stageB", fx["columns"]["notes"])]
    assert stageB.satisfied_at is None and stageB.breached == 0
    # stageB owner B was notified of the open expectation.
    assert len(_process_notifs("B")) == 1


# ---------------------------------------------------------------------------
# satisfy stageA -> open+notify stageB; fill stageB -> completed + dashboard
# ---------------------------------------------------------------------------
def test_fill_dag_in_order_completes_and_dashboard_edges(fx):
    _define_enable(fx)
    process = api.get_process(sheet=fx["sheet"])

    h.login_as("A")
    node = api.add_node(sheet=fx["sheet"], parent=fx["nodes"]["R"])["data"]["node"]

    # C fills budget (stageA) -> satisfy stageA, open stageB (notes), notify B.
    h.login_as("C")
    api.update_cell(sheet=fx["sheet"], node=node, column=fx["columns"]["budget"], value=42)
    run = _run(process["name"], node, "status")
    assert run["status"] == "active"
    exps = _run_expectations(run["name"])
    assert exps[("stageA", fx["columns"]["budget"])].satisfied_at is not None
    assert exps[("stageB", fx["columns"]["notes"])].satisfied_at is None
    assert len(_process_notifs("B")) == 1

    dash = api.process_dashboard(sheet=fx["sheet"])
    assert dash["total_active"] == 1 and dash["total_completed"] == 0
    # edge stageB (budget -> notes) has one pending expectation.
    edgeB = next(
        e for e in dash["edges"]
        if e["rule_key"] == "stageB" and e["to_column"] == fx["columns"]["notes"]
    )
    assert edgeB["from_column"] == fx["columns"]["budget"]
    assert edgeB["pending_count"] == 1 and edgeB["satisfied_count"] == 0
    # edge stageA (START -> budget) is satisfied.
    edgeA = next(e for e in dash["edges"] if e["rule_key"] == "stageA")
    assert edgeA["from_kind"] == "row" and edgeA["from_column"] is None
    assert edgeA["satisfied_count"] == 1

    # B fills notes (stageB) -> terminal completion (no further notify).
    h.login_as("B")
    api.update_cell(sheet=fx["sheet"], node=node, column=fx["columns"]["notes"], value="done")
    assert _run(process["name"], node, "status")["status"] == "completed"
    dash2 = api.process_dashboard(sheet=fx["sheet"])
    assert dash2["total_completed"] == 1 and dash2["throughput"] == 1


# ---------------------------------------------------------------------------
# list_process_runs drill-down carries the expectation ledger
# ---------------------------------------------------------------------------
def test_list_process_runs_returns_run_with_expectations(fx):
    _define_enable(fx)
    h.login_as("A")
    node = api.add_node(sheet=fx["sheet"], parent=fx["nodes"]["R"])["data"]["node"]

    runs = api.list_process_runs(sheet=fx["sheet"], status="active")
    run = next(r for r in runs if r["node"] == node)
    assert run["status"] == "active"
    keys = {(e["rule_key"], e["expected_column"]) for e in run["expectations"]}
    assert ("stageA", fx["columns"]["budget"]) in keys
    # edge drill-down: filter to runs carrying an expectation on stageA.
    filtered = api.list_process_runs(sheet=fx["sheet"], rule_key="stageA")
    assert any(r["node"] == node for r in filtered)


# ---------------------------------------------------------------------------
# SLA sweep marks an over-due OPEN expectation breached + notifies once
# (scheduler-driven, so invoked directly with a fixed test clock)
# ---------------------------------------------------------------------------
def test_sla_sweep_marks_breach_and_notifies(fx):
    _define_enable(fx, within=(50, 0))  # stageA SLA = 50s
    process = api.get_process(sheet=fx["sheet"])

    d = fd.ProcessDispatcher()
    h.login_as("A")
    node = api.add_node(sheet=fx["sheet"], parent=fx["nodes"]["R"])["data"]["node"]

    # stageA opened by the live hook; pin its ledger to fixed ISO datetimes so the
    # sweep is deterministic against the Datetime column (opened 2026-01-01 00:00,
    # due 2026-01-01 00:00:50 given the 50s SLA).
    run_name = _run(process["name"], node, "name")["name"]
    existing = d.repo.get_process_run(process["name"], node)
    exps = [dict(e) for e in existing["expectations"]]
    for e in exps:
        if e["rule_key"] == "stageA":
            e["opened_at"] = "2026-01-01 00:00:00"
            e["due_at"] = "2026-01-01 00:00:50"
            e["breached"] = False
    d.repo.update_process_run(run_name, {"expectations": exps})

    # sweep before due -> no breach.
    d.clock = _FixedClock("2026-01-01 00:00:10")
    assert d.sla_sweep() == []
    # sweep after due -> breach + notify stageA owner C once.
    d.clock = _FixedClock("2026-01-01 00:01:00")
    breached = d.sla_sweep()
    assert [t["kind"] for t in breached] == ["breached"]

    fresh = frappe.get_doc("Arbor Process Run", run_name)
    s0 = next(e for e in fresh.expectations if e.rule_key == "stageA")
    assert s0.breached == 1
    assert any(n["source"] == "sla" for n in _process_notifs("C"))
    # idempotent: a second sweep does not re-breach / re-notify.
    d.clock = _FixedClock("2026-01-01 00:02:00")
    assert d.sla_sweep() == []


# ---------------------------------------------------------------------------
# inbox() surfaces the process notification for the responsible owner across
# sheets, deep-linked to {sheet, node} (even after the expectation resolves)
# ---------------------------------------------------------------------------
def test_inbox_shows_process_notification_to_expected_owner(fx):
    _define_enable(fx)

    h.login_as("A")
    node = api.add_node(sheet=fx["sheet"], parent=fx["nodes"]["R"])["data"]["node"]

    # C fills budget (stageA) -> satisfy stageA, open stageB (notes), notify B.
    h.login_as("C")
    api.update_cell(sheet=fx["sheet"], node=node, column=fx["columns"]["budget"], value=7)

    # B (the stageB owner) sees the process notification in the inbox, deep-linked
    # to {sheet, node}.
    h.login_as("B")
    proc_items = [i for i in api.inbox() if i["source"] == "process"]
    assert proc_items
    assert any(i["sheet"] == fx["sheet"] and i["node"] == node for i in proc_items)
    assert proc_items[0]["event_type"] == "PROCESS_STAGE_ASSIGNED"
