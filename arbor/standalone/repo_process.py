"""ProcessRepoMixin — the process / SLA slice of the standalone Repository.

Implements the ``arbor.core.ports.Repository`` process methods (upsert_process
… list_active_runs_with_due) over SQLAlchemy, mirroring
``core.testing.InMemoryRepository`` semantics exactly:

* ONE process per sheet — ``upsert_process`` replaces the definition in place,
  PRESERVING the ``enabled`` flag across a redefine (a new process starts
  disabled).
* Runs are UNIQUE per (process, node); the expectation ledger is one row per
  (run, rule_key, expected_column), and ``update_process_run`` replaces the
  whole ledger (the pure machine in ``core.process`` mutates the full list and
  persists it back, exactly like the frappe adapter's ``doc.set``).
* ``list_active_runs_with_due`` is the bounded SLA-sweep candidate set: active
  runs carrying an OPEN (unsatisfied, un-breached) expectation whose ``due_at``
  is set and already due.

Timestamp plumbing: the pure machine has no clock, so ``opened_at``/``due_at``
arrive as datetimes, ISO strings, numeric epochs (tests), or the
``{base, add_seconds}`` marker ``core.process.default_due_at`` emits for a
string base. Everything is coerced to a naive-UTC ``datetime`` on WRITE
(``_to_dt`` — the analog of the frappe adapter's ``_resolve_due``) and returned
as ``str(datetime)`` ("YYYY-MM-DD HH:MM:SS", lexically ordered) on READ, the
same shape the frappe adapter's ``_run_dict`` produces, so ``core.process``'s
string comparisons keep working.

Assumes ``self.session`` (a SQLAlchemy ``Session``) on the composing class.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import sqlalchemy as sa

from .models import Node, Process, ProcessRule, ProcessRun, ProcessRunExpectation


# ---------------------------------------------------------------------------
# Local read views (structurally satisfy ports.ProcessView/ProcessRuleView).
# Defined here rather than in views.py: the process slice is their only
# producer, and keeping them local keeps the mixin files independently
# authorable.
# ---------------------------------------------------------------------------
@dataclass
class _ProcessRuleView:
    """One trigger->expectation rule (see ``ports.ProcessRuleView``).
    ``rule_key`` is the stable ledger ref; ``idx`` is presentation order ONLY."""

    rule_key: str
    idx: int
    trigger_kind: str  # 'row' | 'column'
    trigger_op: str  # 'created' | 'updated' | 'created-or-updated'
    expected_columns: list[str] = field(default_factory=list)
    # The trigger SET (1+); ``trigger_column`` is a back-compat alias == [0].
    trigger_columns: list[str] = field(default_factory=list)
    trigger_join: str = "any"  # 'any' | 'all'
    trigger_column: Optional[str] = None
    within_seconds: int = 0  # 0 => no SLA
    notify_on_expect: bool = True
    label: Optional[str] = None


@dataclass
class _ProcessView:
    """The per-sheet process definition (see ``ports.ProcessView``)."""

    name: str
    sheet: str
    title: str = ""
    enabled: bool = False
    row_scope: str = "root-children"  # 'root-children' | 'all-nodes' | 'depth'
    sla_breach_notify: bool = True
    rules: list[_ProcessRuleView] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Timestamp coercion (write side) + stringification (read side)
# ---------------------------------------------------------------------------
def _resolve_due(due: Any) -> Any:
    """Resolve the pure machine's ``{base, add_seconds}`` due marker into a real
    ISO timestamp string (mirror of FrappeRepository._resolve_due). The pure
    module is clockless, so a non-numeric ``opened_at`` yields this marker and
    ``_past_due`` treats an UNRESOLVED dict as never-due — persisting it verbatim
    made the SLA sweep permanently inert. Plain values pass through (idempotent).
    """
    if isinstance(due, dict) and "base" in due:
        from datetime import datetime, timedelta

        try:
            base = datetime.fromisoformat(str(due["base"]))
        except ValueError:
            return due
        return str(base + timedelta(seconds=int(due.get("add_seconds") or 0)))
    return due


def _to_dt(value: Any) -> Optional[datetime]:
    """Coerce a pure-machine timestamp into a naive-UTC ``datetime`` (the
    DATETIME column type), or None.

    Accepted shapes: ``datetime`` (tz-aware normalized to naive UTC), ISO
    string (``fromisoformat`` handles both the 'T' and the space separator),
    numeric epoch seconds (the bench-free tests use plain numbers), and the
    ``{base, add_seconds}`` marker from ``core.process.default_due_at`` — the
    adapter HAS a clock, so the marker resolves to ``base + add_seconds`` here
    (the frappe adapter's ``_resolve_due`` analog). An unparseable value
    coalesces to None rather than raising: the pure machine treats a missing
    timestamp as "no SLA", the safe direction.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, dict) and "base" in value:
        base = _to_dt(value.get("base"))
        if base is None:
            return None
        return base + timedelta(seconds=int(value.get("add_seconds") or 0))
    if isinstance(value, bool):  # bool is an int subclass; never a timestamp
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _dt_str(value: Optional[datetime]) -> Optional[str]:
    """``str(datetime)`` -> "YYYY-MM-DD HH:MM:SS" (lexically ordered), or None —
    the same read shape the frappe adapter's ``_run_dict`` returns."""
    return str(value) if value is not None else None


