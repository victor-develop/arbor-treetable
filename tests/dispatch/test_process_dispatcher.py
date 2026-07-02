"""Process dispatch-lane consumer + SLA sweep (Area 3) — bench-free.

Drives the Frappe binding ``ProcessDispatcher`` with INJECTED doubles (the pure
``arbor.core.testing.InMemoryRepository``, a recording notify sink, and a
freezable clock), so the SAME trigger->expectation DAG logic the bench-free
``tests/core/test_process.py`` covers is exercised through the dispatch seam —
proving the binding wires the Tree Event stream to the pure machine correctly and
never touches frappe when its deps are supplied.

A process is a per-sheet SET of trigger->expectation RULES (not an ordered stage
list). Covers, through the seam: NODE_CREATED on an in-scope node starts a run +
opens the row rule's expectations + notifies the expected column's owner
(op ``process-expect-opened``); a NODE_VALUE_UPDATED(colX) satisfies the pending
expectation on colX AND fires column(colX)-triggered rules (opening + notifying
downstream); a default/pre-filled expected column is satisfied at creation with
no notify + cascades; idempotent replay does not double-satisfy or double-notify;
the terminal fill completes the run with no further notify; a disabled / undefined
process (and an unrelated event type) is inert; the SLA sweep breaches an
overdue-unmet expectation + notifies once (idempotently), and within_seconds=0
never breaches. Ordering with the notify + webhook dispatchers is safe — the
process consumer emits no Tree Event, so it cannot recurse.
"""

from __future__ import annotations

from arbor.arbor.dispatch.frappe_dispatch import ProcessDispatcher
from arbor.arbor.dispatch.testing import FakeEvent
from arbor.core.testing import InMemoryRepository

OWNER_A = "owner-a"
OWNER_B = "owner-b"
OWNER_C = "owner-c"


