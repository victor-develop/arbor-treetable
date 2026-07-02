"""The pure process RULE/DAG evaluator (process DAG) — exhaustive, bench-free.

Covers the Area-1 runtime contract against InMemoryRepository (zero frappe):

  * is_filled predicate parity with the frontend empty-check.
  * rule matching: row / column triggers x trigger_op {created, updated,
    created-or-updated}.
  * single + 'and' multi-expected expectations sharing one window.
  * pre-filled / default columns satisfied at creation + downstream cascade.
  * chain A -> B -> C; DAG fan-out / fan-in.
  * SLA due_at math; sweep breach; idempotent sweep; within=0 never breaches.
  * completion / quiescence; all-prefilled completes immediately.
  * idempotency / replay: no double-open, double-satisfy, or double-notify.
  * live owner re-resolution + role:<key> expansion + notify_on_expect gate.
  * dashboard edge aggregate.
  * startProcessRun handler parity + defineProcess validation rejection.
"""

from __future__ import annotations

from arbor.core import process as P
from arbor.core.testing import InMemoryRepository

OWNER_A = "owner-a"
OWNER_B = "owner-b"
OWNER_C = "owner-c"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _base_repo():
    repo = InMemoryRepository()
    repo.add_sheet("S", structural_owner="root-owner")
    repo.add_column("colA", "S", "a", column_owner=OWNER_A)
    repo.add_column("colB", "S", "b", column_owner=OWNER_B)
    repo.add_column("colC", "S", "c", column_owner=OWNER_C)
    repo.add_node("R", "S", parent=None)
    repo.add_node("P1", "S", parent="R")
    repo.add_node("P2", "S", parent="R")
    repo.add_node("Xdeep", "S", parent="P1")  # NOT root-child -> out of scope
    return repo


def _seed_chain(row_scope="root-children", within=(0, 0), enabled=True):
    """A chain: row -> expect colA; on colA updated -> expect colB; on colB
    updated -> expect colC. within[0] is colB's window, within[1] is colC's."""
    repo = _base_repo()
    name = repo.upsert_process(
        {
            "sheet": "S",
            "title": "Chain",
            "row_scope": row_scope,
            "rules": [
                {"rule_key": "start", "trigger_kind": "row", "trigger_op": "created",
                 "expected_columns": ["colA"]},
                {"rule_key": "ab", "trigger_kind": "column", "trigger_column": "colA",
                 "trigger_op": "created-or-updated", "expected_columns": ["colB"],
                 "within_seconds": within[0]},
                {"rule_key": "bc", "trigger_kind": "column", "trigger_column": "colB",
                 "trigger_op": "created-or-updated", "expected_columns": ["colC"],
                 "within_seconds": within[1]},
            ],
        }
    )
    repo.set_process_enabled(name, enabled)
    return repo, repo.get_process("S")


def _seed_row_expect(cols, within=0, notify=True, enabled=True):
    """A single row rule expecting `cols` (an 'and' set) within `within`."""
    repo = _base_repo()
    name = repo.upsert_process(
        {
            "sheet": "S",
            "rules": [
                {"rule_key": "start", "trigger_kind": "row", "trigger_op": "created",
                 "expected_columns": list(cols), "within_seconds": within,
                 "notify_on_expect": notify},
            ],
        }
    )
    repo.set_process_enabled(name, enabled)
    return repo, repo.get_process("S")


def _run(repo, proc, node="P1"):
    return repo.get_process_run(proc.name, node)


def _exp(run, rule_key, column):
    for e in run["expectations"]:
        if e["rule_key"] == rule_key and e["expected_column"] == column:
            return e
    return None


def _recipients(repo):
    return [n["recipient"] for n in repo.notifications.values()]


# ---------------------------------------------------------------------------
# is_filled predicate
# ---------------------------------------------------------------------------
def test_is_filled_predicate_matches_frontend_empty_check():
    assert P.is_filled(None) is False
    assert P.is_filled("") is False
    assert P.is_filled([]) is False
    assert P.is_filled(()) is False
    assert P.is_filled("x") is True
    assert P.is_filled(["a"]) is True
    assert P.is_filled(("a",)) is True
    assert P.is_filled(0) is True
    assert P.is_filled(False) is True
    assert P.is_filled(0.0) is True
    assert P.is_filled(" ") is True