class ProcessRepoMixin:
    """Process / SLA Repository methods. Composed into the standalone
    Repository; requires ``self.session`` (SQLAlchemy ``Session``)."""

    session: sa.orm.Session  # provided by the composing class

    # ---- definition (Process + its rule child rows) ------------------------
    def upsert_process(self, data: dict[str, Any]) -> str:
        """Create or replace the sheet's process definition (+ rules); return
        its id. Exactly one process per sheet: an existing one is updated in
        place, its ``enabled`` flag PRESERVED across the redefine (matching
        InMemoryRepository — a fresh process starts disabled). Rule rows are
        fully replaced; each is normalized the same way the reference does
        (rule_key default ``r<i>``, trigger_columns/trigger_column alias sync,
        op default by kind)."""
        sheet = data["sheet"]
        row = self.session.scalars(
            sa.select(Process).where(Process.sheet == sheet).order_by(Process.creation)
        ).first()
        if row is None:
            row = Process(sheet=sheet, enabled=False)
            self.session.add(row)
            self.session.flush()  # assign row.name for the rule child rows
        # InMemory semantics: title/row_scope/sla_breach_notify take the given
        # value or the DEFAULT (not the prior value) on every redefine.
        row.title = data.get("title", "")
        row.row_scope = data.get("row_scope", "root-children")
        row.sla_breach_notify = bool(data.get("sla_breach_notify", True))
        # replace the rule set (delete-then-reinsert; deterministic child PKs).
        self.session.execute(
            sa.delete(ProcessRule).where(ProcessRule.process == row.name)
        )
        self.session.flush()
        for i, r in enumerate(data.get("rules") or []):
            kind = r["trigger_kind"]
            # normalize the trigger SET: prefer trigger_columns, else the single
            # trigger_column alias; keep trigger_column == the first entry.
            tcols = [c for c in (r.get("trigger_columns") or []) if c is not None]
            if not tcols and r.get("trigger_column"):
                tcols = [r["trigger_column"]]
            self.session.add(
                ProcessRule(
                    name=f"{row.name}#r{i:03d}",
                    process=row.name,
                    rule_key=r.get("rule_key") or f"r{i}",
                    idx=r.get("idx", i),
                    label=r.get("label"),
                    trigger_kind=kind,
                    trigger_column=(tcols[0] if tcols else None),
                    trigger_columns=tcols,
                    trigger_join=r.get("trigger_join") or "any",
                    trigger_op=r.get("trigger_op")
                    or ("created" if kind == "row" else "updated"),
                    expected_columns=list(r.get("expected_columns") or []),
                    within_seconds=int(r.get("within_seconds") or 0),
                    notify_on_expect=bool(r.get("notify_on_expect", True)),
                )
            )
        self.session.flush()
        return row.name

    def _process_view(self, row: Process) -> _ProcessView:
        """Build a ``ProcessView`` from a Process row + its rule child rows
        (ordered by stored ``idx`` — presentation order, never the ledger ref)."""
        rules = self.session.scalars(
            sa.select(ProcessRule)
            .where(ProcessRule.process == row.name)
            .order_by(ProcessRule.idx, ProcessRule.name)
        ).all()
        return _ProcessView(
            name=row.name,
            sheet=row.sheet,
            title=row.title or "",
            enabled=bool(row.enabled),
            row_scope=row.row_scope or "root-children",
            sla_breach_notify=bool(row.sla_breach_notify),
            rules=[
                _ProcessRuleView(
                    rule_key=r.rule_key,
                    idx=r.idx,
                    trigger_kind=r.trigger_kind,
                    trigger_op=r.trigger_op,
                    expected_columns=list(r.expected_columns or []),
                    trigger_columns=list(r.trigger_columns or []),
                    trigger_join=r.trigger_join or "any",
                    trigger_column=(r.trigger_columns or [None])[0],
                    within_seconds=int(r.within_seconds or 0),
                    notify_on_expect=bool(r.notify_on_expect),
                    label=r.label or None,
                )
                for r in rules
            ],
        )

    def get_process(self, sheet: str) -> Optional[_ProcessView]:
        """The sheet's process definition (enabled or not), or None."""
        row = self.session.scalars(
            sa.select(Process).where(Process.sheet == sheet).order_by(Process.creation)
        ).first()
        return self._process_view(row) if row is not None else None

    def get_process_by_name(self, process: str) -> Optional[_ProcessView]:
        """Resolve a run's ``process`` link back to its definition (the SLA
        sweep's ``process_of`` resolver / ``sla_breach_notify`` gate)."""
        row = self.session.get(Process, process)
        return self._process_view(row) if row is not None else None

    def set_process_enabled(self, process: str, enabled: bool) -> None:
        """Flip the process ``enabled`` flag. A missing process is a KeyError,
        same as the in-memory reference's dict lookup."""
        row = self.session.get(Process, process)
        if row is None:
            raise KeyError(process)
        row.enabled = bool(enabled)
        self.session.flush()

    # ---- row scope ----------------------------------------------------------
    def list_in_scope_nodes(self, sheet: str, row_scope: str) -> list[str]:
        """Node ids that count as process 'rows' under ``row_scope``. Mirrors
        the in-memory double: ``all-nodes`` = every node; ``root-children``
        (default) / ``depth`` = the direct children of a root (a node whose own
        parent is None)."""
        rows = self.session.execute(
            sa.select(Node.name, Node.parent).where(Node.sheet == sheet)
        ).all()
        if row_scope == "all-nodes":
            return [name for name, _parent in rows]
        roots = {name for name, parent in rows if parent is None}
        return [name for name, parent in rows if parent in roots]

    # ---- runs + the expectation ledger --------------------------------------
    def _set_expectations(self, run: str, exps: list[dict[str, Any]]) -> None:
        """Replace the run's whole expectation ledger (the pure machine hands
        back the full mutated list). Deterministic child PKs (``<run>#NNNN``)
        preserve the machine's append order across the delete-then-reinsert,
        so reads return the ledger in the exact order InMemoryRepository would.

        Each dict is stored VERBATIM in ``raw`` (the machine is clockless, so
        its timestamps — numeric epochs, ISO strings — must round-trip
        untouched, exactly as the in-memory reference keeps them); the typed
        datetime columns are best-effort COERCED copies for SQL-side filtering,
        and ``open_due`` precomputes the sweep's candidate predicate from the
        raw values."""
        self.session.execute(
            sa.delete(ProcessRunExpectation).where(ProcessRunExpectation.run == run)
        )
        self.session.flush()  # PKs are reused; the deletes must hit first
        for i, e in enumerate(exps or []):
            raw = dict(e)
            # Resolve the clockless due marker HERE (the adapter has the clock);
            # raw carries the RESOLVED value so the sweep's string compare works.
            raw["due_at"] = _resolve_due(raw.get("due_at"))
            self.session.add(
                ProcessRunExpectation(
                    name=f"{run}#{i:04d}",
                    run=run,
                    rule_key=raw.get("rule_key"),
                    expected_column=raw.get("expected_column"),
                    opened_at=_to_dt(raw.get("opened_at")),
                    satisfied_at=_to_dt(raw.get("satisfied_at")),
                    due_at=_to_dt(raw.get("due_at")),
                    breached=bool(raw.get("breached")),
                    breached_at=_to_dt(raw.get("breached_at")),
                    notified_owner=raw.get("notified_owner") or "",
                    open_due=(
                        raw.get("due_at") is not None
                        and raw.get("satisfied_at") is None
                        and not raw.get("breached")
                    ),
                    raw=raw,
                )
            )
        self.session.flush()

    def _run_dict(self, row: ProcessRun) -> dict[str, Any]:
        """A run row + its ledger as the plain dict shape ``core.process``
        consumes. Timestamps come back from the RAW (verbatim) store — the
        clockless machine compares/equates them against the ``now`` values it
        was handed, so a numeric epoch must read back as the same number, not
        a stringified datetime. The typed columns fall back only for legacy
        rows written before the raw store existed."""
        exps = self.session.scalars(
            sa.select(ProcessRunExpectation)
            .where(ProcessRunExpectation.run == row.name)
            .order_by(ProcessRunExpectation.name)
        ).all()
        raw = row.raw or {}
        return {
            "name": row.name,
            "process": row.process,
            "sheet": row.sheet,
            "node": row.node,
            "status": row.status,
            "started_at": raw.get("started_at", _dt_str(row.started_at)),
            "completed_at": raw.get("completed_at", _dt_str(row.completed_at)),
            "expectations": [
                dict(e.raw)
                if e.raw is not None
                else {
                    "rule_key": e.rule_key,
                    "expected_column": e.expected_column,
                    "opened_at": _dt_str(e.opened_at),
                    "satisfied_at": _dt_str(e.satisfied_at),
                    "due_at": _dt_str(e.due_at),
                    "breached": bool(e.breached),
                    "breached_at": _dt_str(e.breached_at),
                    "notified_owner": e.notified_owner or "",
                }
                for e in exps
            ],
        }

    def create_process_run(self, data: dict[str, Any]) -> str:
        """Create a Process Run (+ its expectation ledger). ``data`` =
        {process, sheet, node, status, started_at, expectations:[...]}."""
        row = ProcessRun(
            process=data["process"],
            sheet=data["sheet"],
            node=data["node"],
            status=data.get("status", "active"),
            started_at=_to_dt(data.get("started_at")),
            completed_at=_to_dt(data.get("completed_at")),
            raw={
                "started_at": data.get("started_at"),
                "completed_at": data.get("completed_at"),
            },
        )
        self.session.add(row)
        self.session.flush()  # assign row.name for the ledger child rows
        self._set_expectations(row.name, data.get("expectations") or [])
        return row.name

    def get_process_run(self, process: str, node: str) -> Optional[dict[str, Any]]:
        """The run for (process, node), or None (unique per pair)."""
        row = self.session.scalars(
            sa.select(ProcessRun)
            .where(ProcessRun.process == process, ProcessRun.node == node)
            .order_by(ProcessRun.creation)
        ).first()
        return self._run_dict(row) if row is not None else None

    def update_process_run(self, run: str, patch: dict[str, Any]) -> None:
        """Patch a run row. ``expectations`` replaces the WHOLE ledger; the
        timestamp fields coerce like every other write; ``status`` and anything
        else set as-is (the reference's ``dict.update``)."""
        row = self.session.get(ProcessRun, run)
        if row is None:
            raise KeyError(run)
        for k, v in (patch or {}).items():
            if k == "expectations":
                self._set_expectations(run, v or [])
            elif k in ("started_at", "completed_at"):
                setattr(row, k, _to_dt(v))
                # keep the verbatim copy in step (reassign: JSON mutation is
                # untracked by the ORM).
                row.raw = {**(row.raw or {}), k: v}
            else:
                setattr(row, k, v)
        self.session.flush()

    def list_process_runs(
        self, sheet: str, status: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Runs for ``sheet`` (optionally filtered by status) — dashboard source."""
        stmt = (
            sa.select(ProcessRun)
            .where(ProcessRun.sheet == sheet)
            .order_by(ProcessRun.creation, ProcessRun.name)
        )
        if status is not None:
            stmt = stmt.where(ProcessRun.status == status)
        return [self._run_dict(r) for r in self.session.scalars(stmt).all()]

    def list_active_runs_with_due(self, now: Any) -> list[dict[str, Any]]:
        """Active runs carrying an OPEN (due_at set, unsatisfied, un-breached)
        expectation — the SLA-sweep candidate set, filtered in SQL on the
        precomputed ``open_due`` flag. Deliberately NOT narrowed by ``now``:
        the in-memory reference ignores it (the machine's clockless timestamps
        need not be SQL-comparable to ``now``), and the sweep re-compares
        ``due_at <= now`` on the raw values itself."""
        e = ProcessRunExpectation
        stmt = (
            sa.select(ProcessRun)
            .where(ProcessRun.status == "active")
            .where(
                sa.exists(
                    sa.select(e.name).where(e.run == ProcessRun.name, e.open_due.is_(True))
                )
            )
            .order_by(ProcessRun.creation, ProcessRun.name)
        )
        return [self._run_dict(r) for r in self.session.scalars(stmt).all()]
