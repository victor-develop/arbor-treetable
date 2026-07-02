"""The pure process RULE/DAG evaluator (process DAG + SLA).

ZERO frappe. A "process" is a per-sheet SET of trigger->expectation RULES (not an
ordered stage list). Each rule reads:

    "On <trigger>: expect (colA [and colB...]) filled within <within_seconds>"

where ``<trigger>`` is a ROW event (a node created/updated — the canvas START
node) or a COLUMN event (a specific ``trigger_column`` created/updated). Rules
compose into a DAG because a column EXPECTED by one rule may be the
``trigger_column`` of another. Per-row tracking is ONE Expectation per
``(run, rule_key, expected_column)``.

Runtime is a THIRD pure consumer off the SAME Tree Event stream the notification/
webhook dispatchers consume — NO new EventType (the closed 11-type set stands):

    NODE_CREATED (in-scope node)
        -> create a run, fire every ROW rule, treat every already-filled column
           as an implicit column trigger (op 'created'), open the resulting
           expectations, SATISFY any whose expected column is already filled
           (a default / head-label counts), and CASCADE downstream
           (cycle-guarded). If the run is quiescent it completes at creation.
    NODE_VALUE_UPDATED(colX)
        -> SATISFY every open expectation on colX, then fire COLUMN(colX) rules,
           open + maybe-satisfy their expectations, and CASCADE. Complete when
           quiescent.
    sla_sweep(now)
        -> breach every OPEN expectation whose ``due_at <= now`` (idempotent),
           optionally notifying the expected column's live owner once.

"filled" == ``is_filled`` (non-empty; a default at row-creation counts). Owners
are resolved LIVE via the ONE ACL resolver so re-grants reroute automatically.
Idempotency spans MANY expectations per event: a per-(run, rule_key,
expected_column) existence guard + ``satisfied_at`` + ``notified_owner`` all hold
so replays never double-open, double-satisfy, or double-notify.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .acl import resolve_column_approvers
from .ports import ProcessView, Repository
from .process_graph import START, build_edges, trigger_columns_of

#: A transition record returned by ``on_event``/``sla_sweep`` — one per side
#: effect, so callers/tests assert exactly what happened without re-reading.
Transition = dict[str, Any]

#: Recipient-resolver + notify callback seam. ``notify(recipients, data)`` fans
#: out an expectation-open / SLA-breach notification. Defaults route through
#: ``repo.create_notification`` (in-app), but a caller may inject its own.
NotifyFn = Callable[[list[str], dict[str, Any]], None]


# ---------------------------------------------------------------------------
# Empty-check + SLA math (unchanged public helpers)
# ---------------------------------------------------------------------------
def is_filled(value: Any) -> bool:
    """A column is FILLED iff its value is non-empty.

    Mirrors the FRONTEND cell empty-check (``renderStatic`` in
    ``frontend/src/components/cells/Cell.tsx``): a value renders "empty" when
    ``renderStatic(value)`` is ``""``. ``renderStatic`` returns ``""`` for
    ``None``, joins arrays (so ``[]`` -> ``""``), and otherwise ``str(value)``.

    Therefore NOT-filled == ``None`` / ``""`` / ``[]`` (empty tuple too).
    Everything else is filled — notably a value written at row-creation (a
    DEFAULT, or the head/label cell) COUNTS. Numeric ``0`` / boolean ``False``
    render to non-empty strings on the grid, so they are filled here too.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return value != ""
    if isinstance(value, (list, tuple)):
        return len(value) > 0
    return True


def default_due_at(opened_at: Any, within_seconds: int) -> Optional[Any]:
    """Compute an expectation's ``due_at`` from ``opened_at`` + ``within_seconds``.

    ``within_seconds == 0`` means "no SLA" -> None (never breaches). A numeric
    epoch adds directly; otherwise a ``{base, add_seconds}`` marker the adapter
    resolves against its clock is returned. Kept trivial + pure so tests use
    plain numeric timestamps.
    """
    if not within_seconds:
        return None
    if isinstance(opened_at, (int, float)):
        return opened_at + within_seconds
    return {"base": opened_at, "add_seconds": within_seconds}


# ---------------------------------------------------------------------------
# Rule helpers
# ---------------------------------------------------------------------------
def _rule_key(rule: "Any", idx: int) -> str:
    key = getattr(rule, "rule_key", None)
    return key if key else f"r{idx}"