# ---------------------------------------------------------------------------
# Row-rule firing + scope + disabled
# ---------------------------------------------------------------------------
def test_node_created_fires_row_rule_opens_expectation_notifies_owner():
    repo, proc = _seed_row_expect(["colA"])
    trans = P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=100)
    run = _run(repo, proc)
    assert run["status"] == "active"
    e = _exp(run, "start", "colA")
    assert e is not None
    assert e["opened_at"] == 100 and e["satisfied_at"] is None
    assert _recipients(repo) == [OWNER_A]
    kinds = {t["kind"] for t in trans}
    assert {"started", "notified"} <= kinds
    notif = next(iter(repo.notifications.values()))
    assert notif["source"] == "process"
    assert notif["requires_ack"] is False  # FYI, never pollutes ack math


def test_out_of_scope_node_creates_no_run():
    repo, proc = _seed_row_expect(["colA"])
    trans = P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "Xdeep"}, now=1)
    assert trans == []
    assert _run(repo, proc, "Xdeep") is None


def test_all_nodes_scope_includes_deep_node():
    repo, proc = _seed_chain(row_scope="all-nodes")
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "Xdeep"}, now=1)
    assert _run(repo, proc, "Xdeep") is not None


def test_disabled_process_is_inert():
    repo, proc = _seed_row_expect(["colA"], enabled=False)
    trans = P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    assert trans == []
    assert _run(repo, proc) is None


def test_notify_on_expect_false_suppresses_notification():
    repo, proc = _seed_row_expect(["colA"], notify=False)
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    assert len(repo.notifications) == 0
    # the expectation still opened.
    assert _exp(_run(repo, proc), "start", "colA") is not None


# ---------------------------------------------------------------------------
# 'and' multi-expected sharing one window
# ---------------------------------------------------------------------------
def test_and_rule_opens_one_expectation_per_expected_column():
    repo, proc = _seed_row_expect(["colA", "colB"], within=60)
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=100)
    run = _run(repo, proc)
    ea = _exp(run, "start", "colA")
    eb = _exp(run, "start", "colB")
    assert ea is not None and eb is not None
    # both share ONE window (opened_at + within) => same due_at math.
    assert ea["due_at"] == 160 and eb["due_at"] == 160
    # both owners notified.
    assert set(_recipients(repo)) == {OWNER_A, OWNER_B}


def test_and_rule_completes_only_when_all_expected_filled():
    repo, proc = _seed_row_expect(["colA", "colB"])
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    # fill only colA -> still active (colB open).
    repo.seed_value("S", "P1", "colA", "x")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    run = _run(repo, proc)
    assert _exp(run, "start", "colA")["satisfied_at"] == 2
    assert run["status"] == "active"
    # fill colB -> now quiescent -> completed.
    repo.seed_value("S", "P1", "colB", "y")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colB"}, now=3)
    assert _run(repo, proc)["status"] == "completed"


# ---------------------------------------------------------------------------
# Pre-filled / default satisfied at creation + cascade
# ---------------------------------------------------------------------------
def test_prefilled_expected_column_satisfied_at_creation_no_notify():
    repo, proc = _seed_row_expect(["colA"])
    repo.seed_value("S", "P1", "colA", "already")  # default present at creation
    trans = P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=5)
    run = _run(repo, proc)
    assert _exp(run, "start", "colA")["satisfied_at"] == 5
    # a satisfied-at-creation expectation notifies no one (nobody is waiting).
    assert len(repo.notifications) == 0
    assert run["status"] == "completed"  # quiescent immediately
    assert any(t["kind"] == "completed" for t in trans)


def test_all_prefilled_chain_completes_immediately_via_cascade():
    """colA + colB + colC all defaulted at creation -> the row rule opens colA
    (satisfied), which triggers ab -> colB (satisfied), which triggers bc ->
    colC (satisfied): the whole chain fires + satisfies + completes at creation,
    notifying no one."""
    repo, proc = _seed_chain()
    repo.seed_value("S", "P1", "colA", "a")
    repo.seed_value("S", "P1", "colB", "b")
    repo.seed_value("S", "P1", "colC", "c")
    trans = P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=9)
    run = _run(repo, proc)
    assert run["status"] == "completed" and run["completed_at"] == 9
    for rk, col in [("start", "colA"), ("ab", "colB"), ("bc", "colC")]:
        assert _exp(run, rk, col)["satisfied_at"] == 9
    assert len(repo.notifications) == 0
    assert any(t["kind"] == "completed" for t in trans)


