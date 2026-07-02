"""The pure process stage machine (Area 3 — Process/SLA).

ZERO frappe. A "process" is a per-sheet ordered list of column stages
(A -> B -> C). Each in-scope node (a "row") gets a per-row Process Run tracking
which stage is active + the timestamp each stage was entered/filled. Stage
advancement is DERIVED from the SAME Tree Event stream the notification/webhook
dispatchers consume — NOT a new EventType (the closed 11-type set is preserved):

    NODE_CREATED   (in-scope node)  -> start a run at stage 0, notify owner(A)
    NODE_VALUE_UPDATED (col==current stage column) -> advance, notify next owner
    terminal-stage fill -> status=completed

Per-transition SLA: each stage carries ``sla_seconds``; a stage's ``due_at`` =
``entered_at`` + sla; a sweep marks runs breached when ``now > due_at`` and the
stage is not yet filled. All of this is idempotent (a replayed event / a repeated
sweep does not double-advance or double-notify) via per-stage ``filled_at`` +
``notified_owner`` guards.

The module operates over the Repository PORT + the ONE ACL resolver
(``resolve_column_approvers``, resolved LIVE so re-grants reroute) and a plain
``notify`` sink so it is unit-testable against the in-memory doubles.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .acl import resolve_column_approvers
from .ports import ProcessView, Repository

#: A transition record returned by ``on_event`` — one per (run) side effect, so
#: callers/tests can assert exactly what happened without re-reading the store.
Transition = dict[str, Any]

#: Recipient-resolver + notify callback seam. ``notify(recipients, message)`` is
#: how a stage-enter / SLA-breach notification is fanned out. Defaults route
#: through ``repo.create_notification`` (in-app), but a caller may inject its own.
NotifyFn = Callable[[list[str], dict[str, Any]], None]


# ---------------------------------------------------------------------------
# Time helpers — the machine treats timestamps as comparable strings/ints. The
# adapter passes ISO-8601 strings (lexically ordered) or epoch seconds; the pure
# math is done on ``sla_seconds`` via ``add_seconds`` which the caller supplies
# for its clock. To stay framework-free we accept a pluggable ``add_seconds``.
# ---------------------------------------------------------------------------
def default_due_at(entered_at: Any, sla_seconds: int) -> Optional[Any]:
    """Compute a stage's ``due_at`` from ``entered_at`` + ``sla_seconds``.

    ``sla_seconds == 0`` means "no SLA" -> None (never breaches). When
    ``entered_at`` is an ``int``/``float`` epoch we add directly; otherwise we
    return a ``(entered_at, sla_seconds)`` marker the adapter resolves against its
    clock. Kept trivial + pure so tests use plain numeric timestamps.
    """
    if not sla_seconds:
        return None
    if isinstance(entered_at, (int, float)):
        return entered_at + sla_seconds
    return {"base": entered_at, "add_seconds": sla_seconds}


def _stage_owners(repo: Repository, sheet: str, column: str) -> list[str]:
    """The users responsible for a stage — resolved LIVE at notification time via
    the ONE ACL resolver, so a grantColumn re-grant / ``role:<key>`` reroute takes
    effect on the NEXT notification (mirrors CR decision-time re-resolution)."""
    return sorted(resolve_column_approvers(repo, sheet, column))


def _build_run_stages(
    process: ProcessView, started_at: Any
) -> list[dict[str, Any]]:
    """The per-stage ledger for a fresh run: stage 0 entered_at/due_at set,
    the rest pending. ``notified_owner`` is the idempotency guard on enter."""
    stages: list[dict[str, Any]] = []
    ordered = sorted(process.stages, key=lambda s: s.idx)
    for i, st in enumerate(ordered):
        entered = started_at if i == 0 else None
        stages.append(
            {
                "stage_idx": st.idx,
                "column": st.column,
                "entered_at": entered,
                "filled_at": None,
                "due_at": default_due_at(entered, st.sla_seconds) if i == 0 else None,
                "breached": False,
                "breached_at": None,
                "notified_owner": "",  # "" = not yet notified (idempotency guard)
            }
        )
    return stages


def _ordered_stages(process: ProcessView):
    return sorted(process.stages, key=lambda s: s.idx)


# ---------------------------------------------------------------------------
# Event-driven advancement
# ---------------------------------------------------------------------------
def on_event(
    repo: Repository,
    process: ProcessView,
    event: dict[str, Any],
    *,
    now: Any,
    notify: Optional[NotifyFn] = None,
) -> list[Transition]:
    """React to ONE Tree Event, mutating Process Runs via ``repo`` and returning
    the transitions performed (possibly empty).

    ``event`` is a light dict ``{type, node, column?, tree_event?}`` (the dispatch
    lane extracts these from the persisted Tree Event). ``now`` is the enter
    timestamp for a newly-entered stage. Idempotent: a replayed event never
    double-advances (guarded by ``current_stage_idx`` + ``filled_at``) or
    double-notifies (``notified_owner``).

    Only fires when ``process.enabled``; a disabled process is inert.
    """
    if not process.enabled:
        return []
    notify = notify or _default_notify(repo)
    etype = event.get("type")
    node = event.get("node")

    if etype == "NODE_CREATED":
        return _start_run(repo, process, node, now=now, notify=notify)
    if etype == "NODE_VALUE_UPDATED":
        return _maybe_advance(repo, process, event, now=now, notify=notify)
    return []


def _start_run(
    repo: Repository, process: ProcessView, node: str, *, now: Any, notify: NotifyFn
) -> list[Transition]:
    if node is None:
        return []
    # scope guard: only in-scope nodes become process rows.
    in_scope = set(repo.list_in_scope_nodes(process.sheet, process.row_scope))
    if node not in in_scope:
        return []
    # idempotency: never create a second run for the same (process, node).
    if repo.get_process_run(process.name, node) is not None:
        return []
    ordered = _ordered_stages(process)
    if not ordered:
        return []
    stages = _build_run_stages(process, now)
    run = repo.create_process_run(
        {
            "process": process.name,
            "sheet": process.sheet,
            "node": node,
            "status": "active",
            "current_stage_idx": ordered[0].idx,
            "started_at": now,
            "stages": stages,
        }
    )
    trans = [{"run": run, "node": node, "kind": "started", "stage_idx": ordered[0].idx}]
    trans += _notify_stage_enter(repo, process, run, node, 0, now, notify)
    return trans


def _maybe_advance(
    repo: Repository, process: ProcessView, event: dict[str, Any], *, now: Any, notify: NotifyFn
) -> list[Transition]:
    node = event.get("node")
    column = event.get("column")
    if node is None or column is None:
        return []
    run = repo.get_process_run(process.name, node)
    if run is None or run.get("status") != "active":
        return []
    stages = [dict(s) for s in run.get("stages") or []]
    cur_pos = _pos_of_idx(stages, run.get("current_stage_idx"))
    if cur_pos is None:
        return []
    cur = stages[cur_pos]
    # STRICT ordering (design default): only a fill on the CURRENT stage column
    # advances. A value update on any other column is ignored (out-of-order guard).
    if cur["column"] != column:
        return []
    # idempotency: if the current stage is already filled, do nothing (a repeated
    # edit of the same column must advance ONCE).
    if cur.get("filled_at") is not None:
        return []

    cur["filled_at"] = now
    trans: list[Transition] = [
        {"run": run["name"], "node": node, "kind": "filled", "stage_idx": cur["stage_idx"]}
    ]

    next_pos = cur_pos + 1
    if next_pos >= len(stages):
        # terminal fill -> complete the run; no further notify.
        repo.update_process_run(
            run["name"],
            {"status": "completed", "completed_at": now, "stages": stages, "current_stage_idx": cur["stage_idx"]},
        )
        trans.append({"run": run["name"], "node": node, "kind": "completed"})
        return trans

    nxt = stages[next_pos]
    nxt["entered_at"] = now
    # due_at from the definition's sla for that stage.
    sla = _sla_for_idx(process, nxt["stage_idx"])
    nxt["due_at"] = default_due_at(now, sla)
    repo.update_process_run(
        run["name"],
        {"current_stage_idx": nxt["stage_idx"], "stages": stages},
    )
    trans.append({"run": run["name"], "node": node, "kind": "advanced", "stage_idx": nxt["stage_idx"]})
    # re-read the persisted run so notify guard mutates the stored ledger.
    trans += _notify_stage_enter(repo, process, run["name"], node, next_pos, now, notify)
    return trans


def _notify_stage_enter(
    repo: Repository,
    process: ProcessView,
    run: str,
    node: str,
    stage_pos: int,
    now: Any,
    notify: NotifyFn,
) -> list[Transition]:
    """Notify the stage's live owners exactly once (``notified_owner`` guard).
    Honors the stage's ``notify_on_enter`` flag."""
    stored = repo.get_process_run(process.name, node)
    if stored is None:
        return []
    stages = [dict(s) for s in stored.get("stages") or []]
    if stage_pos >= len(stages):
        return []
    st = stages[stage_pos]
    if st.get("notified_owner"):  # already notified — idempotent
        return []
    ordered = _ordered_stages(process)
    defn = ordered[stage_pos] if stage_pos < len(ordered) else None
    if defn is not None and not defn.notify_on_enter:
        return []
    owners = _stage_owners(repo, process.sheet, st["column"])
    if not owners:
        return []
    notify(
        owners,
        {
            "source": "process",
            "op": "process-stage-assigned",
            "sheet": process.sheet,
            "node": node,
            "process": process.name,
            "stage_idx": st["stage_idx"],
            "column": st["column"],
        },
    )
    st["notified_owner"] = ",".join(owners)
    stages[stage_pos] = st
    repo.update_process_run(run, {"stages": stages})
    return [{"run": run, "node": node, "kind": "notified", "stage_idx": st["stage_idx"], "owners": owners}]