class RecordingNotifier:
    """A ``ProcessNotifier`` double: records every (recipients, data) fan-out."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, recipients, data) -> None:
        self.calls.append((list(recipients), dict(data)))

    @property
    def recipients(self) -> list[str]:
        out: list[str] = []
        for recips, _ in self.calls:
            out.extend(recips)
        return out


class ListClock:
    """A clock that yields the SAME integer instant until ``set`` moves it, so
    due_at math is numeric (the pure machine adds within_seconds directly to an
    int ``now``). ``step=0`` keeps every ``now()`` in one event stable."""

    def __init__(self, start: int = 100, step: int = 10) -> None:
        self._t = start
        self._step = step

    def now(self) -> int:
        v = self._t
        self._t += self._step
        return v

    def set(self, when: int) -> None:
        self._t = when


def _base_repo():
    repo = InMemoryRepository()
    repo.add_sheet("S", structural_owner="root-owner")
    repo.add_column("colA", "S", "a", column_owner=OWNER_A)
    repo.add_column("colB", "S", "b", column_owner=OWNER_B)
    repo.add_column("colC", "S", "c", column_owner=OWNER_C)
    repo.add_node("R", "S", parent=None)
    repo.add_node("P1", "S", parent="R")
    return repo


def _seed_chain(within=(0, 0), enabled=True, row_scope="root-children"):
    """A chain: row -> expect colA; on colA -> expect colB; on colB -> expect
    colC. within[0] is colB's window, within[1] is colC's."""
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
    return repo


def _seed_row_expect(cols, within=0, enabled=True):
    """A single row rule expecting ``cols`` (an 'and' set) within ``within``."""
    repo = _base_repo()
    name = repo.upsert_process(
        {
            "sheet": "S",
            "rules": [
                {"rule_key": "start", "trigger_kind": "row", "trigger_op": "created",
                 "expected_columns": list(cols), "within_seconds": within},
            ],
        }
    )
    repo.set_process_enabled(name, enabled)
    return repo


def _dispatcher(repo, notify=None, clock=None):
    return ProcessDispatcher(
        repo=repo, notify=notify or RecordingNotifier(), clock=clock or ListClock()
    )


def _ev(etype, node, column=None, name="evt1"):
    payload = {"node": node}
    if column is not None:
        payload["column"] = column
    return FakeEvent(name, "S", etype, payload)


def _run(repo, node="P1"):
    return repo.get_process_run(repo.get_process("S").name, node)


def _exp(run, rule_key, column):
    for e in run["expectations"]:
        if e["rule_key"] == rule_key and e["expected_column"] == column:
            return e
    return None


# --- start ------------------------------------------------------------------
def test_node_created_starts_run_and_opens_expectation_and_notifies_owner():
    repo = _seed_row_expect(["colA"])
    notify = RecordingNotifier()
    d = _dispatcher(repo, notify)
    trans = d.on_tree_event(_ev("NODE_CREATED", "P1"))
    run = _run(repo)
    assert run is not None and run["status"] == "active"
    # the row rule opened its expectation on colA (unsatisfied — colA empty).
    e = _exp(run, "start", "colA")
    assert e is not None and e["satisfied_at"] is None
    kinds = [t["kind"] for t in trans]
    assert "started" in kinds and "notified" in kinds
    # the expected column's owner was notified once.
    assert notify.recipients == [OWNER_A]
    assert notify.calls[0][1]["source"] == "process"
    assert notify.calls[0][1]["op"] == "process-expect-opened"


def test_out_of_scope_node_creates_no_run():
    repo = _seed_row_expect(["colA"])
    repo.add_node("Xdeep", "S", parent="P1")  # not a root child
    d = _dispatcher(repo)
    assert d.on_tree_event(_ev("NODE_CREATED", "Xdeep")) == []
    assert _run(repo, "Xdeep") is None


# --- advance (value update satisfies + fires downstream) --------------------
def test_value_update_satisfies_expectation_and_fires_downstream_rule():
    repo = _seed_chain()
    notify = RecordingNotifier()
    d = _dispatcher(repo, notify)
    d.on_tree_event(_ev("NODE_CREATED", "P1"))
    # fill colA: satisfies start/colA AND fires the colA-triggered rule 'ab',
    # opening colB's expectation + notifying B.
    repo.seed_value("S", "P1", "colA", "a")
    trans = d.on_tree_event(_ev("NODE_VALUE_UPDATED", "P1", column="colA", name="evt2"))
    run = _run(repo)
    assert _exp(run, "start", "colA")["satisfied_at"] is not None
    assert _exp(run, "ab", "colB") is not None
    assert _exp(run, "ab", "colB")["satisfied_at"] is None
    kinds = [t["kind"] for t in trans]
    assert "satisfied" in kinds and "notified" in kinds
    # colA owner notified once (on start), colB owner once (on this fill).
    assert notify.recipients == [OWNER_A, OWNER_B]
    assert run["status"] == "active"


def test_value_update_on_untracked_column_before_run_is_noop():
    repo = _seed_chain()
    repo.add_node("P2", "S", parent="R")
    d = _dispatcher(repo)
    # no run exists for P2 yet -> a value update is a no-op.
    trans = d.on_tree_event(_ev("NODE_VALUE_UPDATED", "P2", column="colA", name="evt2"))
    assert trans == []
    assert _run(repo, "P2") is None


# --- default / pre-filled satisfied at creation + cascade -------------------
def test_prefilled_expected_column_satisfied_at_creation_no_notify_and_cascades():
    """colA defaulted at creation -> start/colA satisfied immediately (no notify),
    which fires 'ab' -> opens colB's expectation + notifies B; run stays active."""
    repo = _seed_chain()
    repo.seed_value("S", "P1", "colA", "already")  # default present at creation
    notify = RecordingNotifier()
    d = _dispatcher(repo, notify)
    trans = d.on_tree_event(_ev("NODE_CREATED", "P1"))
    run = _run(repo)
    assert _exp(run, "start", "colA")["satisfied_at"] is not None
    # cascaded: colB expectation now open + its owner notified.
    assert _exp(run, "ab", "colB") is not None
    assert _exp(run, "ab", "colB")["satisfied_at"] is None
    # the satisfied-at-creation colA notified no one; only the open colB did.
    assert notify.recipients == [OWNER_B]
    assert "satisfied" in [t["kind"] for t in trans]
    assert run["status"] == "active"


def test_all_prefilled_chain_completes_immediately_via_cascade_no_notify():
    repo = _seed_chain()
    repo.seed_value("S", "P1", "colA", "a")
    repo.seed_value("S", "P1", "colB", "b")
    repo.seed_value("S", "P1", "colC", "c")
    notify = RecordingNotifier()
    d = _dispatcher(repo, notify)
    trans = d.on_tree_event(_ev("NODE_CREATED", "P1"))
    run = _run(repo)
    assert run["status"] == "completed"
    for rk, col in [("start", "colA"), ("ab", "colB"), ("bc", "colC")]:
        assert _exp(run, rk, col)["satisfied_at"] is not None
    assert notify.calls == []
    assert "completed" in [t["kind"] for t in trans]


