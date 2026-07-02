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

    # Dashboard edges: ``enable_process`` BACKFILLS a run for every already-existing
    # in-scope root-child (P1, P2), so sheet-wide totals are NOT 1/0 — assert on the
    # SPECIFIC edges' STRUCTURE + on THIS node's run/expectations, not the aggregate
    # counts (which fold in the backfilled runs).
    dash = api.process_dashboard(sheet=fx["sheet"])
    # edge stageB (budget -> notes) exists ONLY for this node's cascade so far, and
    # its structure (from budget -> notes) is stable regardless of backfill.
    edgeB = next(
        e for e in dash["edges"]
        if e["rule_key"] == "stageB" and e["to_column"] == fx["columns"]["notes"]
    )
    assert edgeB["from_column"] == fx["columns"]["budget"]
    # edge stageA (START -> budget) is a row trigger (structure is backfill-stable).
    edgeA = next(e for e in dash["edges"] if e["rule_key"] == "stageA")
    assert edgeA["from_kind"] == "row" and edgeA["from_column"] is None
    # THIS node's run is the one under test: stageA satisfied + stageB pending; and
    # it is the run the stageB edge drill-down surfaces as pending on notes.
    exps_now = _run_expectations(run["name"])
    assert exps_now[("stageA", fx["columns"]["budget"])].satisfied_at is not None
    assert exps_now[("stageB", fx["columns"]["notes"])].satisfied_at is None
    drill = api.list_process_runs(sheet=fx["sheet"], rule_key="stageB", status="active")
    assert node in {r["node"] for r in drill}

    # B fills notes (stageB) -> terminal completion of THIS run (no further notify).
    total_completed_before = dash["total_completed"]
    h.login_as("B")
    api.update_cell(sheet=fx["sheet"], node=node, column=fx["columns"]["notes"], value="done")
    assert _run(process["name"], node, "status")["status"] == "completed"
    # This node's run flipped active -> completed: the completed tally grew by
    # exactly one (relative), and this run is gone from the active drill-down.
    dash2 = api.process_dashboard(sheet=fx["sheet"])
    assert dash2["total_completed"] == total_completed_before + 1
    still_active = api.list_process_runs(sheet=fx["sheet"], rule_key="stageB", status="active")
    assert node not in {r["node"] for r in still_active}


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


# ---------------------------------------------------------------------------
# "AND" fan-out DAG on the FULLY WIRED site: row -> expect budget (stageA, owner
# C) within T; on budget -> expect notes AND status (stageBC, an 'and' set sharing
# ONE window, owners B + C) within T2. The column EXPECTED by rule 1 (budget) is
# the TRIGGER of rule 2 — and rule 2 opens TWO expectations at once.
# ---------------------------------------------------------------------------
def _and_rules(fx, within=(0, 0)):
    """row -> budget (stageA) within within[0];
    on budget -> {notes, status} (stageBC, an 'and' set) within within[1]."""
    return [
        {
            "rule_key": "stageA",
            "trigger_kind": "row",
            "trigger_op": "created",
            "expected_columns": [fx["columns"]["budget"]],
            "within_seconds": within[0],
        },
        {
            "rule_key": "stageBC",
            "trigger_kind": "column",
            "trigger_column": fx["columns"]["budget"],
            "trigger_op": "created-or-updated",
            # two expected columns sharing ONE window => an 'and' rule.
            "expected_columns": [fx["columns"]["notes"], fx["columns"]["status"]],
            "within_seconds": within[1],
        },
    ]


def _define_enable_and(fx, within=(0, 0)):
    h.login_as("A")
    api.define_process(sheet=fx["sheet"], rules=_and_rules(fx, within), title="Fill+review")
    api.enable_process(sheet=fx["sheet"])