def _pos_of_idx(stages: list[dict[str, Any]], stage_idx: Any) -> Optional[int]:
    for pos, s in enumerate(stages):
        if s.get("stage_idx") == stage_idx:
            return pos
    return None


def _sla_for_idx(process: ProcessView, stage_idx: Any) -> int:
    for s in process.stages:
        if s.idx == stage_idx:
            return int(s.sla_seconds or 0)
    return 0


# ---------------------------------------------------------------------------
# SLA sweep
# ---------------------------------------------------------------------------
def sla_sweep(
    repo: Repository,
    now: Any,
    *,
    process_of: Optional[Callable[[str], ProcessView]] = None,
    notify: Optional[NotifyFn] = None,
) -> list[Transition]:
    """Mark the current stage of every candidate active run breached when
    ``now > due_at`` and the stage is not yet filled. Idempotent: an already-
    breached stage is skipped. Optionally notifies the stage owner once (when the
    owning process has ``sla_breach_notify`` and a ``process_of`` resolver is
    given). ``sla_seconds == 0`` stages have ``due_at == None`` and never breach.
    """
    breached: list[Transition] = []
    for run in repo.list_active_runs_with_due(now):
        stages = [dict(s) for s in run.get("stages") or []]
        pos = _pos_of_idx(stages, run.get("current_stage_idx"))
        if pos is None:
            continue
        st = stages[pos]
        due = st.get("due_at")
        if due is None or st.get("filled_at") is not None or st.get("breached"):
            continue
        if not _past_due(now, due):
            continue
        st["breached"] = True
        st["breached_at"] = now
        stages[pos] = st
        repo.update_process_run(run["name"], {"stages": stages})
        rec: Transition = {
            "run": run["name"],
            "node": run.get("node"),
            "kind": "breached",
            "stage_idx": st["stage_idx"],
        }
        if process_of is not None and notify is not None:
            proc = process_of(run["process"])
            if proc is not None and getattr(proc, "sla_breach_notify", False):
                owners = _stage_owners(repo, proc.sheet, st["column"])
                if owners:
                    notify(
                        owners,
                        {
                            "source": "sla",
                            "op": "process-stage-due",
                            "sheet": proc.sheet,
                            "node": run.get("node"),
                            "process": proc.name,
                            "stage_idx": st["stage_idx"],
                            "column": st["column"],
                        },
                    )
                    rec["owners"] = owners
        breached.append(rec)
    return breached