def test_partial_prefill_cascades_then_waits_at_first_empty():
    """colA prefilled -> start+ab fire, colA satisfied, colB expectation open and
    its owner (B) notified; colC not yet expected (colB empty)."""
    repo, proc = _seed_chain()
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    run = _run(repo, proc)
    assert _exp(run, "start", "colA")["satisfied_at"] == 1
    assert _exp(run, "ab", "colB") is not None
    assert _exp(run, "ab", "colB")["satisfied_at"] is None
    assert _exp(run, "bc", "colC") is None  # colB empty -> bc not fired yet
    assert set(_recipients(repo)) == {OWNER_B}
    assert run["status"] == "active"


def test_empty_string_and_empty_list_are_not_filled_stay_open():
    repo, proc = _seed_row_expect(["colA"])
    repo.seed_value("S", "P1", "colA", "")  # explicitly empty
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    assert _exp(_run(repo, proc), "start", "colA")["satisfied_at"] is None

    repo2, proc2 = _seed_row_expect(["colA"])
    repo2.seed_value("S", "P1", "colA", [])
    P.on_event(repo2, proc2, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    assert _exp(_run(repo2, proc2), "start", "colA")["satisfied_at"] is None


def test_numeric_zero_default_counts_as_filled_and_satisfies():
    repo, proc = _seed_row_expect(["colA"])
    repo.seed_value("S", "P1", "colA", 0)
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    assert _exp(_run(repo, proc), "start", "colA")["satisfied_at"] == 1


# ---------------------------------------------------------------------------
# Chain A -> B -> C driven by value updates
# ---------------------------------------------------------------------------
def test_chain_advances_on_each_fill_and_completes():
    repo, proc = _seed_chain()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    assert set(_recipients(repo)) == {OWNER_A}
    # fill colA -> satisfies start/colA, fires ab -> opens colB, notifies B.
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    run = _run(repo, proc)
    assert _exp(run, "start", "colA")["satisfied_at"] == 2
    assert _exp(run, "ab", "colB") is not None
    assert OWNER_B in _recipients(repo)
    assert run["status"] == "active"
    # fill colB -> satisfies ab/colB, fires bc -> opens colC, notifies C.
    repo.seed_value("S", "P1", "colB", "b")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colB"}, now=3)
    assert _exp(_run(repo, proc), "bc", "colC") is not None
    assert OWNER_C in _recipients(repo)
    # fill colC -> satisfies bc/colC, no downstream -> completes.
    repo.seed_value("S", "P1", "colC", "c")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colC"}, now=4)
    assert _run(repo, proc)["status"] == "completed"


def test_value_update_on_untracked_column_before_run_is_noop():
    repo, proc = _seed_chain()
    trans = P.on_event(
        repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P2", "column": "colA"}, now=5
    )
    assert trans == []


def test_value_update_on_completed_run_is_noop():
    repo, proc = _seed_row_expect(["colA"])
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    assert _run(repo, proc)["status"] == "completed"
    trans = P.on_event(
        repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2
    )
    assert trans == []


# ---------------------------------------------------------------------------
# DAG fan-out / fan-in
# ---------------------------------------------------------------------------
def _seed_fanout():
    """row -> colA; colA -> (colB and colC): a fan-out."""
    repo = _base_repo()
    name = repo.upsert_process(
        {
            "sheet": "S",
            "rules": [
                {"rule_key": "start", "trigger_kind": "row", "trigger_op": "created",
                 "expected_columns": ["colA"]},
                {"rule_key": "fan", "trigger_kind": "column", "trigger_column": "colA",
                 "trigger_op": "created-or-updated", "expected_columns": ["colB", "colC"]},
            ],
        }
    )
    repo.set_process_enabled(name, True)
    return repo, repo.get_process("S")


def test_fanout_opens_both_downstream_on_trigger_fill():
    repo, proc = _seed_fanout()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    run = _run(repo, proc)
    assert _exp(run, "fan", "colB") is not None
    assert _exp(run, "fan", "colC") is not None
    assert {OWNER_B, OWNER_C} <= set(_recipients(repo))
    # complete only when BOTH filled.
    repo.seed_value("S", "P1", "colB", "b")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colB"}, now=3)
    assert _run(repo, proc)["status"] == "active"
    repo.seed_value("S", "P1", "colC", "c")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colC"}, now=4)
    assert _run(repo, proc)["status"] == "completed"


def _seed_fanin():
    """row -> (colA and colB); colA -> colC; colB -> colC: a fan-in (colC has
    TWO expectations under distinct rule_keys)."""
    repo = _base_repo()
    name = repo.upsert_process(
        {
            "sheet": "S",
            "rules": [
                {"rule_key": "start", "trigger_kind": "row", "trigger_op": "created",
                 "expected_columns": ["colA", "colB"]},
                {"rule_key": "ac", "trigger_kind": "column", "trigger_column": "colA",
                 "trigger_op": "created-or-updated", "expected_columns": ["colC"]},
                {"rule_key": "bc", "trigger_kind": "column", "trigger_column": "colB",
                 "trigger_op": "created-or-updated", "expected_columns": ["colC"]},
            ],
        }
    )
    repo.set_process_enabled(name, True)
    return repo, repo.get_process("S")


def test_fanin_two_rules_expect_same_column_both_satisfied_by_one_fill():
    repo, proc = _seed_fanin()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    repo.seed_value("S", "P1", "colB", "b")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colB"}, now=3)
    run = _run(repo, proc)
    # both ac/colC and bc/colC expectations now open.
    assert _exp(run, "ac", "colC") is not None
    assert _exp(run, "bc", "colC") is not None
    # ONE fill of colC satisfies BOTH.
    repo.seed_value("S", "P1", "colC", "c")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colC"}, now=4)
    run = _run(repo, proc)
    assert _exp(run, "ac", "colC")["satisfied_at"] == 4
    assert _exp(run, "bc", "colC")["satisfied_at"] == 4
    assert run["status"] == "completed"


# ---------------------------------------------------------------------------
# trigger_op matrix
# ---------------------------------------------------------------------------
def _seed_op(op):
    repo = _base_repo()
    name = repo.upsert_process(
        {
            "sheet": "S",
            "rules": [
                {"rule_key": "start", "trigger_kind": "row", "trigger_op": "created",
                 "expected_columns": ["colA"]},
                {"rule_key": "r", "trigger_kind": "column", "trigger_column": "colA",
                 "trigger_op": op, "expected_columns": ["colB"]},
            ],
        }
    )
    repo.set_process_enabled(name, True)
    return repo, repo.get_process("S")


def test_column_op_updated_fires_on_value_update():
    repo, proc = _seed_op("updated")
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    assert _exp(_run(repo, proc), "r", "colB") is not None


def test_column_op_updated_does_not_fire_from_prefilled_at_creation():
    """op='updated' must NOT fire at NODE_CREATED (nothing was 'updated')."""
    repo, proc = _seed_op("updated")
    repo.seed_value("S", "P1", "colA", "a")  # prefilled
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    # start/colA satisfied at creation, but the 'updated' rule did NOT fire.
    assert _exp(_run(repo, proc), "r", "colB") is None


def test_column_op_created_fires_from_prefilled_at_creation():
    repo, proc = _seed_op("created")
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    assert _exp(_run(repo, proc), "r", "colB") is not None


def test_column_op_created_fires_once_only():
    """op='created' fires the first time colA is filled, not on later edits."""
    repo, proc = _seed_op("created")
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    run = _run(repo, proc)
    n_exp = len([e for e in run["expectations"] if e["rule_key"] == "r"])
    assert n_exp == 1
    # re-edit colA -> 'created' does NOT re-fire.
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=3)
    run = _run(repo, proc)
    assert len([e for e in run["expectations"] if e["rule_key"] == "r"]) == 1


