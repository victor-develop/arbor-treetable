# Process DAG rule model + Visual canvas + Notification webhooks — merged implementation plan

Lead-architect merge of three area designs into ONE parallelizable, tests-first plan.
All three build on top of the SAME closed 11 EVENT_TYPES, the ONE executor + ONE ACL
resolver, and the EXISTING webhook/notification/dispatch lanes. The linear stage
process shipped THIS session and is NOT in production, so it is REPLACED wholesale
(no data migration).

## The three areas (summarized)

- **Area 1 — Flow DAG rule model + runtime.** Replace the linear ordered-stage machine
  with a RULE/DAG model. A process is a SET of rules on a sheet; each rule is
  "On <trigger>: expect (colA [and colB...]) filled within <within_seconds>",
  trigger in {row-created/updated, column-created/updated(X)}. Rules compose into a DAG
  because a column EXPECTED by rule R can be the TRIGGER column of rule R'. Runtime is a
  THIRD pure consumer off the SAME Tree Event stream (no new EventType): NODE_CREATED
  fires row rules + treats already-filled columns as implicit column triggers;
  NODE_VALUE_UPDATED(colX) satisfies pending expectations on colX and fires
  column(colX) rules; an SLA sweep breaches overdue-unmet expectations. Per-row tracking
  is one Expectation per (run, rule, expected_column).

- **Area 2 — Visual DAG canvas editor.** Replace the linear ProcessConfigPanel stage
  list with a lightweight, dependency-free SVG/DOM canvas: a fixed START node + one node
  per participating column; an edge trigger->expected carries the within-duration. Pure,
  framework-free graph helpers (buildGraph/layout/wouldCreateCycle/reachableFromStart/
  validate) mirror a server-side validate_rules so cycles/self-loops/dup-edges are
  rejected on BOTH sides. Recommends a bespoke canvas over @xyflow/react.

- **Area 3 — Notification webhooks.** Reuse the ENTIRE existing webhook delivery engine
  (dispatcher, matcher, serializer, HMAC, backoff, stores, transport, retry runner)
  unchanged and add three layers: (1) a sheet-admin-gated registration surface (non-
  capability whitelisted shims, extended Webhook Endpoint doctype); (2) a
  NOTIFICATION-driven fan-out seam so comment/process/sla/CR notifications (which never
  become Tree Events) reach webhooks; (3) subscribe(delivery='webhook') routed through
  the same seam. SSRF-guarded, admin-only, not agent-exposed.

## Reconciliation of Area 1 vs Area 2 (the deep merge)

Both areas rewrite `arbor/core/process.py`, `ports.py`, `registry.py`, `handlers.py`,
`testing.py`, `repository.py`, the same doctypes, `api.py`, and `frontend/src/api.ts`.
They MUST NOT be two workstreams touching those files in parallel. The merge:

- **Persisted + runtime truth = Area 1's rule model** (richer superset). A rule carries
  `trigger_kind`, `trigger_column`, `trigger_op`, `expected_columns[]` (an "and" set
  sharing ONE window), `within_seconds`, `notify_on_expect`, `label`. One Expectation
  row per (run, rule_ref, expected_column).
- **The canvas is a VIEW/PRODUCER over that same model.** An "and" rule with N expected
  columns renders as N edges from the same trigger that share a group/rule_ref; a plain
  single-expected rule renders as one edge. `defineProcess(rules[])` is the one payload
  both the canvas and the LLM emit. The canvas's `graph.ts` derives edges from
  `rules[]` and packs edited edges back into `rules[]` (grouping edges that share a
  trigger + window into an "and" rule when the user opts in; otherwise one edge = one
  single-expected rule — the simplest default).
- **DAG validation is ONE algorithm in two implementations.** A pure server helper
  `validate_rules(rules)` (in a new `arbor/core/process_graph.py`, imported by
  `process.py` + `handlers.py`) and a pure client `graph.ts` mirror it exactly
  (self-loop, duplicate-edge, cycle via topo, reachability-from-START warning). The
  server is the authority (ValidationError 400); the client blocks before the round-trip.
- **START node = row trigger.** The canvas's fixed START node maps to a
  `trigger_kind='row'` rule; edges out of a column node map to `trigger_kind='column'`
  rules with that `trigger_column`.
- Because both areas rewrite the SAME python core, the **rule model + runtime + server
  validation is ONE workstream (WS-A1)** and the **canvas + graph.ts is a SEPARATE
  frontend workstream (WS-A2)** that depends only on the finalized `rules[]` API shape.
  The shared API-shape contract (types in `api.ts`, get_process/dashboard shapes) is
  pinned in a FOUNDATION workstream so A1 and A2 don't both edit `api.ts`.