def test_and_rule_prefilled_budget_opens_both_expectations_and_notifies_both_owners(fx):
    """Adding a node WITH budget prefilled satisfies stageA at creation and cascades
    the budget trigger, opening BOTH stageBC expectations (notes AND status) at once
    — each expected column's owner notified once (B for notes, C for status)."""
    _define_enable_and(fx)
    process = api.get_process(sheet=fx["sheet"])

    h.login_as("A")
    node = api.add_node(
        sheet=fx["sheet"], parent=fx["nodes"]["R"], values={"budget": 500}
    )["data"]["node"]

    run = _run(process["name"], node, "status")
    assert run is not None and run["status"] == "active"
    exps = _run_expectations(run["name"])
    # stageA satisfied at creation (prefilled budget counts as filled) ...
    assert exps[("stageA", fx["columns"]["budget"])].satisfied_at is not None
    # ... and the 'and' rule opened BOTH expectations, still open.
    notes_exp = exps[("stageBC", fx["columns"]["notes"])]
    status_exp = exps[("stageBC", fx["columns"]["status"])]
    assert notes_exp.satisfied_at is None and notes_exp.breached == 0
    assert status_exp.satisfied_at is None and status_exp.breached == 0
    # both expected owners were notified: B owns notes, C owns status.
    assert len(_process_notifs("B")) == 1  # notes owner
    # C owns budget too, but budget was satisfied-at-creation (no notify); C's ONE
    # process notification is the OPEN status expectation.
    assert len(_process_notifs("C")) == 1


def test_and_rule_partial_then_full_fill_completes_run(fx):
    """Filling ONE leg of the 'and' set (notes) leaves the run active with the other
    leg (status) still pending; filling the second leg completes the run."""
    _define_enable_and(fx)
    process = api.get_process(sheet=fx["sheet"])

    h.login_as("A")
    node = api.add_node(sheet=fx["sheet"], parent=fx["nodes"]["R"])["data"]["node"]

    # C fills budget (stageA) -> satisfy stageA, open BOTH stageBC legs.
    h.login_as("C")
    api.update_cell(sheet=fx["sheet"], node=node, column=fx["columns"]["budget"], value=9)
    exps = _run_expectations(run_name := _run(process["name"], node, "name")["name"])
    assert exps[("stageBC", fx["columns"]["notes"])].satisfied_at is None
    assert exps[("stageBC", fx["columns"]["status"])].satisfied_at is None

    # B fills notes (one 'and' leg) -> run still active (status leg unmet).
    h.login_as("B")
    api.update_cell(sheet=fx["sheet"], node=node, column=fx["columns"]["notes"], value="ok")
    assert _run(process["name"], node, "status")["status"] == "active"
    exps = _run_expectations(run_name)
    assert exps[("stageBC", fx["columns"]["notes"])].satisfied_at is not None
    assert exps[("stageBC", fx["columns"]["status"])].satisfied_at is None

    # C fills status (the last leg) -> both legs satisfied -> run completed.
    h.login_as("C")
    api.update_cell(sheet=fx["sheet"], node=node, column=fx["columns"]["status"], value="done")
    assert _run(process["name"], node, "status")["status"] == "completed"
    exps = _run_expectations(run_name)
    assert exps[("stageBC", fx["columns"]["status"])].satisfied_at is not None


# ---------------------------------------------------------------------------
# Server-side DAG authority: a cyclic / self-looping defineProcess is REJECTED
# by the pure ``process_graph.validate_rules`` BEFORE any write (risk #2). The
# executor funnel does not translate this into a 4xx wrapper (it is not a
# SchemaValidationError), so the pure ``ValidationError`` propagates — the point
# is the write is blocked and nothing persists.
# ---------------------------------------------------------------------------
def test_cyclic_define_process_rejected_and_persists_nothing(fx):
    from arbor.core.process_graph import ValidationError

    h.login_as("A")
    cyclic = [
        {"rule_key": "ab", "trigger_kind": "column",
         "trigger_column": fx["columns"]["budget"], "trigger_op": "created-or-updated",
         "expected_columns": [fx["columns"]["notes"]]},
        {"rule_key": "ba", "trigger_kind": "column",
         "trigger_column": fx["columns"]["notes"], "trigger_op": "created-or-updated",
         "expected_columns": [fx["columns"]["budget"]]},
    ]
    with pytest.raises(ValidationError):
        api.define_process(sheet=fx["sheet"], rules=cyclic, title="Loop")
    # the cyclic set never became a process definition.
    assert api.get_process(sheet=fx["sheet"]) is None


def test_self_loop_define_process_rejected(fx):
    """A rule whose trigger column is also one of its own expected columns is a
    self-loop — rejected by the server DAG authority."""
    from arbor.core.process_graph import ValidationError

    h.login_as("A")
    self_loop = [
        {"rule_key": "loop", "trigger_kind": "column",
         "trigger_column": fx["columns"]["budget"], "trigger_op": "created-or-updated",
         "expected_columns": [fx["columns"]["budget"]]},
    ]
    with pytest.raises(ValidationError):
        api.define_process(sheet=fx["sheet"], rules=self_loop, title="Self")
    assert api.get_process(sheet=fx["sheet"]) is None