# ---------------------------------------------------------------------------
# Idempotency / replay
# ---------------------------------------------------------------------------
def test_replaying_node_created_does_not_create_second_run():
    repo, proc = _seed_chain()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    n_before = len(repo.notifications)
    trans = P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=2)
    assert trans == []
    runs = [r for r in repo.process_runs.values() if r["node"] == "P1"]
    assert len(runs) == 1
    assert len(repo.notifications) == n_before


def test_replaying_same_fill_satisfies_once_no_double_notify():
    repo, proc = _seed_chain()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    n_after = len(repo.notifications)
    exp_before = _exp(_run(repo, proc), "start", "colA")["satisfied_at"]
    # replay the same colA fill.
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=9)
    run = _run(repo, proc)
    assert _exp(run, "start", "colA")["satisfied_at"] == exp_before  # unchanged
    # ab expectation not duplicated.
    assert len([e for e in run["expectations"] if e["rule_key"] == "ab"]) == 1
    assert len(repo.notifications) == n_after


# ---------------------------------------------------------------------------
# Live owner re-resolution + role expansion
# ---------------------------------------------------------------------------
def test_downstream_notification_targets_regranted_owner():
    repo, proc = _seed_chain()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    repo.set_column_authority("S", "colB", column_owner="new-b")
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    assert "new-b" in _recipients(repo)