## Canvas decision

**Build a bespoke, dependency-free SVG/DOM canvas — NOT @xyflow/react.** The repo is a
zero-runtime-dependency React 19 + TS app (only react/react-dom in dependencies), fully
CSS-variable-token driven. @xyflow/react (~50KB gz + its own CSS/theming that fights the
design tokens) is disproportionate for a graph that is tiny (START + a handful of column
nodes). The interactions we need (add node, drag edge, edit a duration chip, delete,
reject cycle) are ~250 lines of SVG + pointer handlers PLUS pure graph/layout/validation
helpers we WANT unit-tested in isolation. A bespoke canvas keeps the bundle lean, keeps
the repo OSS-clean (no new vendor strings), and makes layout + DAG validation pure
functions with near-total unit coverage. Reconsider @xyflow/react ONLY if product later
needs large graphs (dozens of columns), pan/zoom/minimap, or rich node UIs — it is MIT/
OSS-clean, so the door stays open.

## Replacement summary (linear-stage process: deleted / replaced / kept)

DELETED (dropped outright; new+unused this session, so no migration):
- Doctypes `Arbor Process Stage` and `Arbor Process Run Stage` (dirs removed).
- `arbor_process.json` fields `stages` (Table) and `start_trigger`.
- `arbor_process_run.json` field `current_stage_idx`.
- `process.py` stage-cursor internals: `_first_empty_pos`, `_build_run_stages`,
  `_ordered_stages`, `_pos_of_idx`, `_sla_for_idx`, `_notify_stage_enter`,
  `_maybe_advance`, `_start_run` stage logic, `_stage_in_run`.
- Frontend linear stage editor body of `ProcessConfigPanel.tsx`; api.ts `ProcessStage`,
  `ProcessStageInput`, `ProcessDashboardStage`, `ProcessDef.stages`.

REPLACED (rewritten around rules/expectations, same public seam):
- `process.py` `on_event`/`sla_sweep`/`dashboard_aggregate` — SAME signatures (so the
  ProcessDispatcher binding is untouched) but rewritten as a rule/DAG evaluator over
  Expectations. NEW pure `process_graph.py` (validate_rules/cycle/reachability).
- `ports.py` `ProcessStageView` -> `ProcessRuleView`; `ProcessView.stages` -> `rules`;
  run-ledger dict shape `stages` -> `expectations`. `row_scope` KEPT.
- `registry.py` `_S_DEFINE_PROCESS` schema `stages` -> `rules`. Cap IDs unchanged.
- `handlers.py` `define_process_handler` builds rules + calls validate_rules;
  `enable_process_handler` backfills via the new start path; `start_process_run_handler`
  delegation unchanged.
- `testing.py` InMemoryRepository `_ProcessStage` -> `_ProcessRule`; run store
  `stages` -> `expectations`; adapt upsert_process / list_in_scope_nodes /
  create_process_run / list_active_runs_with_due.
- `repository.py` `_ProcessStageView` -> `_ProcessRuleView`; `_run_stage_row` ->
  `_run_expectation_row`; upsert_process writes rules; child field run_stages ->
  expectations.
- `api.py` `get_process` / `process_dashboard` / `list_process_runs` shapes
  (stages -> rules / edges / expectations); `define_process` shim passes rules.
- `ProcessConfigPanel.tsx` -> DAG canvas host; `ProcessDashboard.tsx` -> edge metrics;
  `App.tsx` per-row badge current-stage -> pending/breached count.

KEPT UNCHANGED (explicitly not touched):
- The closed 11 EVENT_TYPES; the ONE executor + ONE ACL resolver; `acl._PROCESS_META_CAPS`
  structural-owner gate; the 4 capability IDs + registry count.
- The ProcessDispatcher binding shell / FrappeProcessNotifier / FrappeProcessClock /
  `on_tree_event_insert` 3-consumer fan-out / `run_process_sla_sweep` scheduler — the
  binding calls the same 3 pure entrypoints whose signatures are preserved.
- The ENTIRE webhook lane and the notification/subscription/inbox lane (Area 3 only
  EXTENDS these additively).
- `is_filled` (unchanged empty-check; defaults count as filled) and `default_due_at`.

## Workstream table

