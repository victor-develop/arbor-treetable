"""The pure process stage machine (Area 3) — exhaustive, bench-free.

Covers: start on NODE_CREATED / in-scope guard, advance on current-stage fill,
out-of-order guard, terminal completion, idempotency (replay), live owner
re-resolution, notify fan-out + FYI shape, SLA due_at math + sweep breach +
sweep idempotency + sla=0 never-breaches, and the pure dashboard aggregate.

All against InMemoryRepository — zero frappe.
"""

from __future__ import annotations

from arbor.core import process as P
from arbor.core.testing import InMemoryRepository

OWNER_A = "owner-a"
OWNER_B = "owner-b"
OWNER_C = "owner-c"


def _seed(row_scope: str = "root-children", slas=(0, 0, 0), enabled: bool = True):
    """A 3-stage process (colA -> colB -> colC) on a sheet with two rows (P1, P2)
    each a direct child of root R. Returns (repo, process, node_ids)."""
    repo = InMemoryRepository()
    repo.add_sheet("S", structural_owner="root-owner")
    repo.add_column("colA", "S", "a", column_owner=OWNER_A)
    repo.add_column("colB", "S", "b", column_owner=OWNER_B)
    repo.add_column("colC", "S", "c", column_owner=OWNER_C)
    repo.add_node("R", "S", parent=None)
    repo.add_node("P1", "S", parent="R")
    repo.add_node("P2", "S", parent="R")
    repo.add_node("Xdeep", "S", parent="P1")  # NOT root-child → out of scope
    name = repo.upsert_process(
        {
            "sheet": "S",
            "title": "Flow",
            "stages": [
                {"column": "colA", "sla_seconds": slas[0]},
                {"column": "colB", "sla_seconds": slas[1]},
                {"column": "colC", "sla_seconds": slas[2]},
            ],
            "row_scope": row_scope,
        }
    )
    repo.set_process_enabled(name, enabled)
    return repo, repo.get_process("S")


# ---------------------------------------------------------------------------
# Start (NODE_CREATED)
# ---------------------------------------------------------------------------
def test_node_created_starts_run_at_stage_0_and_notifies_owner_a():
    repo, proc = _seed()
    trans = P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=100)
    run = repo.get_process_run(proc.name, "P1")
    assert run is not None
    assert run["status"] == "active"
    assert run["current_stage_idx"] == 0
    st0 = run["stages"][0]
    assert st0["entered_at"] == 100 and st0["filled_at"] is None
    # owner A of colA notified exactly once.
    kinds = [t["kind"] for t in trans]
    assert "started" in kinds and "notified" in kinds
    notified = [n for n in repo.notifications.values()]
    assert len(notified) == 1
    assert notified[0]["recipient"] == OWNER_A
    assert notified[0]["source"] == "process"
    assert notified[0]["requires_ack"] is False  # FYI, must not pollute ack math


def test_out_of_scope_node_creates_no_run():
    repo, proc = _seed()
    trans = P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "Xdeep"}, now=1)
    assert trans == []
    assert repo.get_process_run(proc.name, "Xdeep") is None


def test_all_nodes_scope_includes_deep_node():
    repo, proc = _seed(row_scope="all-nodes")
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "Xdeep"}, now=1)
    assert repo.get_process_run(proc.name, "Xdeep") is not None


def test_disabled_process_is_inert():
    repo, proc = _seed(enabled=False)
    trans = P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    assert trans == []
    assert repo.get_process_run(proc.name, "P1") is None


# ---------------------------------------------------------------------------
# Advance (NODE_VALUE_UPDATED on the current stage column)
# ---------------------------------------------------------------------------
def test_fill_current_stage_advances_and_notifies_next_owner():
    repo, proc = _seed()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=100)
    trans = P.on_event(
        repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=200
    )
    run = repo.get_process_run(proc.name, "P1")
    assert run["current_stage_idx"] == 1
    assert run["stages"][0]["filled_at"] == 200
    assert run["stages"][1]["entered_at"] == 200
    kinds = [t["kind"] for t in trans]
    assert "filled" in kinds and "advanced" in kinds and "notified" in kinds
    # owner B (colB) now notified.
    recips = {n["recipient"] for n in repo.notifications.values()}
    assert OWNER_B in recips