def test_role_principal_owner_expands_to_all_holders():
    repo, proc = _seed_row_expect(["colA"])
    repo.add_role_grant("approver", "u1")
    repo.add_role_grant("approver", "u2")
    repo.set_column_authority("S", "colA", column_owner="role:approver")
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    assert {"u1", "u2"} <= set(_recipients(repo))


# ---------------------------------------------------------------------------
# SLA math + sweep
# ---------------------------------------------------------------------------
def test_due_at_math():
    assert P.default_due_at(100, 60) == 160
    assert P.default_due_at(100, 0) is None


def test_sweep_marks_open_expectation_breached_when_past_due():
    repo, proc = _seed_row_expect(["colA"], within=60)
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=100)  # due=160
    assert P.sla_sweep(repo, now=150) == []
    assert _exp(_run(repo, proc), "start", "colA")["breached"] is False
    breached = P.sla_sweep(repo, now=200)
    assert len(breached) == 1 and breached[0]["kind"] == "breached"
    e = _exp(_run(repo, proc), "start", "colA")
    assert e["breached"] is True and e["breached_at"] == 200


def test_sweep_is_idempotent():
    repo, proc = _seed_row_expect(["colA"], within=60)
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=100)
    P.sla_sweep(repo, now=200)
    assert P.sla_sweep(repo, now=300) == []


def test_sweep_skips_satisfied_expectation():
    repo, proc = _seed_row_expect(["colA"], within=60)
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=100)
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=120)
    assert P.sla_sweep(repo, now=999) == []


def test_within_zero_never_breaches():
    repo, proc = _seed_row_expect(["colA"], within=0)
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    assert P.sla_sweep(repo, now=10 ** 9) == []
    assert _exp(_run(repo, proc), "start", "colA")["due_at"] is None


def test_sweep_notifies_owner_when_process_has_breach_notify():
    repo, proc = _seed_row_expect(["colA"], within=60)
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=100)
    n_before = len(repo.notifications)
    breached = P.sla_sweep(
        repo, now=300, process_of=lambda name: repo.get_process_by_name(name),
        notify=P._default_notify(repo),
    )
    assert breached and "owners" in breached[0]
    assert len(repo.notifications) > n_before
    sla_notif = [n for n in repo.notifications.values() if n.get("source") == "sla"]
    assert sla_notif and sla_notif[0]["recipient"] == OWNER_A


def test_breach_of_last_open_expectation_completes_run():
    repo, proc = _seed_row_expect(["colA"], within=60)
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=100)
    P.sla_sweep(repo, now=300, process_of=lambda name: repo.get_process_by_name(name),
                notify=P._default_notify(repo))
    assert _run(repo, proc)["status"] == "completed"


# ---------------------------------------------------------------------------
# Completion / quiescence corner cases
# ---------------------------------------------------------------------------
def test_row_rule_with_no_trigger_stays_active_until_filled():
    repo, proc = _seed_row_expect(["colA"])
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    assert _run(repo, proc)["status"] == "active"