| ID | Title | Area | parallelGroup | dependsOn |
|----|-------|------|---------------|-----------|
| WS-F1 | Doctype + port + API-shape foundation | shared | 0 | — |
| WS-A1 | Rule/DAG runtime + server validation | 1 | 1 | WS-F1 |
| WS-A2 | Visual DAG canvas + pure graph.ts | 2 | 1 | WS-F1 |
| WS-A3a | Webhook doctype + serializer/store extension | 3 | 1 | WS-F1 |
| WS-B1 | Adapter + api shims for rules/dashboard/runs | 1 | 2 | WS-A1 |
| WS-B2 | Dashboard + App badge frontend (rules/edges) | 1/2 | 2 | WS-A2 |
| WS-A3b | Notification-webhook fan-out seam + retry reuse | 3 | 2 | WS-A3a |
| WS-A3c | Webhook registration shims + SSRF + WebhookPanel FE | 3 | 2 | WS-A3a |
| WS-C1 | Bench + e2e integration (process + webhooks) | all | 3 | WS-B1, WS-A3b, WS-A3c |

## Dependency waves (sequencing)

- **Wave 0:** WS-F1 alone. Pin doctypes, ports, and the api.ts/get_process shapes so no
  later workstream re-edits the shared contract files.
- **Wave 1 (parallel):** WS-A1 (pure python rule engine + process_graph.py),
  WS-A2 (frontend canvas + graph.ts), WS-A3a (webhook doctype/serializer/store fields).
  Disjoint file sets; all depend only on WS-F1.
- **Wave 2 (parallel):** WS-B1 (python adapter + api shims), WS-B2 (dashboard/App FE),
  WS-A3b (fan-out seam), WS-A3c (registration shims + panel). Disjoint.
- **Wave 3:** WS-C1 bench/e2e that exercises the wired site end to end.

## Cross-cutting risks

1. Two areas rewriting the SAME python core — resolved by making the rule model ONE
   workstream (WS-A1) and the canvas a separate FE workstream (WS-A2) over a pinned
   contract (WS-F1). Never let A1 and A2 both edit process.py / ports.py / api.ts.
2. DAG cycle/self-loop MUST be rejected on BOTH client (graph.ts) and server
   (validate_rules) or the LLM path persists a deadlocking rule set. Bench asserts a
   cyclic defineProcess returns 400.
3. Prefilled-at-creation cascade parity: reproducing "defaults count as filled + auto-
   advance" needs, at NODE_CREATED, firing already-filled column rules AND immediately
   satisfying prefilled expected columns AND cascading downstream — guarded by a per-
   event visited-set and an (rule_ref, expected_column) existence guard.
4. Idempotency now spans MULTIPLE expectations per event; the (rule_ref, expected_column)
   existence guard + notified_owner + satisfied_at must all hold or replays double-notify.
5. Label redaction: get_process/dashboard and the canvas must render a generic
   placeholder for columns the viewer cannot read; never leak the field key or a value.
6. Notification-birth call-site coverage (Area 3): comment/process/sla/CR notifications
   are inserted at 3+ direct sites; funnel ALL through one fan_out_notification helper or
   a bench per source will catch a missed site.
7. SSRF on webhook registration: deny-by-default loopback/link-local/private-range/non-
   http(s) validator on register+update+rotate; secret returned only once, never in list.
8. OSS-clean guard: re-run tests/auth/test_auth_seam.py after canvas + webhook panel land.

## Test philosophy

Near-total path coverage, bench-free where possible:
- The pure rule/DAG evaluator + SLA math are exhaustively unit-tested over
  InMemoryRepository + a recording notify: row/column triggers, "and" sets, DAG chaining,
  prefilled cascade (both directions), trigger_op matrix, out-of-scope, idempotency/
  replay, live owner re-resolution, cycle guard, completion/quiescence matrix; SLA
  due_at math + within=0 + idempotent sweep + breach-notify-once.
- Canvas graph.ts helpers unit-tested exhaustively (cycle/reachability/layout
  determinism/hit-test math); ProcessCanvas component-tested (add node, draw edge fires
  onDefine, cycle/self-loop rejected with aria-live + no onDefine, edit chip, delete,
  keyboard/list-fallback a11y).
- Dispatch + webhook delivery proven in-process over in-memory doubles (fan-out source
  matching, per-(endpoint,event_id) idempotency, HMAC signing, backoff/exhaustion,
  deleted-endpoint cancel, subscribe(webhook)).
- Adversarial cases first-class: ACL structural-owner-else-CR for the 4 caps + sheet-
  admin-else-403 for webhook shims; DAG cycle/self-loop rejection both sides; SSRF
  deny-list.
- Bench (@pytest.mark.bench) proves the wired site: define(rules)+enable, fill columns in
  DAG order, assert Run + Expectation + Notification rows + dashboard edge aggregate +
  SLA sweep flips breached + inbox cross-sheet; a comment/stage/SLA/CR fans out a signed
  Webhook Delivery received + HMAC-verified by LocalHTTPReceiver.
- Registry EXPECTED_IDS + count unchanged (only defineProcess schema assertion updates);
  webhook mgmt methods asserted whitelisted-but-NOT registry capabilities.