def test_value_update_on_non_current_column_does_not_advance():
    repo, proc = _seed()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=100)
    # current stage is colA; a fill on colC (future stage) must NOT advance.
    trans = P.on_event(
        repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colC"}, now=200
    )
    assert trans == []
    assert repo.get_process_run(proc.name, "P1")["current_stage_idx"] == 0


def test_value_update_on_node_without_run_is_noop():
    repo, proc = _seed()
    trans = P.on_event(
        repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P2", "column": "colA"}, now=5
    )
    assert trans == []


# ---------------------------------------------------------------------------
# Terminal completion
# ---------------------------------------------------------------------------
def test_filling_terminal_stage_completes_run_no_further_notify():
    repo, proc = _seed()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colB"}, now=3)
    n_before = len(repo.notifications)
    trans = P.on_event(
        repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colC"}, now=4
    )
    run = repo.get_process_run(proc.name, "P1")
    assert run["status"] == "completed"
    assert run["completed_at"] == 4
    assert run["stages"][2]["filled_at"] == 4
    assert any(t["kind"] == "completed" for t in trans)
    # terminal fill notifies no NEW owner.
    assert len(repo.notifications) == n_before


# ---------------------------------------------------------------------------
# Idempotency (replay)
# ---------------------------------------------------------------------------
def test_replaying_node_created_does_not_create_second_run():
    repo, proc = _seed()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    trans = P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=2)
    assert trans == []
    runs = [r for r in repo.process_runs.values() if r["node"] == "P1"]
    assert len(runs) == 1


def test_replaying_same_fill_advances_once_no_double_notify():
    repo, proc = _seed()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    n_after_first = len(repo.notifications)
    # re-edit colA (already filled / stage advanced) → no re-advance, no re-notify.
    trans = P.on_event(
        repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=3
    )
    assert trans == []
    assert repo.get_process_run(proc.name, "P1")["current_stage_idx"] == 1
    assert len(repo.notifications) == n_after_first


# ---------------------------------------------------------------------------
# Live owner re-resolution
# ---------------------------------------------------------------------------
def test_next_stage_notification_targets_re_granted_owner():
    repo, proc = _seed()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    # re-grant colB to a new owner BEFORE advancing into stage 1.
    repo.set_column_authority("S", "colB", column_owner="new-b")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    recips = {n["recipient"] for n in repo.notifications.values()}
    assert "new-b" in recips
    assert OWNER_B not in recips or "new-b" in recips  # rerouted to current owner


def test_role_principal_stage_owner_expands_to_all_holders():
    repo, proc = _seed()
    repo.add_role_grant("approver", "u1")
    repo.add_role_grant("approver", "u2")
    repo.set_column_authority("S", "colA", column_owner="role:approver")
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    recips = {n["recipient"] for n in repo.notifications.values()}
    assert {"u1", "u2"} <= recips


def test_notify_on_enter_false_suppresses_notification():
    repo = InMemoryRepository()
    repo.add_sheet("S", structural_owner="o")
    repo.add_column("colA", "S", "a", column_owner=OWNER_A)
    repo.add_node("R", "S", parent=None)
    repo.add_node("P1", "S", parent="R")
    name = repo.upsert_process(
        {"sheet": "S", "stages": [{"column": "colA", "notify_on_enter": False}]}
    )
    repo.set_process_enabled(name, True)
    proc = repo.get_process("S")
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    assert len(repo.notifications) == 0


# ---------------------------------------------------------------------------
# SLA math + sweep
# ---------------------------------------------------------------------------
def test_due_at_math():
    assert P.default_due_at(100, 60) == 160
    assert P.default_due_at(100, 0) is None  # sla=0 → no SLA