# ---------------------------------------------------------------------------
# Dashboard aggregate (pure, edge-shaped)
# ---------------------------------------------------------------------------
def test_dashboard_edge_aggregate_counts_and_avg():
    repo, proc = _seed_chain(within=(0, 0))
    # P1: start/colA satisfied @2 (dur 1 from open@1), ab/colB open (pending).
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    # P2: start/colA open (pending).
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P2"}, now=1)

    agg = P.dashboard_aggregate(proc, repo.list_process_runs("S"))
    edges = {(e["rule_key"], e["to_column"]): e for e in agg["edges"]}
    start_edge = edges[("start", "colA")]
    assert start_edge["from_kind"] == "row" and start_edge["from_column"] is None
    assert start_edge["satisfied_count"] == 1  # P1
    assert start_edge["pending_count"] == 1    # P2
    assert start_edge["avg_open_to_satisfy_seconds"] == 1.0
    ab_edge = edges[("ab", "colB")]
    assert ab_edge["from_column"] == "colA"
    assert ab_edge["pending_count"] == 1       # P1 waiting on colB
    assert agg["total_active"] == 2
    assert agg["total_completed"] == 0
    assert agg["throughput"] == 0


def test_dashboard_throughput_counts_completed():
    repo, proc = _seed_row_expect(["colA"])
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    agg = P.dashboard_aggregate(proc, repo.list_process_runs("S"))
    assert agg["total_completed"] == 1 and agg["throughput"] == 1


def test_dashboard_edge_breached_count():
    repo, proc = _seed_row_expect(["colA"], within=60)
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=100)
    P.sla_sweep(repo, now=300, process_of=lambda name: repo.get_process_by_name(name))
    agg = P.dashboard_aggregate(proc, repo.list_process_runs("S"))
    edge = next(e for e in agg["edges"] if e["rule_key"] == "start")
    assert edge["breached_count"] == 1


# ---------------------------------------------------------------------------
# startProcessRun handler parity + defineProcess validation
# ---------------------------------------------------------------------------
def test_start_process_run_handler_matches_node_created():
    from arbor.core import handlers
    from arbor.core.types import Actor

    repo, proc = _seed_chain()
    res = handlers.start_process_run_handler(
        {"sheet": "S", "node": "P1", "now": 100}, Actor("root-owner"), repo
    )
    assert res.data["node"] == "P1"
    run = _run(repo, proc)
    assert run is not None
    assert _exp(run, "start", "colA") is not None


def test_define_process_handler_rejects_cycle():
    import pytest

    from arbor.core import handlers
    from arbor.core.process_graph import ValidationError
    from arbor.core.types import Actor

    repo = _base_repo()
    params = {
        "sheet": "S",
        "rules": [
            {"rule_key": "ab", "trigger_kind": "column", "trigger_column": "colA",
             "expected_columns": ["colB"]},
            {"rule_key": "ba", "trigger_kind": "column", "trigger_column": "colB",
             "expected_columns": ["colA"]},
        ],
    }
    with pytest.raises(ValidationError):
        handlers.define_process_handler(params, Actor("root-owner"), repo)
    # nothing was persisted.
    assert repo.get_process("S") is None


# ---------------------------------------------------------------------------
# AND-join (fan-in trigger): trigger_columns + trigger_join='all'
# ---------------------------------------------------------------------------
def _seed_all_join(within=0, op="created-or-updated", enabled=True):
    """row -> (colA and colB); on (colA AND colB) BOTH filled -> expect colC.
    The AND-join fires ONCE, when the LAST of colA/colB becomes filled."""
    repo = _base_repo()
    name = repo.upsert_process(
        {
            "sheet": "S",
            "rules": [
                {"rule_key": "start", "trigger_kind": "row", "trigger_op": "created",
                 "expected_columns": ["colA", "colB"]},
                {"rule_key": "join", "trigger_kind": "column",
                 "trigger_columns": ["colA", "colB"], "trigger_join": "all",
                 "trigger_op": op, "expected_columns": ["colC"],
                 "within_seconds": within},
            ],
        }
    )
    repo.set_process_enabled(name, enabled)
    return repo, repo.get_process("S")


def test_all_join_does_not_fire_on_partial_set():
    repo, proc = _seed_all_join()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    # fill only colA -> the join must NOT fire (colB still empty).
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    run = _run(repo, proc)
    assert _exp(run, "join", "colC") is None
    assert run["status"] == "active"


def test_all_join_fires_when_last_column_filled():
    repo, proc = _seed_all_join()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    assert _exp(_run(repo, proc), "join", "colC") is None  # still partial
    # fill colB -> the LAST column -> join fires, opens colC, notifies C.
    repo.seed_value("S", "P1", "colB", "b")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colB"}, now=3)
    run = _run(repo, proc)
    e = _exp(run, "join", "colC")
    assert e is not None and e["opened_at"] == 3
    assert OWNER_C in _recipients(repo)