def _ordered_rules(process: ProcessView) -> list[Any]:
    """Rules in presentation order (by ``idx``); ``idx`` is NOT the ledger ref."""
    return sorted(process.rules, key=lambda r: getattr(r, "idx", 0))


def _expected_owners(repo: Repository, sheet: str, column: str) -> list[str]:
    """The users responsible for an expected column — resolved LIVE at
    notification time via the ONE ACL resolver, so a grantColumn re-grant /
    ``role:<key>`` reroute takes effect on the NEXT notification."""
    return sorted(resolve_column_approvers(repo, sheet, column))


def _row_trigger_fires(trigger_op: str, etype: str) -> bool:
    """Whether a ROW rule with ``trigger_op`` fires for this event type."""
    if etype == "NODE_CREATED":
        return trigger_op in ("created", "created-or-updated")
    if etype == "NODE_VALUE_UPDATED":
        return trigger_op in ("updated", "created-or-updated")
    return False


def _trigger_columns(rule: Any) -> list[str]:
    """The rule's trigger SET (normalizes the single ``trigger_column`` alias)."""
    return trigger_columns_of(rule)


def _trigger_join(rule: Any) -> str:
    """A column rule's join mode: 'all' (AND-join / fan-in) or 'any' (default)."""
    return getattr(rule, "trigger_join", None) or "any"


def _all_triggers_filled(repo: Repository, node: str, rule: Any) -> bool:
    """Whether EVERY trigger column of ``rule`` is currently filled (the AND-join
    completion predicate)."""
    cols = _trigger_columns(rule)
    return bool(cols) and all(is_filled(repo.get_value(node, c)) for c in cols)


# ---------------------------------------------------------------------------
# Expectation-ledger primitives (operate on a mutable list of expectation dicts)
# ---------------------------------------------------------------------------
def _exp_key(exp: dict[str, Any]) -> tuple[str, str]:
    return (exp.get("rule_key"), exp.get("expected_column"))


def _has_expectation(exps: list[dict[str, Any]], rule_key: str, column: str) -> bool:
    return any(_exp_key(e) == (rule_key, column) for e in exps)


def _open_expectation(
    exps: list[dict[str, Any]], rule_key: str, column: str, *, opened_at: Any, within_seconds: int
) -> dict[str, Any]:
    """Append one OPEN expectation (idempotent via the existence guard upstream)."""
    exp = {
        "rule_key": rule_key,
        "expected_column": column,
        "opened_at": opened_at,
        "satisfied_at": None,
        "due_at": default_due_at(opened_at, within_seconds),
        "breached": False,
        "breached_at": None,
        "notified_owner": "",
    }
    exps.append(exp)
    return exp


# ---------------------------------------------------------------------------
# Event-driven evaluation
# ---------------------------------------------------------------------------
def on_event(
    repo: Repository,
    process: ProcessView,
    event: dict[str, Any],
    *,
    now: Any,
    notify: Optional[NotifyFn] = None,
) -> list[Transition]:
    """React to ONE Tree Event, mutating the Process Run for the event's node and
    returning the transitions performed (possibly empty).

    ``event`` is ``{type, node, column?, tree_event?}``. ``now`` is the
    open/satisfy timestamp. Idempotent: a replayed event never double-opens
    (per-(rule_key, expected_column) existence guard), double-satisfies
    (``satisfied_at``) or double-notifies (``notified_owner``). Only fires when
    ``process.enabled``; a disabled process is inert.
    """
    if not process.enabled:
        return []
    notify = notify or _default_notify(repo)
    etype = event.get("type")
    node = event.get("node")
    if node is None:
        return []

    if etype == "NODE_CREATED":
        return _on_node_created(repo, process, node, now=now, notify=notify)
    if etype == "NODE_VALUE_UPDATED":
        column = event.get("column")
        if column is None:
            return []
        return _on_value_updated(repo, process, node, column, now=now, notify=notify)
    return []