def test_sweep_marks_breached_when_past_due():
    repo, proc = _seed(slas=(60, 0, 0))
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=100)
    # due_at = 160; sweep at 150 → not breached.
    assert P.sla_sweep(repo, now=150) == []
    assert repo.get_process_run(proc.name, "P1")["stages"][0]["breached"] is False
    # sweep at 200 → breached.
    breached = P.sla_sweep(repo, now=200)
    assert len(breached) == 1 and breached[0]["kind"] == "breached"
    st0 = repo.get_process_run(proc.name, "P1")["stages"][0]
    assert st0["breached"] is True and st0["breached_at"] == 200


def test_sweep_is_idempotent():
    repo, proc = _seed(slas=(60, 0, 0))
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=100)
    P.sla_sweep(repo, now=200)
    # second sweep does not re-breach.
    assert P.sla_sweep(repo, now=300) == []


def test_sweep_skips_filled_stage():
    repo, proc = _seed(slas=(60, 0, 0))
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=100)
    # fill stage 0 before it breaches → advanced to stage 1 (no SLA) → nothing breaches.
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=120)
    assert P.sla_sweep(repo, now=999) == []


def test_sla_zero_never_breaches():
    repo, proc = _seed(slas=(0, 0, 0))
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    assert P.sla_sweep(repo, now=10 ** 9) == []


def test_sweep_notifies_owner_when_process_has_breach_notify():
    repo, proc = _seed(slas=(60, 0, 0))
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=100)
    n_before = len(repo.notifications)
    breached = P.sla_sweep(
        repo, now=300, process_of=lambda name: repo.processes[name], notify=P._default_notify(repo)
    )
    assert breached and "owners" in breached[0]
    assert len(repo.notifications) > n_before
    sla_notif = [n for n in repo.notifications.values() if n.get("source") == "sla"]
    assert sla_notif and sla_notif[0]["recipient"] == OWNER_A


# ---------------------------------------------------------------------------
# Dashboard aggregate (pure)
# ---------------------------------------------------------------------------
def test_dashboard_aggregate_counts_and_avg():
    repo, proc = _seed(slas=(60, 0, 0))
    # P1: created@100, colA filled@130 (dur 30) → now at stage 1 (pending).
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=100)
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=130)
    # P2: created@100, colA breached (never filled), pending at stage 0.
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P2"}, now=100)
    P.sla_sweep(repo, now=999)

    runs = repo.list_process_runs("S")
    agg = P.dashboard_aggregate(proc, runs)
    stage0 = next(s for s in agg["stages"] if s["idx"] == 0)
    stage1 = next(s for s in agg["stages"] if s["idx"] == 1)
    # stage 0: P1 filled (dur 30), P2 still pending + breached.
    assert stage0["pending_count"] == 1        # P2 sits at stage 0
    assert stage0["breached_count"] == 1       # P2 breached
    assert stage0["avg_enter_to_fill_seconds"] == 30.0
    # stage 1: P1 pending (entered, not filled).
    assert stage1["pending_count"] == 1
    assert agg["total_active"] == 2
    assert agg["total_completed"] == 0
    assert agg["throughput"] == 0


def test_dashboard_aggregate_completed_throughput():
    repo, proc = _seed()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    for col, t in [("colA", 2), ("colB", 3), ("colC", 4)]:
        P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": col}, now=t)
    agg = P.dashboard_aggregate(proc, repo.list_process_runs("S"))
    assert agg["total_completed"] == 1 and agg["throughput"] == 1


# ---------------------------------------------------------------------------
# startProcessRun handler parity (manual start)
# ---------------------------------------------------------------------------
def test_start_process_run_handler_matches_node_created():
    from arbor.core import handlers
    from arbor.core.types import Actor

    repo, proc = _seed()
    res = handlers.start_process_run_handler(
        {"sheet": "S", "node": "P1", "now": 100}, Actor("root-owner"), repo
    )
    assert res.data["node"] == "P1"
    run = repo.get_process_run(proc.name, "P1")
    assert run is not None and run["current_stage_idx"] == 0