def test_all_join_fires_once_idempotent_on_reupdate():
    repo, proc = _seed_all_join()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    repo.seed_value("S", "P1", "colB", "b")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colB"}, now=3)
    run = _run(repo, proc)
    assert len([e for e in run["expectations"] if e["rule_key"] == "join"]) == 1
    opened_at = _exp(run, "join", "colC")["opened_at"]
    # re-update colA (already filled) -> join must NOT re-open.
    repo.seed_value("S", "P1", "colA", "a2")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=9)
    run = _run(repo, proc)
    assert len([e for e in run["expectations"] if e["rule_key"] == "join"]) == 1
    assert _exp(run, "join", "colC")["opened_at"] == opened_at


def test_all_join_prefilled_fires_at_creation_and_cascades():
    """colA + colB + colC all defaulted at creation -> start opens+satisfies
    colA/colB, the AND-join fires at creation (all triggers filled), opens colC
    which is also prefilled -> satisfied. The whole run completes at creation."""
    repo, proc = _seed_all_join()
    repo.seed_value("S", "P1", "colA", "a")
    repo.seed_value("S", "P1", "colB", "b")
    repo.seed_value("S", "P1", "colC", "c")
    trans = P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=5)
    run = _run(repo, proc)
    assert _exp(run, "join", "colC")["satisfied_at"] == 5
    assert run["status"] == "completed"
    assert len(repo.notifications) == 0
    assert any(t["kind"] == "completed" for t in trans)


def test_all_join_prefilled_partial_does_not_fire_at_creation():
    """Only colA prefilled at creation -> the AND-join must NOT fire (colB empty).
    colB's expectation (from start) stays open; join/colC not opened."""
    repo, proc = _seed_all_join()
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    run = _run(repo, proc)
    assert _exp(run, "join", "colC") is None
    assert _exp(run, "start", "colB")["satisfied_at"] is None
    assert run["status"] == "active"


def test_all_join_completes_when_downstream_filled():
    repo, proc = _seed_all_join()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    repo.seed_value("S", "P1", "colB", "b")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colB"}, now=3)
    assert _run(repo, proc)["status"] == "active"
    repo.seed_value("S", "P1", "colC", "c")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colC"}, now=4)
    assert _run(repo, proc)["status"] == "completed"


def test_all_join_op_updated_does_not_fire_at_creation():
    """An 'all' rule with op='updated' does NOT fire at NODE_CREATED even when
    every trigger column is prefilled (nothing was 'updated')."""
    repo, proc = _seed_all_join(op="updated")
    repo.seed_value("S", "P1", "colA", "a")
    repo.seed_value("S", "P1", "colB", "b")
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    assert _exp(_run(repo, proc), "join", "colC") is None


def test_mixed_any_and_all_rules_coexist():
    """One any-join rule (colA -> colB) + one all-join rule ((colA AND colC) ->
    colD). Filling colA fires the any rule immediately; the all rule waits for
    colC too."""
    repo = _base_repo()
    repo.add_column("colD", "S", "d", column_owner="owner-d")
    name = repo.upsert_process(
        {
            "sheet": "S",
            "rules": [
                {"rule_key": "start", "trigger_kind": "row", "trigger_op": "created",
                 "expected_columns": ["colA", "colC"]},
                {"rule_key": "any", "trigger_kind": "column", "trigger_column": "colA",
                 "trigger_op": "created-or-updated", "expected_columns": ["colB"]},
                {"rule_key": "all", "trigger_kind": "column",
                 "trigger_columns": ["colA", "colC"], "trigger_join": "all",
                 "trigger_op": "created-or-updated", "expected_columns": ["colD"]},
            ],
        }
    )
    repo.set_process_enabled(name, True)
    proc = repo.get_process("S")
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    run = _run(repo, proc)
    assert _exp(run, "any", "colB") is not None      # any fired
    assert _exp(run, "all", "colD") is None           # all waits for colC
    repo.seed_value("S", "P1", "colC", "c")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colC"}, now=3)
    assert _exp(_run(repo, proc), "all", "colD") is not None  # now all fired