def _on_node_created(
    repo: Repository, process: ProcessView, node: str, *, now: Any, notify: NotifyFn
) -> list[Transition]:
    # scope guard: only in-scope nodes become process rows.
    in_scope = set(repo.list_in_scope_nodes(process.sheet, process.row_scope))
    if node not in in_scope:
        return []
    # idempotency: never create a second run for the same (process, node).
    if repo.get_process_run(process.name, node) is not None:
        return []

    run_name = repo.create_process_run(
        {
            "process": process.name,
            "sheet": process.sheet,
            "node": node,
            "status": "active",
            "started_at": now,
            "expectations": [],
        }
    )
    trans: list[Transition] = [{"run": run_name, "node": node, "kind": "started"}]

    exps: list[dict[str, Any]] = []
    fired: set[str] = set()  # rule_keys already fired this event (cascade guard)

    # 1) fire every ROW rule that matches NODE_CREATED.
    for i, rule in enumerate(_ordered_rules(process)):
        if getattr(rule, "trigger_kind", None) != "row":
            continue
        if not _row_trigger_fires(getattr(rule, "trigger_op", "created"), "NODE_CREATED"):
            continue
        _fire_rule(exps, rule, i, now=now)
        fired.add(_rule_key(rule, i))

    # 2) treat every already-filled column as an implicit column trigger with
    #    op 'created': fire column rules whose trigger_column is filled at
    #    creation AND whose trigger_op includes 'created' (an 'updated'-only rule
    #    is NOT fired at creation — nothing was updated). This reproduces
    #    "defaults count as filled + auto-advance" without spuriously firing
    #    update-only rules.
    for i, rule in enumerate(_ordered_rules(process)):
        if getattr(rule, "trigger_kind", None) != "column":
            continue
        rk = _rule_key(rule, i)
        if rk in fired:
            continue
        if getattr(rule, "trigger_op", "updated") not in ("created", "created-or-updated"):
            continue
        cols = _trigger_columns(rule)
        if not cols:
            continue
        if _trigger_join(rule) == "all":
            # AND-join fires at creation only when ALL trigger columns are filled.
            if _all_triggers_filled(repo, node, rule):
                _fire_rule(exps, rule, i, now=now)
                fired.add(rk)
        elif any(is_filled(repo.get_value(node, c)) for c in cols):
            _fire_rule(exps, rule, i, now=now)
            fired.add(rk)

    # 3) satisfy every expectation whose expected column is already filled, and
    #    CASCADE (a satisfied column may be another column rule's trigger).
    trans += _satisfy_and_cascade(
        repo, process, node, exps, fired, now=now, notify=notify, initial=True
    )

    # 4) notify open (unsatisfied) expectations' owners once.
    trans += _notify_open(repo, process, node, exps, now=now, notify=notify)

    # persist + maybe complete.
    repo.update_process_run(run_name, {"expectations": exps})
    trans += _maybe_complete(repo, process, node, run_name, exps, now=now)
    return trans


def _on_value_updated(
    repo: Repository, process: ProcessView, node: str, column: str, *, now: Any, notify: NotifyFn
) -> list[Transition]:
    run = repo.get_process_run(process.name, node)
    if run is None or run.get("status") != "active":
        return []
    exps = [dict(e) for e in run.get("expectations") or []]
    fired: set[str] = set()

    # fire COLUMN rules whose trigger SET contains ``column`` (op 'updated' /
    # 'created-or-updated' always; op 'created' fires only if this rule has not
    # fired for the run yet — the first time the column becomes filled).
    #
    # join='any' (or a single trigger): fire when ANY named trigger column is
    # updated (today's behavior). join='all' (AND-join / fan-in): fire ONCE only
    # when EVERY trigger column is filled — the update that completes the set is
    # the trigger moment. Idempotency is per (run, rule): once fired (its
    # expectation exists) it never re-opens.
    for i, rule in enumerate(_ordered_rules(process)):
        if getattr(rule, "trigger_kind", None) != "column":
            continue
        if column not in _trigger_columns(rule):
            continue
        rk = _rule_key(rule, i)
        op = getattr(rule, "trigger_op", "updated")
        already = any(e.get("rule_key") == rk for e in exps)
        if op == "created" and already:
            continue  # 'created' fires once (first fill only)
        if _trigger_join(rule) == "all":
            if already:
                continue  # AND-join fires ONCE per run+rule
            if not _all_triggers_filled(repo, node, rule):
                continue  # the set is not complete yet -> do not fire
        if _fire_rule(exps, rule, i, now=now):
            fired.add(rk)

    # satisfy open expectations on this column + cascade downstream.
    trans = _satisfy_and_cascade(
        repo, process, node, exps, fired, now=now, notify=notify,
        initial=False, satisfied_col=column,
    )
    trans += _notify_open(repo, process, node, exps, now=now, notify=notify)

    repo.update_process_run(run["name"], {"expectations": exps})
    trans += _maybe_complete(repo, process, node, run["name"], exps, now=now)
    return trans


