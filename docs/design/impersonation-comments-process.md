# Master Implementation Plan — Impersonation + Per-Cell Comments + Process/SLA/Inbox

Lead-architect merge of three area designs into ONE build plan optimized for maximal
sub-agent parallelism and minimal file conflicts. Every deliverable is tests-first, every
workstream owns a disjoint file set, and shared primitives are built before their consumers.

Standing constraints preserved throughout:
- PUBLIC repo (`victor-develop/arbor-treetable`) stays OSS-clean — zero AfterShip/automizely/SDK
  strings (guarded by `tests/auth/test_auth_seam.py`). All three areas are OSS-public; only the
  SSO wrapper stays in the private overlay (`arbor-sso-aftership`) and needs no change.
- The closed 11-`EventType` set (`arbor/core/types.py::EVENT_TYPES`) is NOT extended by any area.
- The ONE executor + ONE ACL resolver remain the single sources of truth. New registry
  capabilities are registered in the registry AND `hooks.override_whitelisted_methods` in the
  same change, with parity/registry tests updated in lockstep.

---

## Area summaries

### Area 1 — Password auth + traceable "act as" impersonation
The auth seam already ships (`arbor/auth/` AuthProvider Protocol + Local/OIDC providers). Remaining
public work: a provider-agnostic login screen + whoami-driven auth gate in the frontend shell.
The core is **traceable impersonation as a request-scoped overlay decoupled from authentication**:
never `frappe.set_user()`; the real Frappe session stays authoritative; `_actor()` reads a
server-persisted `Arbor Impersonation Session` and builds `Actor(user=<effective>, real_user=<real>,
impersonated_as=<effective>)`. ACL runs against the EFFECTIVE user; both identities travel through
the ONE executor into every Tree Event and Change Request. Admin authority is computed from the REAL
user BEFORE the overlay is applied; loss of admin mid-session force-ends the overlay (fail-safe).
Two new admin-only, non-LLM capabilities `beginImpersonation`/`endImpersonation` (emit no Tree Event —
the session row is the record).

### Area 2 — Per-cell comments via a right-side drawer
A NON-capability, cell-keyed collaboration feature mirroring the shipped `Arbor Cell Draft` precedent.
New `Arbor Cell Comment` DocType keyed by `(sheet, node, column)` with self-referential threading,
@mentions, resolve/reopen. Thin whitelisted shims (add/list/resolve/delete) — NOT registry
capabilities, so parity/registry stay unchanged. Governance reuses the ONE ACL resolver:
`can_read_column` gates read/post; `resolve_column_approvers` gates resolve; delete is author-or-approver.
Comments do NOT emit Tree Events. Notifications reuse the EXISTING Notification DocType via a direct
create path (source discriminator + nullable `tree_event` + `comment` link). Snapshot gains a
read-ACL-filtered per-cell comment summary.

### Area 3 — Process/SLA + Kanban dashboard + cross-sheet Inbox
A "process" is a per-sheet ordered list of column stages. Stage advancement is DERIVED from the
existing Tree Event stream by a new dispatch-lane consumer (NODE_CREATED starts a run; a
NODE_VALUE_UPDATED on the current stage column advances it) — no new EventType. Per-transition SLA via
a scheduler sweep. `defineProcess`/`enableProcess`/`disableProcess`/`startProcessRun` are governed
registry capabilities (Axis.META, structural-owner gated, LLM-exposed). Pure stage machine lives in
`arbor/core/process.py`. Read shims: `get_process`, `process_dashboard`, `list_process_runs`, and a
cross-sheet `inbox()`. Two new React pages (dashboard + inbox) + a config panel.

---

## Shared foundations (build FIRST — Wave 0)

These primitives are observed or extended by multiple areas; building them first avoids serialized
rework and file contention.

1. **Actor trace fields on the frozen core `Actor`** (`arbor/core/types.py`): `real_user`,
   `impersonated_as`, `is_impersonated` property. Consumed by the executor/CR stamping (Area 1) and
   read by any code that renders "on behalf of". Additive, keyword-constructed everywhere — backward
   compatible.
2. **Executor + Change Request trace stamping** (`arbor/core/executor.py`, `arbor/core/change_request.py`):
   `_run_and_emit`/`_suggest`/`_suggest_batch` stamp `real_user`/`impersonated_as` into TreeEvent and
   `real_requester` into the CR dict. This is the join point every governed mutation flows through, so
   Areas 2 and 3 (which observe events/CRs) inherit correct attribution for free.