def _past_due(now: Any, due: Any) -> bool:
    """``now > due``. Numeric epochs compare directly; ISO strings compare
    lexically (ISO-8601 is lexically ordered). The ``default_due_at`` marker dict
    is treated as not-yet-resolvable here (adapter resolves it) -> never breaches
    in the pure path unless numeric/string."""
    if isinstance(due, dict):
        return False
    try:
        return now > due
    except TypeError:  # pragma: no cover - defensive: incomparable types
        return False


# ---------------------------------------------------------------------------
# Dashboard aggregation (pure)
# ---------------------------------------------------------------------------
def dashboard_aggregate(
    process: ProcessView, runs: list[dict[str, Any]]
) -> dict[str, Any]:
    """Pure aggregation over a list of run dicts -> the kanban/flow metrics:
    per-stage pending_count / breached_count / avg_enter_to_fill_seconds, plus
    throughput totals. Table-driven-testable; no repo access."""
    ordered = _ordered_stages(process)
    stage_out: list[dict[str, Any]] = []
    total_active = sum(1 for r in runs if r.get("status") == "active")
    total_completed = sum(1 for r in runs if r.get("status") == "completed")

    for defn in ordered:
        pending = 0
        breached = 0
        durations: list[float] = []
        for r in runs:
            st = _stage_in_run(r, defn.idx)
            if st is None:
                continue
            if st.get("breached"):
                breached += 1
            filled = st.get("filled_at")
            entered = st.get("entered_at")
            if filled is None and entered is not None and r.get("status") == "active" \
                    and r.get("current_stage_idx") == defn.idx:
                pending += 1
            if filled is not None and entered is not None:
                d = _duration_seconds(entered, filled)
                if d is not None:
                    durations.append(d)
        avg = (sum(durations) / len(durations)) if durations else None
        stage_out.append(
            {
                "idx": defn.idx,
                "column": defn.column,
                "pending_count": pending,
                "breached_count": breached,
                "avg_enter_to_fill_seconds": avg,
            }
        )

    return {
        "stages": stage_out,
        "total_active": total_active,
        "total_completed": total_completed,
        "throughput": total_completed,
    }


def _stage_in_run(run: dict[str, Any], stage_idx: Any) -> Optional[dict[str, Any]]:
    for s in run.get("stages") or []:
        if s.get("stage_idx") == stage_idx:
            return s
    return None


def _duration_seconds(entered: Any, filled: Any) -> Optional[float]:
    if isinstance(entered, (int, float)) and isinstance(filled, (int, float)):
        return float(filled - entered)
    return None


# ---------------------------------------------------------------------------
# Default notify sink (in-app Notification rows via the repo)
# ---------------------------------------------------------------------------
def _default_notify(repo: Repository) -> NotifyFn:
    def _notify(recipients: list[str], data: dict[str, Any]) -> None:
        for r in recipients:
            repo.create_notification(
                {
                    "recipient": r,
                    "tree_event": None,
                    "requires_ack": False,
                    **data,
                }
            )

    return _notify