def _fire_rule(exps: list[dict[str, Any]], rule: Any, idx: int, *, now: Any) -> bool:
    """Open one expectation per expected column of ``rule`` (skipping any that
    already exists — the per-(rule_key, expected_column) idempotency guard).
    Returns True if any NEW expectation was opened."""
    rk = _rule_key(rule, idx)
    within = int(getattr(rule, "within_seconds", 0) or 0)
    opened_any = False
    for col in getattr(rule, "expected_columns", None) or []:
        if _has_expectation(exps, rk, col):
            continue
        _open_expectation(exps, rk, col, opened_at=now, within_seconds=within)
        opened_any = True
    return opened_any


def _satisfy_and_cascade(
    repo: Repository,
    process: ProcessView,
    node: str,
    exps: list[dict[str, Any]],
    fired: set[str],
    *,
    now: Any,
    notify: NotifyFn,
    initial: bool,
    satisfied_col: Optional[str] = None,
) -> list[Transition]:
    """Satisfy expectations whose expected column is filled, then fire any
    downstream column rule the newly-satisfied column triggers, opening its
    expectations and repeating to a fixpoint. Cycle-guarded by ``fired`` (a rule
    fires at most once per event) + the per-(rule_key, expected_column)
    existence guard so a cyclic-but-passed-validation edge can never spin."""
    trans: list[Transition] = []
    ordered = _ordered_rules(process)
    # Seed the work list: columns to (re)check for satisfaction.
    if initial:
        # every distinct expected column present so far.
        pending_cols = {e["expected_column"] for e in exps}
    else:
        pending_cols = {satisfied_col} if satisfied_col else set()

    processed_cols: set[str] = set()
    while pending_cols:
        col = pending_cols.pop()
        processed_cols.add(col)
        filled = is_filled(repo.get_value(node, col)) if initial else True
        if not filled:
            continue
        # satisfy every OPEN expectation on this column.
        newly_satisfied = False
        for e in exps:
            if e["expected_column"] != col:
                continue
            if e.get("satisfied_at") is not None or e.get("breached"):
                continue
            e["satisfied_at"] = now
            newly_satisfied = True
            trans.append(
                {"run": None, "node": node, "kind": "satisfied",
                 "rule_key": e["rule_key"], "column": col}
            )
        if not newly_satisfied and initial is False:
            # nothing to cascade from a column with no open expectation.
            # (still allow initial pass to fire downstream from prefilled cols.)
            pass
        # CASCADE: this column may be the trigger_column of downstream rules.
        # A cascade observes ``col`` as ALREADY filled (a default / a just-
        # satisfied prefilled column), so it counts as a 'created' observation:
        # only rules whose trigger_op includes 'created' cascade-fire. The
        # directly-updated column's own rules are fired by the caller (and are
        # already in ``fired``), so an 'updated'-only rule still fires there.
        for i, rule in enumerate(ordered):
            if getattr(rule, "trigger_kind", None) != "column":
                continue
            if col not in _trigger_columns(rule):
                continue
            rk = _rule_key(rule, i)
            if rk in fired:
                continue
            # a rule already opened for this run must not re-open (AND-join fires
            # once; the per-(rule_key, column) guard covers any-join too).
            if any(e.get("rule_key") == rk for e in exps):
                fired.add(rk)
                continue
            if getattr(rule, "trigger_op", "updated") not in ("created", "created-or-updated"):
                fired.add(rk)  # not eligible on a cascade observation; mark seen
                continue
            # a downstream column rule fires once its trigger column is filled.
            if not is_filled(repo.get_value(node, col)):
                continue
            # AND-join: cascade-fire only when EVERY trigger column is filled.
            if _trigger_join(rule) == "all" and not _all_triggers_filled(repo, node, rule):
                continue
            if _fire_rule(exps, rule, i, now=now):
                # its expected columns may be pre-filled -> re-check them.
                for c in getattr(rule, "expected_columns", None) or []:
                    if c not in processed_cols:
                        pending_cols.add(c)
            fired.add(rk)
    return trans


