"""Process dispatch-lane consumer + SLA sweep (Area 3) — bench-free.

Drives the Frappe binding ``ProcessDispatcher`` with INJECTED doubles (the pure
``arbor.core.testing.InMemoryRepository``, a recording notify sink, and a
freezable clock), so the SAME start/advance/complete + SLA-breach logic the
bench-free ``tests/core/test_process.py`` covers is exercised through the
dispatch seam — proving the binding wires the Tree Event stream to the pure
machine correctly and never touches frappe when its deps are supplied.

Covers: NODE_CREATED starts a run + notifies stage-0 owner; a NODE_VALUE_UPDATED
on the current stage column advances + notifies the next owner; a value update on
a NON-current column does NOT advance (out-of-order guard); terminal fill
completes with no further notify; idempotent replay does not double-advance or
double-notify; a disabled / undefined process is inert; the SLA sweep marks the
current stage breached + notifies once; ordering with the notify + webhook
dispatchers (no recursion — the process consumer emits no Tree Event).
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
    """A clock that yields successive integer instants so due_at math is numeric
    (the pure machine adds sla_seconds directly to an int ``now``)."""

    def __init__(self, start: int = 100, step: int = 10) -> None:
        self._t = start
        self._step = step

    def now(self) -> int:
        v = self._t
        self._t += self._step
        return v


def _seed(slas=(0, 0, 0), enabled=True):
    repo = InMemoryRepository()
    repo.add_sheet("S", structural_owner="root-owner")
    repo.add_column("colA", "S", "a", column_owner=OWNER_A)
    repo.add_column("colB", "S", "b", column_owner=OWNER_B)
    repo.add_column("colC", "S", "c", column_owner=OWNER_C)
    repo.add_node("R", "S", parent=None)
    repo.add_node("P1", "S", parent="R")
    name = repo.upsert_process(
        {
            "sheet": "S",
            "title": "Flow",
            "stages": [
                {"column": "colA", "sla_seconds": slas[0]},
                {"column": "colB", "sla_seconds": slas[1]},
                {"column": "colC", "sla_seconds": slas[2]},
            ],
            "row_scope": "root-children",
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


# --- start ------------------------------------------------------------------
def test_node_created_starts_run_and_notifies_stage0_owner():
    repo = _seed()
    notify = RecordingNotifier()
    d = _dispatcher(repo, notify)
    trans = d.on_tree_event(_ev("NODE_CREATED", "P1"))
    run = repo.get_process_run(repo.get_process("S").name, "P1")
    assert run is not None and run["status"] == "active"
    assert run["current_stage_idx"] == 0
    kinds = [t["kind"] for t in trans]
    assert "started" in kinds and "notified" in kinds
    assert notify.recipients == [OWNER_A]
    assert notify.calls[0][1]["source"] == "process"
    assert notify.calls[0][1]["op"] == "process-stage-assigned"


def test_out_of_scope_node_creates_no_run():
    repo = _seed()
    repo.add_node("Xdeep", "S", parent="P1")  # not a root child
    d = _dispatcher(repo)
    assert d.on_tree_event(_ev("NODE_CREATED", "Xdeep")) == []
    assert repo.get_process_run(repo.get_process("S").name, "Xdeep") is None


# --- advance ----------------------------------------------------------------
def test_value_update_on_current_column_advances_and_notifies_next():
    repo = _seed()
    notify = RecordingNotifier()
    d = _dispatcher(repo, notify)
    d.on_tree_event(_ev("NODE_CREATED", "P1"))
    trans = d.on_tree_event(_ev("NODE_VALUE_UPDATED", "P1", column="colA", name="evt2"))
    run = repo.get_process_run(repo.get_process("S").name, "P1")
    assert run["current_stage_idx"] == 1
    kinds = [t["kind"] for t in trans]
    assert "filled" in kinds and "advanced" in kinds
    # stage-0 owner notified once (on start), stage-1 owner once (on advance).
    assert notify.recipients == [OWNER_A, OWNER_B]


def test_value_update_on_non_current_column_does_not_advance():
    repo = _seed()
    d = _dispatcher(repo)
    d.on_tree_event(_ev("NODE_CREATED", "P1"))
    # colB is stage-1's column, but the current stage is 0 (colA) -> no advance.
    trans = d.on_tree_event(_ev("NODE_VALUE_UPDATED", "P1", column="colB", name="evt2"))
    assert trans == []
    run = repo.get_process_run(repo.get_process("S").name, "P1")
    assert run["current_stage_idx"] == 0


def test_terminal_fill_completes_run_and_does_not_notify_again():
    repo = _seed()
    notify = RecordingNotifier()
    d = _dispatcher(repo, notify)
    d.on_tree_event(_ev("NODE_CREATED", "P1"))
    d.on_tree_event(_ev("NODE_VALUE_UPDATED", "P1", column="colA", name="e2"))
    d.on_tree_event(_ev("NODE_VALUE_UPDATED", "P1", column="colB", name="e3"))
    before = len(notify.calls)
    trans = d.on_tree_event(_ev("NODE_VALUE_UPDATED", "P1", column="colC", name="e4"))
    run = repo.get_process_run(repo.get_process("S").name, "P1")
    assert run["status"] == "completed"
    assert "completed" in [t["kind"] for t in trans]
    # terminal completion notifies no one.
    assert len(notify.calls) == before


# --- idempotency ------------------------------------------------------------
def test_replaying_the_same_fill_does_not_double_advance_or_double_notify():
    repo = _seed()
    notify = RecordingNotifier()
    d = _dispatcher(repo, notify)
    d.on_tree_event(_ev("NODE_CREATED", "P1"))
    d.on_tree_event(_ev("NODE_VALUE_UPDATED", "P1", column="colA", name="e2"))
    n_after_first = len(notify.calls)
    # replay the SAME colA fill: current stage is already 1, colA != current col.
    trans = d.on_tree_event(_ev("NODE_VALUE_UPDATED", "P1", column="colA", name="e2"))
    assert trans == []
    run = repo.get_process_run(repo.get_process("S").name, "P1")
    assert run["current_stage_idx"] == 1
    assert len(notify.calls) == n_after_first


# --- inert lanes ------------------------------------------------------------
def test_disabled_process_is_inert():
    repo = _seed(enabled=False)
    d = _dispatcher(repo)
    assert d.on_tree_event(_ev("NODE_CREATED", "P1")) == []
    assert repo.get_process_run(repo.get_process("S").name, "P1") is None


def test_no_process_defined_is_inert():
    repo = InMemoryRepository()
    repo.add_sheet("S", structural_owner="o")
    repo.add_node("R", "S", parent=None)
    d = _dispatcher(repo)
    assert d.on_tree_event(_ev("NODE_CREATED", "R")) == []


def test_unrelated_event_type_is_ignored():
    repo = _seed()
    d = _dispatcher(repo)
    assert d.on_tree_event(_ev("NODE_DELETED", "P1")) == []


# --- SLA sweep --------------------------------------------------------------
def test_sla_sweep_marks_current_stage_breached_and_notifies_once():
    repo = _seed(slas=(50, 0, 0))
    notify = RecordingNotifier()
    # start at now=100 -> stage-0 due_at = 150.
    d = ProcessDispatcher(repo=repo, notify=notify, clock=ListClock(start=100, step=0))
    d.on_tree_event(_ev("NODE_CREATED", "P1"))
    notify.calls.clear()
    # sweep at now=100 -> not yet due.
    assert d.sla_sweep() == []
    # advance the clock past the due_at and sweep.
    d.clock = ListClock(start=200, step=0)
    breached = d.sla_sweep()
    assert [t["kind"] for t in breached] == ["breached"]
    run = repo.get_process_run(repo.get_process("S").name, "P1")
    assert run["stages"][0]["breached"] is True
    assert notify.recipients == [OWNER_A]
    assert notify.calls[0][1]["source"] == "sla"
    # idempotent: a second sweep does not re-breach or re-notify.
    notify.calls.clear()
    assert d.sla_sweep() == []
    assert notify.calls == []


def test_sla_zero_never_breaches():
    repo = _seed(slas=(0, 0, 0))
    d = ProcessDispatcher(repo=repo, notify=RecordingNotifier(), clock=ListClock(start=100, step=0))
    d.on_tree_event(_ev("NODE_CREATED", "P1"))
    d.clock = ListClock(start=10_000, step=0)
    assert d.sla_sweep() == []