3. **Notification model generalization** (`arbor/arbor/doctype/notification/notification.json` +
   `list_notifications` in `api.py`): make `tree_event` nullable, add `source` discriminator +
   optional `comment` link. This ONE inbox is shared by comment notifications (Area 2) and process/SLA
   notifications (Area 3). Must land before both build their notification fan-out. Owned by a single
   workstream to serialize the shared-file edit.
4. **The ONE ACL resolver contracts** (`arbor/core/acl.py`): `_resolve_meta_authority` gains the process
   caps (Area 3); Areas 1 and 2 reuse existing resolvers unchanged. Only Area 3 edits acl.py.

The notification-model workstream (WS-N) is the critical shared edit: both Area 2 and Area 3 fan out
through `list_notifications`/`create_notification`, so WS-N is a dependency of WS-C-BE and WS-P-DISPATCH.

---

## Workstreams

Backend and frontend are split; within each area, file ownership is partitioned so concurrent agents
never touch the same file. `parallelGroup` = safe-to-run-concurrently cohort.

| ID | Title | Area | parallelGroup | dependsOn | Owns (primary files) |
|----|-------|------|:---:|-----------|----------------------|
| WS-ACTOR | Actor trace fields + executor/CR stamping | 1 | 1 | — | core/types.py, core/executor.py, core/change_request.py, tests/core/test_executor.py, tests/core/test_change_request*.py |
| WS-N | Notification model generalization (shared) | 1/2/3 | 1 | — | doctype/notification/notification.json |
| WS-PROC-CORE | Pure process stage machine + registry/acl/handlers/ports | 3 | 1 | — | core/process.py, core/registry.py, core/handlers.py, core/acl.py, core/ports.py, core/testing.py, tests/core/test_process.py, tests/core/test_registry.py, tests/core/test_acl_resolver.py |
| WS-IMP-DOCTYPE | Impersonation session doctype + Tree Event/CR columns | 1 | 1 | — | doctype/arbor_impersonation_session/*, doctype/tree_event/tree_event.json, doctype/change_request/change_request.json, doctype/change_request_approval/change_request_approval.json |
| WS-CMT-DOCTYPE | Arbor Cell Comment doctype | 2 | 1 | — | doctype/arbor_cell_comment/* |
| WS-PROC-DOCTYPE | Process/Stage/Run/RunStage doctypes | 3 | 1 | — | doctype/arbor_process/*, arbor_process_stage/*, arbor_process_run/*, arbor_process_run_stage/* |
| WS-IMP-BE | _actor() overlay + begin/end shims + whoami + acl_hints + repo | 1 | 2 | WS-ACTOR, WS-IMP-DOCTYPE, WS-PROC-CORE | api.py (impersonation shims + _actor + _acl_hints viewer block), auth/api.py (whoami), adapter/repository.py (impersonation CRUD + CR real_* writes), hooks.py, tests/backend/test_impersonation_bench.py, tests/api/test_rest_parity_bench.py |
| WS-CMT-BE | Comment shims + notification fan-out + snapshot summary | 2 | 2 | WS-N, WS-CMT-DOCTYPE | api.py (comment shims + list_notifications comment branch + snapshot comments summary), hooks.py, tests/backend/test_cell_comments.py, tests/core/test_cell_comments_acl.py |
| WS-PROC-DISPATCH | Dispatch-lane process consumer + SLA sweep + repo + process shims | 3 | 2 | WS-PROC-CORE, WS-PROC-DOCTYPE, WS-N | dispatch/frappe_dispatch.py, dispatch/ports.py, adapter/repository.py (process ORM), api.py (process + inbox shims), hooks.py, tests/dispatch/test_process_dispatcher.py, tests/backend/test_process_bench.py |
| WS-API-TYPES | Frontend api.ts types + client methods (all 3 areas) | 1/2/3 | 3 | WS-IMP-BE, WS-CMT-BE, WS-PROC-DISPATCH | frontend/src/api.ts, frontend/src/api.test.* |
| WS-IMP-FE | Login/whoami gate + ImpersonationBar + Activity affix | 1 | 4 | WS-API-TYPES | frontend/src/components/LoginScreen.tsx(+test), ImpersonationBar.tsx(+test), hooks/useWhoami.ts, index.tsx, ActivityPanel.tsx(+test) |
| WS-CMT-FE | CommentDrawer + cell glyph + App wiring | 2 | 4 | WS-API-TYPES | frontend/src/components/CommentDrawer.tsx(+test), cells/Cell.tsx, TreeTable.tsx, TreeRow.tsx, hooks/useSheet.ts |
| WS-PROC-FE | ProcessDashboard + InboxPage + ProcessConfigPanel + routing | 3 | 4 | WS-API-TYPES | frontend/src/components/ProcessDashboard.tsx(+test), InboxPage.tsx(+test), ProcessConfigPanel.tsx(+test), index.tsx routing |
| WS-APP-SHELL | App.tsx shell integration (all mounts) | 1/2/3 | 5 | WS-IMP-FE, WS-CMT-FE, WS-PROC-FE | frontend/src/App.tsx, App.integration.test.tsx |
| WS-DOCTYPE-SCHEMA | Doctype schema parity test updates | 1/2/3 | 5 | WS-IMP-DOCTYPE, WS-CMT-DOCTYPE, WS-PROC-DOCTYPE | tests/doctype/test_doctype_schemas.py |
| WS-E2E | End-to-end journeys | 1/2/3 | 6 | WS-APP-SHELL, WS-IMP-BE, WS-CMT-BE, WS-PROC-DISPATCH | tests/e2e/*.e2e.spec.ts |

### File-contention resolutions (why the split is safe)
- **`api.py`** is touched by WS-IMP-BE, WS-CMT-BE, WS-PROC-DISPATCH. These edit DISJOINT regions
  (impersonation shims + `_actor`/`_acl_hints`; comment shims + snapshot comment summary;
  process/inbox shims). They are in the SAME parallelGroup (2) but must land as separate,
  region-scoped patches; a merge-serialization note is in Wave 2. If strict no-overlap is required,
  run WS-IMP-BE → WS-CMT-BE → WS-PROC-DISPATCH sequentially within group 2. The `list_notifications`
  comment branch is owned solely by WS-CMT-BE; the `inbox()` cross-sheet reader is owned solely by
  WS-PROC-DISPATCH.
- **`hooks.py`** is appended by three backend workstreams (register whitelisted methods, doc_events,
  scheduler). These are additive registrations to distinct dicts; serialize the final hooks edit or
  hand hooks.py ownership to whichever group-2 workstream finishes last.
- **`adapter/repository.py`** — WS-IMP-BE adds impersonation-session CRUD + CR real_* writes;
  WS-PROC-DISPATCH adds process ORM methods. Distinct method blocks; land as separate patches.
- **`core/registry.py` + `core/acl.py`** — ONLY WS-PROC-CORE edits these (process caps). Area 1's
  `beginImpersonation`/`endImpersonation` registration is ALSO in registry.py; to avoid a second
  editor, WS-PROC-CORE owns registry.py and adds BOTH sets of capabilities (impersonation + process),
  with the executor gates for impersonation implemented in WS-ACTOR (executor.py). This keeps
  registry.py single-owner.
- **`index.tsx`** — WS-IMP-FE adds the auth gate; WS-PROC-FE adds `?page=inbox`/`?dashboard=`
  routing. Distinct branches; serialize within group 4 or hand to the last finisher.

---

## Sequencing (wave by wave)

- **Wave 0 / Group 1 (pure + schema, fully parallel, 6 agents):** WS-ACTOR, WS-N, WS-PROC-CORE,
  WS-IMP-DOCTYPE, WS-CMT-DOCTYPE, WS-PROC-DOCTYPE. All framework-free logic and JSON schemas with
  no cross-file deps. Exhaustive unit tests land here (Actor truth table, executor stamping, process
  stage machine, SLA math, dashboard aggregation, registry/acl parity).
- **Wave 1 / Group 2 (Frappe adapters + shims, 3 agents, api.py-serialized):** WS-IMP-BE, WS-CMT-BE,
  WS-PROC-DISPATCH. Each depends on its group-1 primitives (+ WS-N for the two notification consumers).
  Bench tests land here. Serialize the shared `api.py`/`hooks.py`/`repository.py` merges.
- **Wave 2 / Group 3 (frontend contract, 1 agent):** WS-API-TYPES — single owner of `api.ts`, adds
  all types + client methods once the three backend envelopes are stable.
- **Wave 3 / Group 4 (frontend components, 3 agents):** WS-IMP-FE, WS-CMT-FE, WS-PROC-FE. Disjoint
  component files; `index.tsx` serialized within the group.
- **Wave 4 / Group 5 (integration, 2 agents):** WS-APP-SHELL (all shell mounts + integration test)
  and WS-DOCTYPE-SCHEMA (schema parity). Independent files.
- **Wave 5 / Group 6 (e2e, 1 agent):** WS-E2E — playwright journeys across all three features.

---

## Cross-cutting risks

1. **Silent `set_user` pitfall (Area 1):** if impersonation is ever "simplified" to
   `frappe.set_user(impersonated)`, the real-user trace is destroyed. Locked in by a bench test
   asserting `frappe.session.user == real_user` throughout an impersonated action.
2. **Admin-revocation race (Area 1):** an impersonation session for a user who lost admin must not grant
   lingering foreign identity. `_actor()` recomputes real-admin every request and force-ends the overlay
   if lost; covered by a dedicated bench test.
3. **Shared notification model (Areas 2+3):** `list_notifications` currently keys on `tree_event`;
   comment/process rows may have `tree_event=NULL`, so the loop must branch on `source` to resolve the
   sheet. WS-N owns the schema; WS-CMT-BE owns the comment branch; WS-PROC-DISPATCH owns process rows.
   Accountability aggregates (`requires_ack` math) must exclude FYI comment rows — asserted in bench.
4. **Dispatch-lane idempotency (Area 3):** stage advance off NODE_VALUE_UPDATED must not double-count
   (per-`(tree_event, run)` guard + `filled_at`/`notified_owner` flags), or owners get spammed.
5. **No-new-EventType invariant (all):** all three areas deliberately avoid extending the closed
   11-type set; comments/process advance are invisible to `list_activity`/webhooks by design —
   documented scope decision. `EVENT_TYPES` unchanged is asserted in the schema test.
6. **Parity/registry drift (Areas 1+3):** the impersonation + process capabilities require updating
   `tests/core/test_registry.py` EXPECTED_IDS and the parity manifest in the SAME change (WS-PROC-CORE
   owns registry.py + the registry test to keep them atomic).
7. **OSS-clean regression (all):** LoginScreen/ImpersonationBar and every new file must carry zero
   vendor strings; re-run `tests/auth/test_auth_seam.py` after the frontend waves.
8. **Right-side surface stacking (Area 2):** CommentDrawer vs agent FAB vs Proposed overlay — define
   z-index order (agent popup > drawer > table) and make the drawer inert in Proposed preview.
9. **Frozen-dataclass construction sites (Area 1):** all `Actor(...)` construction is keyword-based;
   new fields default to `None` so additions are backward compatible — grep-verify no positional builds.

---

## Test philosophy — near-total path coverage

- **Pure libraries exhaustively unit-tested (bench-free, `tests/core`):** the framework-free logic
  is where correctness lives, so it gets table-driven exhaustive coverage against in-memory doubles
  (`InMemoryRepository`, `RecordingEventSink`). Area 1: `Actor.is_impersonated` truth table, executor
  stamping on authorized/suggest/batch paths, admin-gate for begin/end. Area 3: the entire stage
  machine (start/advance/out-of-order guard/terminal/completion/idempotency/live-owner-re-resolution),
  SLA math, dashboard aggregation. Area 2: reuse-of-resolver assertions + @mention parsing/filtering.
- **Bench tests for adapters (`@pytest.mark.bench`, `tests/backend`+`tests/api`):** everything that
  touches Frappe (the `_actor()` overlay, doctype writes, notification fan-out, dispatch consumer,
  SLA sweep, REST parity) is verified on a live site. Bench asserts the DB-level trace (both TreeEvent
  columns populated, CR `real_requester`, session force-end on admin loss).
- **Parity harness stays green unchanged for non-capabilities (Area 2):** an explicit assertion that
  comment shims are whitelisted-but-NOT in `registry.all_capabilities()` proves the closed set held.
- **Component + integration for UI (vitest):** each new component tested in isolation (LoginScreen
  submit/error, ImpersonationBar picker-vs-banner off `viewer.impersonating`, CommentDrawer
  thread/compose/resolve/delete gating, ProcessDashboard counts/drill-down, InboxPage cross-sheet+ack).
  `App.integration.test.tsx` verifies coexistence (drawer + agent FAB stacking, inert-in-Proposed).
  `tsc` must pass with the new `api.ts` types.
- **Adversarial / negative paths:** every ACL gate tested from the DENIED side (non-admin begin →
  403; suggest-only user resolve → 403; unreadable-column comment/list → 403 and no mention
  notification; agent-coerced beginImpersonation → AuthorizationError; out-of-order stage fill → no
  advance). Fail-safe and idempotency paths are first-class tests, not afterthoughts.
- **E2E as the visible-trace proof (playwright):** admin logs in with password, "acts as" a column
  owner, edits, stops — Activity feed shows the action attributed to the owner "via" the admin,
  proving the end-to-end trace is legible in the UI, not just the DB.