def _notify_open(
    repo: Repository,
    process: ProcessView,
    node: str,
    exps: list[dict[str, Any]],
    *,
    now: Any,
    notify: NotifyFn,
) -> list[Transition]:
    """Notify the live owners of every OPEN (unsatisfied, un-notified)
    expectation whose rule opted into ``notify_on_expect`` — exactly once
    (``notified_owner`` guard)."""
    trans: list[Transition] = []
    # rule_key -> notify_on_expect flag (default True).
    notify_flag: dict[str, bool] = {}
    for i, rule in enumerate(_ordered_rules(process)):
        notify_flag[_rule_key(rule, i)] = bool(getattr(rule, "notify_on_expect", True))

    for e in exps:
        if e.get("satisfied_at") is not None or e.get("breached"):
            continue
        if e.get("notified_owner"):
            continue
        if not notify_flag.get(e["rule_key"], True):
            continue
        owners = _expected_owners(repo, process.sheet, e["expected_column"])
        if not owners:
            continue
        notify(
            owners,
            {
                "source": "process",
                "op": "process-expect-opened",
                "sheet": process.sheet,
                "node": node,
                "process": process.name,
                "rule_key": e["rule_key"],
                "column": e["expected_column"],
            },
        )
        e["notified_owner"] = ",".join(owners)
        trans.append(
            {"run": None, "node": node, "kind": "notified",
             "rule_key": e["rule_key"], "column": e["expected_column"], "owners": owners}
        )
    return trans


def _maybe_complete(
    repo: Repository,
    process: ProcessView,
    node: str,
    run_name: str,
    exps: list[dict[str, Any]],
    *,
    now: Any,
) -> list[Transition]:
    """Complete the run when it is QUIESCENT: at least one expectation exists,
    every expectation is satisfied-or-breached, and no rule that could still fire
    (its trigger already occurred) remains unfired. A run with NO expectations at
    all (a row rule expecting nothing, or nothing triggered) stays active until a
    trigger opens work."""
    if not exps:
        return []
    if any(e.get("satisfied_at") is None and not e.get("breached") for e in exps):
        return []
    # no OPEN expectation. Check no un-fired rule whose trigger has occurred:
    # a column rule whose trigger_column is filled but which has no expectation.
    fired_keys = {e["rule_key"] for e in exps}
    for i, rule in enumerate(_ordered_rules(process)):
        rk = _rule_key(rule, i)
        if rk in fired_keys:
            continue
        if getattr(rule, "trigger_kind", None) == "column":
            cols = _trigger_columns(rule)
            if not cols:
                continue
            if _trigger_join(rule) == "all":
                # an AND-join is triggerable only when ALL columns are filled; a
                # partial set can never fire, so it never blocks quiescence.
                triggerable = _all_triggers_filled(repo, node, rule)
            else:
                triggerable = any(is_filled(repo.get_value(node, c)) for c in cols)
            if triggerable:
                return []  # a triggerable rule has not fired -> not quiescent
    run = repo.get_process_run(process.name, node)
    if run is not None and run.get("status") == "completed":
        return []
    repo.update_process_run(run_name, {"status": "completed", "completed_at": now})
    return [{"run": run_name, "node": node, "kind": "completed"}]


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
    """Breach every OPEN expectation whose ``due_at <= now`` across candidate
    active runs. Idempotent: an already-breached or already-satisfied expectation
    is skipped. Optionally notifies the expected column's live owner once (when
    the owning process has ``sla_breach_notify`` and a ``process_of`` resolver is
    given). ``within_seconds == 0`` expectations have ``due_at == None`` and never
    breach."""
    breached: list[Transition] = []
    for run in repo.list_active_runs_with_due(now):
        exps = [dict(e) for e in run.get("expectations") or []]
        changed = False
        proc = None
        if process_of is not None:
            proc = process_of(run.get("process"))
        for e in exps:
            due = e.get("due_at")
            if due is None or e.get("satisfied_at") is not None or e.get("breached"):
                continue
            if not _past_due(now, due):
                continue
            e["breached"] = True
            e["breached_at"] = now
            changed = True
            rec: Transition = {
                "run": run["name"],
                "node": run.get("node"),
                "kind": "breached",
                "rule_key": e.get("rule_key"),
                "column": e.get("expected_column"),
            }
            if proc is not None and notify is not None and getattr(proc, "sla_breach_notify", False):
                owners = _expected_owners(repo, proc.sheet, e["expected_column"])
                if owners:
                    notify(
                        owners,
                        {
                            "source": "sla",
                            "op": "process-expect-due",
                            "sheet": proc.sheet,
                            "node": run.get("node"),
                            "process": proc.name,
                            "rule_key": e.get("rule_key"),
                            "column": e["expected_column"],
                        },
                    )
                    rec["owners"] = owners
            breached.append(rec)
        if changed:
            repo.update_process_run(run["name"], {"expectations": exps})
            # a breach may make the run quiescent -> complete it.
            if proc is not None:
                _maybe_complete(repo, proc, run.get("node"), run["name"], exps, now=now)
    return breached


