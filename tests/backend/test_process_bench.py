"""Process / SLA / dashboard / inbox — real-adapter (bench) round-trip
(Area 3 / WS-PROC-DISPATCH).

runnable: NEEDS FRAPPE BENCH (``@pytest.mark.bench``; auto-skips bench-free).

Exercises the WHOLE process lane on a live site through the shipped seams:

* ``arbor.api.define_process`` / ``enable_process`` (registry capabilities routed
  through the ONE executor by the structural owner A of the canonical sheet S);
* the dispatch-lane consumer wired by ``hooks.doc_events["Tree Event"]
  ["after_insert"]`` → ``on_tree_event_insert`` → the process consumer, driven off
  the SAME Tree Event stream: NODE_CREATED starts a run + notifies the stage-0
  owner; a NODE_VALUE_UPDATED on the current stage column advances + notifies the
  next owner; the terminal fill completes (no notify);
* the SLA sweep marking an over-due stage breached + notifying once;
* the read shims ``get_process`` / ``process_dashboard`` / ``list_process_runs``
  and the cross-sheet ``inbox()``.

Canonical sheet S: A is the structural owner; C owns col:budget; B owns col:notes.
Stage 0 = budget (owner C), stage 1 = notes (owner B). Start / advance / complete
run off the LIVE after_insert hook (so the wiring itself is under test); the SLA
sweep is scheduler-driven, so it is invoked directly via ``ProcessDispatcher``
with a numeric test clock for determinism. The bench harness rolls the
transaction back between tests, so nothing persists.
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
    deterministic against the Datetime-typed run-stage ledger."""

    def __init__(self, iso):
        self._iso = iso

    def now(self):
        return self._iso


@pytest.fixture()
def fx():
    data = h.seed()
    yield data
    frappe.set_user("Administrator")


def _define_enable(fx, slas=(0, 0)):
    """Define + enable a 2-stage process (budget -> notes) on S as owner A."""
    h.login_as("A")
    api.define_process(
        sheet=fx["sheet"],
        stages=[
            {"column": fx["columns"]["budget"], "sla_seconds": slas[0]},
            {"column": fx["columns"]["notes"], "sla_seconds": slas[1]},
        ],
        title="Fill order",
    )
    api.enable_process(sheet=fx["sheet"])


def _run(process_name, node, *fields):
    rows = frappe.get_all(
        "Arbor Process Run",
        filters={"arbor_process": process_name, "node": node},
        fields=["name", *fields],
    )
    return rows[0] if rows else None


def _process_notifs(recipient):
    return frappe.get_all(
        "Notification",
        filters={"recipient": h.user(recipient), "source": ["in", ["process", "sla"]]},
        fields=["name", "source", "requires_ack"],
    )


# ---------------------------------------------------------------------------
# start: NODE_CREATED (via the live after_insert hook) -> run at stage 0 +
# notify stage-0 owner (C)
# ---------------------------------------------------------------------------
def test_node_created_starts_run_and_notifies_stage0_owner(fx):
    _define_enable(fx)
    process = api.get_process(sheet=fx["sheet"])
    assert process["enabled"] is True and len(process["stages"]) == 2

    h.login_as("A")
    node = api.add_node(sheet=fx["sheet"], parent=fx["nodes"]["R"])["data"]["node"]

    run = _run(process["name"], node, "status", "current_stage_idx")
    assert run is not None
    assert run["status"] == "active" and run["current_stage_idx"] == 0
    # stage-0 owner C notified via a source='process' FYI (requires_ack=0), so it
    # never pollutes the accountability aggregate.
    notifs = _process_notifs("C")
    assert len(notifs) == 1
    assert notifs[0]["source"] == "process" and notifs[0]["requires_ack"] == 0


# ---------------------------------------------------------------------------
# advance -> complete + dashboard counts
# ---------------------------------------------------------------------------
def test_fill_advances_then_completes_and_dashboard_counts(fx):
    _define_enable(fx)
    process = api.get_process(sheet=fx["sheet"])

    h.login_as("A")
    node = api.add_node(sheet=fx["sheet"], parent=fx["nodes"]["R"])["data"]["node"]

    # C fills stage-0 (budget) -> advance to stage 1, notify B.
    h.login_as("C")
    api.update_cell(sheet=fx["sheet"], node=node, column=fx["columns"]["budget"], value=42)
    run = _run(process["name"], node, "status", "current_stage_idx")
    assert run["current_stage_idx"] == 1 and run["status"] == "active"
    assert len(_process_notifs("B")) == 1

    dash = api.process_dashboard(sheet=fx["sheet"])
    assert dash["total_active"] == 1 and dash["total_completed"] == 0
    stage1 = next(s for s in dash["stages"] if s["idx"] == 1)
    assert stage1["pending_count"] == 1

    # B fills stage-1 (notes) -> terminal completion (no further notify).
    h.login_as("B")
    api.update_cell(sheet=fx["sheet"], node=node, column=fx["columns"]["notes"], value="done")
    assert _run(process["name"], node, "status")["status"] == "completed"
    dash2 = api.process_dashboard(sheet=fx["sheet"])
    assert dash2["total_completed"] == 1 and dash2["throughput"] == 1