# --- idempotency ------------------------------------------------------------
def test_replaying_the_same_fill_does_not_double_satisfy_or_double_notify():
    repo = _seed_chain()
    notify = RecordingNotifier()
    d = _dispatcher(repo, notify)
    d.on_tree_event(_ev("NODE_CREATED", "P1"))
    repo.seed_value("S", "P1", "colA", "a")
    d.on_tree_event(_ev("NODE_VALUE_UPDATED", "P1", column="colA", name="evt2"))
    satisfied_before = _exp(_run(repo), "start", "colA")["satisfied_at"]
    n_after_first = len(notify.calls)
    # replay the SAME colA fill.
    trans = d.on_tree_event(_ev("NODE_VALUE_UPDATED", "P1", column="colA", name="evt2"))
    run = _run(repo)
    # colA's satisfaction is unchanged and 'ab' was not opened twice.
    assert _exp(run, "start", "colA")["satisfied_at"] == satisfied_before
    assert len([e for e in run["expectations"] if e["rule_key"] == "ab"]) == 1
    assert len(notify.calls) == n_after_first
    assert "notified" not in [t["kind"] for t in trans]


# --- terminal fill completes ------------------------------------------------
def test_terminal_fill_completes_run_and_does_not_notify_again():
    repo = _seed_chain()
    notify = RecordingNotifier()
    d = _dispatcher(repo, notify)
    d.on_tree_event(_ev("NODE_CREATED", "P1"))
    repo.seed_value("S", "P1", "colA", "a")
    d.on_tree_event(_ev("NODE_VALUE_UPDATED", "P1", column="colA", name="e2"))
    repo.seed_value("S", "P1", "colB", "b")
    d.on_tree_event(_ev("NODE_VALUE_UPDATED", "P1", column="colB", name="e3"))
    before = len(notify.calls)
    # fill the terminal column colC: satisfies bc/colC, no downstream -> complete.
    repo.seed_value("S", "P1", "colC", "c")
    trans = d.on_tree_event(_ev("NODE_VALUE_UPDATED", "P1", column="colC", name="e4"))
    run = _run(repo)
    assert run["status"] == "completed"
    assert "completed" in [t["kind"] for t in trans]
    # the terminal fill satisfied the last expectation but notified no one new.
    assert len(notify.calls) == before


# --- inert lanes ------------------------------------------------------------
def test_disabled_process_is_inert():
    repo = _seed_row_expect(["colA"], enabled=False)
    d = _dispatcher(repo)
    assert d.on_tree_event(_ev("NODE_CREATED", "P1")) == []
    assert _run(repo) is None


def test_no_process_defined_is_inert():
    repo = InMemoryRepository()
    repo.add_sheet("S", structural_owner="o")
    repo.add_node("R", "S", parent=None)
    d = _dispatcher(repo)
    assert d.on_tree_event(_ev("NODE_CREATED", "R")) == []


def test_unrelated_event_type_is_ignored():
    repo = _seed_row_expect(["colA"])
    d = _dispatcher(repo)
    assert d.on_tree_event(_ev("NODE_DELETED", "P1")) == []


# --- SLA sweep --------------------------------------------------------------
def test_sla_sweep_breaches_overdue_unmet_expectation_and_notifies_once():
    repo = _seed_row_expect(["colA"], within=50)
    notify = RecordingNotifier()
    # start at now=100 -> colA expectation due_at = 150.
    d = ProcessDispatcher(repo=repo, notify=notify, clock=ListClock(start=100, step=0))
    d.on_tree_event(_ev("NODE_CREATED", "P1"))
    notify.calls.clear()
    # sweep at now=100 -> not yet due.
    assert d.sla_sweep() == []
    # advance the clock past due_at and sweep -> breach + notify the owner once.
    d.clock = ListClock(start=200, step=0)
    breached = d.sla_sweep()
    assert [t["kind"] for t in breached] == ["breached"]
    e = _exp(_run(repo), "start", "colA")
    assert e["breached"] is True
    assert notify.recipients == [OWNER_A]
    assert notify.calls[0][1]["source"] == "sla"
    assert notify.calls[0][1]["op"] == "process-expect-due"
    # idempotent: a second sweep does not re-breach or re-notify.
    notify.calls.clear()
    assert d.sla_sweep() == []
    assert notify.calls == []


def test_sla_zero_never_breaches():
    repo = _seed_row_expect(["colA"], within=0)
    d = ProcessDispatcher(repo=repo, notify=RecordingNotifier(), clock=ListClock(start=100, step=0))
    d.on_tree_event(_ev("NODE_CREATED", "P1"))
    assert _exp(_run(repo), "start", "colA")["due_at"] is None
    d.clock = ListClock(start=10_000, step=0)
    assert d.sla_sweep() == []