def _past_due(now: Any, due: Any) -> bool:
    """``now >= due``. Numeric epochs compare directly; ISO strings compare
    lexically (ISO-8601 is lexically ordered). The ``{base, add_seconds}`` marker
    dict is treated as not-yet-resolvable here (adapter resolves it) -> never
    breaches in the pure path."""
    if isinstance(due, dict):
        return False
    try:
        return now >= due
    except TypeError:  # pragma: no cover - defensive: incomparable types
        return False


# ---------------------------------------------------------------------------
# Dashboard aggregation (pure)
# ---------------------------------------------------------------------------
def dashboard_aggregate(
    process: ProcessView, runs: list[dict[str, Any]]
) -> dict[str, Any]:
    """Pure aggregation over run dicts -> per-EDGE flow metrics + throughput.

    An edge is a (rule_key, from_column, to_column) dependency derived from the
    rules (START for a row trigger). Per edge: pending / satisfied / breached
    counts (over the matching expectations across runs, keyed by
    (rule_key, expected_column)) and the average open->satisfy duration. No repo
    access — table-driven-testable."""
    ordered = _ordered_rules(process)
    edges = build_edges(
        [
            {
                "rule_key": _rule_key(r, i),
                "trigger_kind": getattr(r, "trigger_kind", None),
                "trigger_columns": _trigger_columns(r),
                "expected_columns": list(getattr(r, "expected_columns", None) or []),
            }
            for i, r in enumerate(ordered)
        ]
    )
    within_by_rule = {_rule_key(r, i): int(getattr(r, "within_seconds", 0) or 0)
                      for i, r in enumerate(ordered)}
    # rule_key -> join mode ('all' | 'any') so the FE can group AND-join edges.
    join_by_rule = {_rule_key(r, i): _trigger_join(r) if getattr(r, "trigger_kind", None) == "column" else "any"
                    for i, r in enumerate(ordered)}

    total_active = sum(1 for r in runs if r.get("status") == "active")
    total_completed = sum(1 for r in runs if r.get("status") == "completed")

    edge_out: list[dict[str, Any]] = []
    for edge in edges:
        pending = satisfied = breached = 0
        durations: list[float] = []
        for r in runs:
            for e in r.get("expectations") or []:
                if e.get("rule_key") != edge.rule_key or e.get("expected_column") != edge.to:
                    continue
                if e.get("breached"):
                    breached += 1
                elif e.get("satisfied_at") is not None:
                    satisfied += 1
                    d = _duration_seconds(e.get("opened_at"), e.get("satisfied_at"))
                    if d is not None:
                        durations.append(d)
                else:
                    pending += 1
        avg = (sum(durations) / len(durations)) if durations else None
        edge_out.append(
            {
                "rule_key": edge.rule_key,
                "from_kind": edge.from_kind,
                "from_column": None if edge.from_node == START else edge.from_node,
                "to_column": edge.to,
                "join": join_by_rule.get(edge.rule_key, "any"),
                "within_seconds": within_by_rule.get(edge.rule_key, 0),
                "pending_count": pending,
                "satisfied_count": satisfied,
                "breached_count": breached,
                "avg_open_to_satisfy_seconds": avg,
            }
        )

    return {
        "edges": edge_out,
        "total_active": total_active,
        "total_completed": total_completed,
        "throughput": total_completed,
    }


def _duration_seconds(opened: Any, satisfied: Any) -> Optional[float]:
    if isinstance(opened, (int, float)) and isinstance(satisfied, (int, float)):
        return float(satisfied - opened)
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