# ---------------------------------------------------------------------------
# out-of-order guard: filling the NON-current column does not advance
# ---------------------------------------------------------------------------
def test_out_of_order_fill_does_not_advance(fx):
    _define_enable(fx)
    process = api.get_process(sheet=fx["sheet"])

    h.login_as("A")
    node = api.add_node(sheet=fx["sheet"], parent=fx["nodes"]["R"])["data"]["node"]

    # B fills stage-1's column (notes) while the run is at stage 0 -> no advance.
    h.login_as("B")
    api.update_cell(sheet=fx["sheet"], node=node, column=fx["columns"]["notes"], value="early")
    assert _run(process["name"], node, "current_stage_idx")["current_stage_idx"] == 0


# ---------------------------------------------------------------------------
# list_process_runs drill-down
# ---------------------------------------------------------------------------
def test_list_process_runs_returns_run_with_node_label(fx):
    _define_enable(fx)
    h.login_as("A")
    node = api.add_node(sheet=fx["sheet"], parent=fx["nodes"]["R"])["data"]["node"]

    runs = api.list_process_runs(sheet=fx["sheet"], status="active")
    assert any(r["node"] == node and r["status"] == "active" for r in runs)


# ---------------------------------------------------------------------------
# SLA sweep marks the current stage breached + notifies once (scheduler-driven,
# so invoked directly with a numeric test clock)
# ---------------------------------------------------------------------------
def test_sla_sweep_marks_breach_and_notifies(fx):
    _define_enable(fx, slas=(50, 0))  # stage-0 SLA = 50s
    process = api.get_process(sheet=fx["sheet"])

    d = fd.ProcessDispatcher()
    h.login_as("A")
    node = api.add_node(sheet=fx["sheet"], parent=fx["nodes"]["R"])["data"]["node"]

    # The run was started by the live hook; pin stage-0's ledger to fixed ISO
    # datetimes so the sweep is deterministic against the Datetime column (entered
    # 2026-01-01 00:00, due 2026-01-01 00:00:50 given the 50s SLA).
    run_name = _run(process["name"], node, "name")["name"]
    existing = d.repo.get_process_run(process["name"], node)
    stages = [dict(s) for s in existing["stages"]]
    for s in stages:
        if s["stage_idx"] == 0:
            s["entered_at"] = "2026-01-01 00:00:00"
            s["due_at"] = "2026-01-01 00:00:50"
            s["breached"] = False
    d.repo.update_process_run(run_name, {"stages": stages})

    # sweep before due -> no breach.
    d.clock = _FixedClock("2026-01-01 00:00:10")
    assert d.sla_sweep() == []
    # sweep after due -> breach + notify stage-0 owner C once.
    d.clock = _FixedClock("2026-01-01 00:01:00")
    breached = d.sla_sweep()
    assert [t["kind"] for t in breached] == ["breached"]

    fresh = frappe.get_doc("Arbor Process Run", run_name)
    s0 = next(rs for rs in fresh.run_stages if rs.stage_idx == 0)
    assert s0.breached == 1
    assert any(n["source"] == "sla" for n in _process_notifs("C"))
    # idempotent: a second sweep does not re-breach / re-notify.
    d.clock = _FixedClock("2026-01-01 00:02:00")
    assert d.sla_sweep() == []


# ---------------------------------------------------------------------------
# inbox() surfaces the process notification for the responsible owner across
# sheets, deep-linked to {sheet, node} (even after the run completes)
# ---------------------------------------------------------------------------
def test_inbox_shows_process_notification_to_stage_owner(fx):
    _define_enable(fx)

    h.login_as("A")
    node = api.add_node(sheet=fx["sheet"], parent=fx["nodes"]["R"])["data"]["node"]

    # C fills stage-0 -> advance, notify stage-1 owner B.
    h.login_as("C")
    api.update_cell(sheet=fx["sheet"], node=node, column=fx["columns"]["budget"], value=7)

    # B (the stage-1 owner) sees the process notification in the inbox, deep-linked
    # to {sheet, node}.
    h.login_as("B")
    proc_items = [i for i in api.inbox() if i["source"] == "process"]
    assert proc_items
    assert any(i["sheet"] == fx["sheet"] and i["node"] == node for i in proc_items)
    assert proc_items[0]["event_type"] == "PROCESS_STAGE_ASSIGNED"