def test_back_compat_single_trigger_column_still_works():
    """A rule defined with only trigger_column (no trigger_columns/trigger_join)
    behaves exactly as before (any-join on that single column)."""
    repo, proc = _seed_chain()
    # the chain rules use trigger_column singular; confirm the view normalized.
    ab = next(r for r in proc.rules if r.rule_key == "ab")
    assert ab.trigger_columns == ["colA"]
    assert ab.trigger_join == "any"
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    repo.seed_value("S", "P1", "colA", "a")
    P.on_event(repo, proc, {"type": "NODE_VALUE_UPDATED", "node": "P1", "column": "colA"}, now=2)
    assert _exp(_run(repo, proc), "ab", "colB") is not None


def test_define_process_handler_rejects_all_join_self_trigger():
    import pytest

    from arbor.core import handlers
    from arbor.core.process_graph import ValidationError
    from arbor.core.types import Actor

    repo = _base_repo()
    params = {
        "sheet": "S",
        "rules": [
            {"rule_key": "bad", "trigger_kind": "column",
             "trigger_columns": ["colA", "colB"], "trigger_join": "all",
             "expected_columns": ["colB"]},
        ],
    }
    with pytest.raises(ValidationError):
        handlers.define_process_handler(params, Actor("root-owner"), repo)
    assert repo.get_process("S") is None


def test_define_process_handler_persists_all_join_shape():
    from arbor.core import handlers
    from arbor.core.types import Actor

    repo = _base_repo()
    params = {
        "sheet": "S",
        "rules": [
            {"rule_key": "start", "trigger_kind": "row", "trigger_op": "created",
             "expected_columns": ["colA", "colB"]},
            {"rule_key": "join", "trigger_kind": "column",
             "trigger_columns": ["colA", "colB"], "trigger_join": "all",
             "trigger_op": "created-or-updated", "expected_columns": ["colC"]},
        ],
    }
    handlers.define_process_handler(params, Actor("root-owner"), repo)
    proc = repo.get_process("S")
    join = next(r for r in proc.rules if r.rule_key == "join")
    assert join.trigger_columns == ["colA", "colB"]
    assert join.trigger_join == "all"
    # back-compat alias: trigger_column defaults to the first trigger.
    assert join.trigger_column == "colA"


def test_all_join_dashboard_edges_tagged_and_grouped():
    repo, proc = _seed_all_join()
    P.on_event(repo, proc, {"type": "NODE_CREATED", "node": "P1"}, now=1)
    agg = P.dashboard_aggregate(proc, repo.list_process_runs("S"))
    join_edges = [e for e in agg["edges"] if e["rule_key"] == "join"]
    assert {e["from_column"] for e in join_edges} == {"colA", "colB"}
    assert all(e["to_column"] == "colC" for e in join_edges)
    assert all(e.get("join") == "all" for e in join_edges)
    # an any-join edge is tagged 'any'.
    start_edges = [e for e in agg["edges"] if e["rule_key"] == "start"]
    assert all(e.get("join") == "any" for e in start_edges)


def test_define_process_handler_accepts_valid_dag_and_backfill_on_enable():
    from arbor.core import handlers
    from arbor.core.types import Actor

    repo = _base_repo()
    repo.seed_value("S", "P1", "colA", "pre")  # P1 has colA prefilled
    params = {
        "sheet": "S",
        "rules": [
            {"rule_key": "start", "trigger_kind": "row", "trigger_op": "created",
             "expected_columns": ["colA"]},
            {"rule_key": "ab", "trigger_kind": "column", "trigger_column": "colA",
             "trigger_op": "created-or-updated", "expected_columns": ["colB"]},
        ],
    }
    handlers.define_process_handler(params, Actor("root-owner"), repo)
    proc = repo.get_process("S")
    assert proc is not None and len(proc.rules) == 2
    # enable backfills existing in-scope rows through the start path.
    res = handlers.enable_process_handler({"sheet": "S", "now": 10}, Actor("root-owner"), repo)
    assert res.data["backfilled"] == 2  # P1, P2
    run = repo.get_process_run(proc.name, "P1")
    # P1's colA was prefilled -> satisfied at backfill, ab fired -> colB open.
    assert _exp(run, "start", "colA")["satisfied_at"] == 10
    assert _exp(run, "ab", "colB") is not None
