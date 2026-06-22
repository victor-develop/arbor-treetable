# Test-Case Catalog — Change Request & Approval Surface

> **Status:** Test-first catalog (written before implementation). Built against the
> canonical spec: [`ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §4–§5, §11,
> [`CAPABILITIES.md`](../docs/CAPABILITIES.md), [`PERMISSIONS.md`](../docs/PERMISSIONS.md),
> [`DATA-MODEL.md`](../docs/DATA-MODEL.md).
>
> **Surface under test:** the `suggestChange → proposed → approve/reject/withdraw` state
> machine; that approval replays the deferred capability **as the resolved approver** and
> emits the right Tree Event; that an owner acting directly skips the CR path (unless
> `owners_must_use_change_requests` is on); that the agent acting without authority
> produces a CR identical to a human non-owner's; idempotency and double-decision guards;
> re-resolution at decision time; surface parity.

---

## Shared canonical fixtures (referenced by ID, never redefined per-test)

These are assumed to exist as shared fixtures (see `PERMISSIONS.md` §2). Tests reference
them by name; **do not** invent bespoke worlds.

- **Personas / Users:** `A` (root structural owner), `B` (owner `col:name`, `col:notes`;
  editor on `col:status`), `C` (owner `col:status`, `col:budget`), `D` (delegated owner of
  branch `P2` via active Branch Grant), `E` & `F` (suggest-only; no grants, no columns),
  `G` (sensitive subscriber, `requires_ack=true`), `EXT` (external system: API consumer +
  Webhook Endpoint).
- **Agent user:** `AGENT` — the Re-Act agent's own Frappe User (`actor_type=agent`), holds
  **no** grants and owns **no** columns unless a test says otherwise.
- **Sample sheet `S`** with `structural_owner = A`, `status = active`,
  `settings = {}` (so `owners_must_use_change_requests` defaults false unless a test sets
  it). Tree:

  ```
  root R            (struct authority: A)
  ├── P1            (A)
  │   └── X         (A)
  └── P2  ←──────── Branch Grant g_P2 (grantee=D, scope=structure, active)
      ├── Y         (D, inherited)
      └── Z         (D, inherited)
  ```

- **Columns:** `col:name` (is_label, owner `B`), `col:status` (owner `C`, editors `[B]`),
  `col:budget` (owner `C`), `col:notes` (owner `B`).
- **Helper assertions referenced below:**
  - `last_event(S)` — the most recent Tree Event row for sheet `S`.
  - `events_of(cr)` — Tree Events whose `change_request == cr.name`, in order.
  - `cr_fields(cr)` — the Change Request row.
  - `cell(node, col)` — the Tree Node Value `(node, column)` (value + version).
  - `ack_report(event)` — `(N_notified, M_acked)` aggregate (ARCHITECTURE §6).

**Capability IDs in scope:** `suggestChange`, `approveChange`, `rejectChange`,
`withdrawChange`, plus the deferred mutating capabilities they replay (`updateCell`,
`addNode`, `moveNode`, `deleteNode`, `addColumn`, `updateColumn`, `deleteColumn`).
**Event types in scope:** `CHANGE_PROPOSED`, `CHANGE_APPROVED`, `CHANGE_REJECTED`, and the
real mutation events replayed on approval (`NODE_VALUE_UPDATED`, `NODE_CREATED`,
`NODE_MOVED`, `NODE_DELETED`, `COLUMN_CONFIG_UPDATED`).

---

## A. Proposal creation (the two ways a CR is born)

### CHANGE_REQUEST_LIFECYCLE-001
- **Title:** Unauthorized direct mutation auto-creates a CR (implicit suggest via execute_action)
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; actor `E`; column `col:status` (approvers `{C, B}`).
- **Given** `E` is neither owner nor editor of `col:status`.
- **When** `E` calls `execute_action("updateCell", {sheet:S, node:X, column:col:status, value:"done"})`.
- **Then** no Tree Node Value is mutated (`cell(X, col:status)` unchanged, version unchanged); a Change Request is created with `target_kind=cell-value`, `operation=update`, `payload` == the original params, `requester=E`, `resolved_approver=C` (column owner), `status=proposed`; exactly one Tree Event `CHANGE_PROPOSED` is emitted with `change_request=cr.name`, `actor=E`, `actor_type=human`; the call `Outcome.kind == "suggested"`.
- **Covers:** caps `updateCell`→`suggestChange` (implicit branch 4b); event `CHANGE_PROPOSED`.

### CHANGE_REQUEST_LIFECYCLE-002
- **Title:** Explicit suggestChange always creates a CR even when caller could not mutate
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; actor `F`.
- **Given** `F` intends to suggest without first attempting a mutation.
- **When** `F` calls `execute_action("suggestChange", {sheet:S, target_kind:"node-structure", operation:"add", payload:{sheet:S, parent:P2, values:{name:"new"}}})`.
- **Then** a CR is created (`requester=F`, `target_kind=node-structure`, `operation=add`, `status=proposed`, `resolved_approver=D` — resolved from the payload's intended parent `P2`); one `CHANGE_PROPOSED` event emitted; no Tree Node created.
- **Covers:** cap `suggestChange`; event `CHANGE_PROPOSED`.

### CHANGE_REQUEST_LIFECYCLE-003
- **Title:** suggestChange is always allowed even for an actor who DOES have authority
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; actor `C` (owner of `col:budget`).
- **Given** `C` could update `col:budget` directly, but explicitly calls `suggestChange`.
- **When** `C` calls `execute_action("suggestChange", {sheet:S, target_kind:"cell-value", operation:"update", payload:{sheet:S, node:Y, column:col:budget, value:42}})`.
- **Then** a CR is created (not a direct mutation), `requester=C`, `resolved_approver=C` (the owner is its own approver); `status=proposed`; `CHANGE_PROPOSED` emitted; `cell(Y, col:budget)` unchanged until approval.
- **Covers:** cap `suggestChange`; event `CHANGE_PROPOSED`. (Asserts `suggestChange` never auto-executes regardless of authority.)

### CHANGE_REQUEST_LIFECYCLE-004
- **Title:** CR payload is a faithful, replayable copy of the original capability params
- **Level:** unit
- **Preconditions / fixtures:** sheet `S`; actor `E`.
- **Given** `E` attempts `updateCell(Y, col:budget, value:[1,2,3])` (a non-trivial typed value).
- **When** the CR is created.
- **Then** `cr.payload` equals the exact params dict (including `sheet`, `node`, `column`, `value` with array preserved); replaying `cap.handler(cr.payload, ...)` requires no field reconstruction.
- **Covers:** cap `updateCell`/`suggestChange` (payload fidelity). No event assertion (unit-level on stored row).

### CHANGE_REQUEST_LIFECYCLE-005
- **Title:** Schema validation failure prevents CR creation
- **Level:** unit
- **Preconditions / fixtures:** sheet `S`; actor `E`.
- **Given** `E` calls `updateCell` missing the required `value` field.
- **When** `execute_action` runs schema validation (step 2, before ACL).
- **Then** a validation error is raised; **no** Change Request row is created and **no** `CHANGE_PROPOSED` event is emitted (validation precedes the authorized/suggested split).
- **Covers:** cap `updateCell` (params_schema gate). Asserts no event/CR leak on invalid input.

---

## B. Happy-path approval — value, structure, schema (replay correctness)

### CHANGE_REQUEST_LIFECYCLE-010
- **Title:** Approving a cell-value CR replays updateCell AS the approver and emits NODE_VALUE_UPDATED then CHANGE_APPROVED
- **Level:** integration
- **Preconditions / fixtures:** CR `cr1` from -001 (`E` proposed `updateCell(X, col:status, "done")`, `resolved_approver=C`).
- **Given** `cr1.status == proposed`; `cell(X, col:status)` has prior value `v0`, version `n`.
- **When** `C` calls `execute_action("approveChange", {change_request:cr1})`.
- **Then** `cap.handler` for `updateCell` runs with `actor = cr1.resolved_approver (C)`; `cell(X, col:status)` becomes `"done"` with `version == n+1`; exactly two Tree Events are emitted in order: (1) `NODE_VALUE_UPDATED` with payload `{node:X, column:col:status, old_value:v0, new_value:"done", version:n+1}` and `actor=C`, then (2) `CHANGE_APPROVED` with `change_request=cr1`; `cr1.status=approved`, `decided_by=C`, `decided_at` set, `resulting_event` links the `NODE_VALUE_UPDATED` event (not the `CHANGE_APPROVED` one).
- **Covers:** caps `approveChange` + replayed `updateCell`; events `NODE_VALUE_UPDATED`, `CHANGE_APPROVED`.

### CHANGE_REQUEST_LIFECYCLE-011
- **Title:** The mutation event records the approver as actor, not the original requester
- **Level:** integration
- **Preconditions / fixtures:** `cr1` (requester `E`, approver `C`) approved as in -010.
- **Given** the replay runs as `C`.
- **When** inspecting the `NODE_VALUE_UPDATED` event.
- **Then** `event.actor == C` and `event.actor_type == human`; the requester `E` appears only on the originating `CHANGE_PROPOSED` event and as `cr.requester`; the mutation is attributed to the authority that approved it.
- **Covers:** cap `approveChange`; event `NODE_VALUE_UPDATED` (actor attribution).

### CHANGE_REQUEST_LIFECYCLE-012
- **Title:** Approving a structural add CR replays addNode and emits NODE_CREATED
- **Level:** integration
- **Preconditions / fixtures:** CR from -002 (`F` proposed `addNode(parent=P2)`, approver `D`).
- **Given** `cr.status=proposed`.
- **When** `D` calls `approveChange(cr)`.
- **Then** a new Tree Node is created under `P2` (NestedSet `lft/rgt` consistent, inside P2's range); initial `values` from payload are written as Tree Node Value rows; events emitted: `NODE_CREATED` (actor `D`) then `CHANGE_APPROVED`; `cr.resulting_event` → the `NODE_CREATED` event; `cr.status=approved`.
- **Covers:** caps `approveChange` + `addNode`; events `NODE_CREATED`, `CHANGE_APPROVED`.

### CHANGE_REQUEST_LIFECYCLE-013
- **Title:** Approving a deleteNode CR replays deleteNode and emits NODE_DELETED
- **Level:** integration
- **Preconditions / fixtures:** `A` proposed `deleteNode(X)` via CR (approver `A` — but suppose `E` proposed it so approver = `A`). Use: `E` proposed `deleteNode(X)`, `resolved_approver=A`.
- **Given** `cr.status=proposed`; `X` exists.
- **When** `A` calls `approveChange(cr)`.
- **Then** node `X` (and per `cascade` default true, its subtree) is removed; events `NODE_DELETED` (actor `A`) then `CHANGE_APPROVED`; `resulting_event` → `NODE_DELETED`.
- **Covers:** caps `approveChange` + `deleteNode`; events `NODE_DELETED`, `CHANGE_APPROVED`.

### CHANGE_REQUEST_LIFECYCLE-014
- **Title:** Approving a column-schema CR replays the meta op and emits COLUMN_CONFIG_UPDATED
- **Level:** integration
- **Preconditions / fixtures:** `E` proposed `updateColumn(col:budget, patch={width:200})`; approvers `{C}`; `resolved_approver=C`.
- **Given** `cr.status=proposed`, `target_kind=column-schema`, `operation=update`.
- **When** `C` calls `approveChange(cr)`.
- **Then** `col:budget.width == 200`; events `COLUMN_CONFIG_UPDATED` (actor `C`) then `CHANGE_APPROVED`; `resulting_event` → `COLUMN_CONFIG_UPDATED`.
- **Covers:** caps `approveChange` + `updateColumn`; events `COLUMN_CONFIG_UPDATED`, `CHANGE_APPROVED`.

### CHANGE_REQUEST_LIFECYCLE-015
- **Title:** Approval by a column EDITOR (not the owner) is valid and replays correctly
- **Level:** integration
- **Preconditions / fixtures:** `E` proposed `updateCell(X, col:status, "blocked")`; `col:status` approvers `{C, B(editor)}`; `resolved_approver=C` (owner).
- **Given** `cr.status=proposed`.
- **When** `B` (an editor of `col:status`, **not** the `resolved_approver`) calls `approveChange(cr)`.
- **Then** approval succeeds (editor is in `resolve_column_approvers`); the replay runs as `B` (the deciding editor); `cell(X, col:status)="blocked"`; `decided_by=B`; events `NODE_VALUE_UPDATED` (actor `B`) + `CHANGE_APPROVED`.
- **Covers:** cap `approveChange` (column-editor approval path); events `NODE_VALUE_UPDATED`, `CHANGE_APPROVED`. (PERMISSIONS §4.7: "or a column editor" may approve.)

---

## C. Reject & withdraw transitions

### CHANGE_REQUEST_LIFECYCLE-020
- **Title:** Rejecting a CR sets status=rejected, emits CHANGE_REJECTED, mutates nothing
- **Level:** integration
- **Preconditions / fixtures:** `cr1` from -001 (`E`→`C`, `updateCell(X, col:status)`), `proposed`.
- **Given** `cell(X, col:status)` value `v0`, version `n`.
- **When** `C` calls `rejectChange(cr1, comment:"no")`.
- **Then** `cr1.status=rejected`, `decided_by=C`, `decided_at` set, `resulting_event` is **null**; exactly one `CHANGE_REJECTED` event emitted (`change_request=cr1`, actor `C`); `cell(X, col:status)` unchanged (value `v0`, version `n`).
- **Covers:** cap `rejectChange`; event `CHANGE_REJECTED`.

### CHANGE_REQUEST_LIFECYCLE-021
- **Title:** Requester withdraws own CR → status=withdrawn, emits CHANGE_REJECTED(status=withdrawn semantics), no mutation
- **Level:** integration
- **Preconditions / fixtures:** `cr1` (`requester=E`), `proposed`.
- **Given** `cr1.status=proposed`.
- **When** `E` (the requester) calls `withdrawChange(cr1)`.
- **Then** `cr1.status=withdrawn` (terminal); a `CHANGE_REJECTED` event is emitted carrying withdrawn semantics (per CAPABILITIES registry: `withdrawChange` emits `CHANGE_REJECTED` with status=withdrawn); no data mutation; `resulting_event` null; `decided_by` may be `E`/requester.
- **Covers:** cap `withdrawChange`; event `CHANGE_REJECTED` (withdrawn variant).

### CHANGE_REQUEST_LIFECYCLE-022
- **Title:** A non-requester cannot withdraw someone else's CR
- **Level:** integration
- **Preconditions / fixtures:** `cr1` (`requester=E`), `proposed`.
- **Given** `F` is not the requester and not the approver.
- **When** `F` calls `withdrawChange(cr1)`.
- **Then** the call is denied by ACL (`actor == cr.requester` fails); `cr1.status` stays `proposed`; no event emitted; no mutation.
- **Covers:** cap `withdrawChange` (requester-only guard). (PERMISSIONS §4.7.)

### CHANGE_REQUEST_LIFECYCLE-023
- **Title:** The approver cannot withdraw (only reject); the requester cannot reject (only withdraw)
- **Level:** integration
- **Preconditions / fixtures:** `cr1` (`requester=E`, `resolved_approver=C`), `proposed`.
- **Given** the role/transition matrix: approver→{approve,reject}, requester→{withdraw}.
- **When** (a) `C` calls `withdrawChange(cr1)`; and (b) `E` calls `rejectChange(cr1)`.
- **Then** both are denied by ACL; `cr1` remains `proposed`; no events; no mutation. (`withdrawChange` requires `actor==requester`; `rejectChange` requires `actor==resolved_approver`/editor.)
- **Covers:** caps `withdrawChange`, `rejectChange` (role separation). No event.

---

## D. Approval permission-DENIED paths

### CHANGE_REQUEST_LIFECYCLE-030
- **Title:** A non-approver cannot approve a CR (suggest-only user)
- **Level:** integration
- **Preconditions / fixtures:** `cr1` (`resolved_approver=C`, cell-value on `col:status`), `proposed`.
- **Given** `E` is neither owner nor editor of `col:status`.
- **When** `E` calls `approveChange(cr1)`.
- **Then** denied by ACL; `cr1.status` stays `proposed`; no replay; no `NODE_VALUE_UPDATED`, no `CHANGE_APPROVED`; `cell(X, col:status)` unchanged.
- **Covers:** cap `approveChange` (approver-only guard). (PERMISSIONS §3 E-row, §4.7.)

### CHANGE_REQUEST_LIFECYCLE-031
- **Title:** The requester cannot self-approve their own CR (when they are not the approver)
- **Level:** integration
- **Preconditions / fixtures:** `cr1` (`requester=E`, `resolved_approver=C`), `proposed`.
- **When** `E` calls `approveChange(cr1)`.
- **Then** denied (E ≠ C and E not a column editor); no mutation, no event; `proposed` preserved.
- **Covers:** cap `approveChange` (no requester self-approval bypass).

### CHANGE_REQUEST_LIFECYCLE-032
- **Title:** A structural approver of a DIFFERENT branch cannot approve this CR
- **Level:** integration
- **Preconditions / fixtures:** `cr` proposing `addNode(parent=P2)` → `resolved_approver=D`; `proposed`.
- **Given** `A` is root structural owner but `P2` is delegated to `D`.
- **When** `A` calls `approveChange(cr)`.
- **Then** denied (re-resolution at decision time still yields `D` because grant `g_P2` is active; `A ≠ D`); `proposed` preserved; no node created; no event. (If grant were revoked, see -053.)
- **Covers:** cap `approveChange` (delegation-scoped approver guard); re-resolution at decision time.

### CHANGE_REQUEST_LIFECYCLE-033
- **Title:** A column owner cannot approve a structural CR (axis cross-over denied)
- **Level:** integration
- **Preconditions / fixtures:** `cr` proposing `deleteNode(Z)` (Axis 1, approver `D`); `proposed`.
- **Given** `C` is a column owner but holds no structural authority over `P2`.
- **When** `C` calls `approveChange(cr)`.
- **Then** denied (approver is `D`, an Axis-1 identity; `C` is irrelevant to Axis 1); no mutation/event.
- **Covers:** cap `approveChange` (axis independence at decision time).

---

## E. Owner-direct vs. owner-self policy

### CHANGE_REQUEST_LIFECYCLE-040
- **Title:** Authorized owner acting directly skips the CR path entirely (default policy)
- **Level:** integration
- **Preconditions / fixtures:** sheet `S` with `settings.owners_must_use_change_requests` **unset/false**; actor `C` (owner `col:budget`).
- **Given** `C` is authorized for `updateCell` on `col:budget`.
- **When** `C` calls `execute_action("updateCell", {sheet:S, node:Y, column:col:budget, value:99})`.
- **Then** `Outcome.kind=="executed"`; `cell(Y, col:budget)=99`, version incremented; exactly one `NODE_VALUE_UPDATED` event; **no** Change Request row created, **no** `CHANGE_PROPOSED`/`CHANGE_APPROVED` events.
- **Covers:** cap `updateCell` (authorized branch 4a — CR skipped); event `NODE_VALUE_UPDATED`.

### CHANGE_REQUEST_LIFECYCLE-041
- **Title:** With owners_must_use_change_requests=true, an authorized owner's direct action still produces a CR (self-approver)
- **Level:** integration
- **Preconditions / fixtures:** sheet `S` with `settings.owners_must_use_change_requests = true`; actor `C` (owner `col:budget`).
- **Given** `C` is authorized.
- **When** `C` calls `updateCell(Y, col:budget, 99)`.
- **Then** `Outcome.kind=="suggested"`; a CR is created with `requester=C` **and** `resolved_approver=C` (owner becomes their own approver); `CHANGE_PROPOSED` emitted; `cell(Y, col:budget)` unchanged until `C` approves; no immediate `NODE_VALUE_UPDATED`.
- **Covers:** caps `updateCell`→`suggestChange` (owner-self policy); event `CHANGE_PROPOSED`. (PERMISSIONS §1.2, §4.8.)

### CHANGE_REQUEST_LIFECYCLE-042
- **Title:** Owner self-approves their own policy-forced CR and the mutation lands
- **Level:** integration
- **Preconditions / fixtures:** the CR `cr_self` from -041 (`requester=C`, `resolved_approver=C`), `proposed`.
- **When** `C` calls `approveChange(cr_self)`.
- **Then** approval is allowed (actor `C` == `resolved_approver` `C`); replay runs as `C`; `cell(Y, col:budget)=99`; events `NODE_VALUE_UPDATED` (actor `C`) + `CHANGE_APPROVED`; `cr_self.status=approved`. Full audit trail exists (`CHANGE_PROPOSED` → `NODE_VALUE_UPDATED` → `CHANGE_APPROVED`).
- **Covers:** caps `approveChange` + `updateCell`; events `NODE_VALUE_UPDATED`, `CHANGE_APPROVED`. (Asserts the audit-trail intent of the policy.)

### CHANGE_REQUEST_LIFECYCLE-043
- **Title:** owners_must_use_change_requests does NOT alter a non-owner's path
- **Level:** integration
- **Preconditions / fixtures:** sheet `S` with policy `true`; actor `E` (non-owner).
- **When** `E` calls `updateCell(X, col:status, "x")`.
- **Then** behavior is identical to the default-policy case: CR created, `requester=E`, `resolved_approver=C` (the real owner, not `E`); `CHANGE_PROPOSED`. The policy only changes the *owner's* path, never widens a non-owner's authority.
- **Covers:** cap `suggestChange`; event `CHANGE_PROPOSED` (policy scope boundary).

---

## F. Agent-as-actor (agent = human under ACL)

### CHANGE_REQUEST_LIFECYCLE-050
- **Title:** Agent lacking authority produces a CR identical to a human non-owner's
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; actor `AGENT` (own User, no grants, no columns).
- **Given** the agent's Re-Act loop decides to call `updateCell(Y, col:budget, 7)`.
- **When** the agent tool routes through `execute_action("updateCell", params, actor=AGENT)`.
- **Then** a CR is created with `requester=AGENT`, `resolved_approver=C`, `status=proposed`; the originating `CHANGE_PROPOSED` event has `actor=AGENT`, `actor_type=agent`; no cell mutation. The CR is structurally identical to one a human non-owner would create (same `target_kind`, `operation`, `payload`, `resolved_approver`) differing only in `requester`/`actor_type`.
- **Covers:** caps `updateCell`→`suggestChange` via agent; event `CHANGE_PROPOSED`. (ARCHITECTURE §8; PERMISSIONS §4.5.)

### CHANGE_REQUEST_LIFECYCLE-051
- **Title:** A human approves an agent-filed CR; the replay runs as the human approver, not the agent
- **Level:** integration
- **Preconditions / fixtures:** the agent CR `cr_a` from -050 (`requester=AGENT`, `resolved_approver=C`), `proposed`.
- **When** `C` calls `approveChange(cr_a)`.
- **Then** `cap.handler` replays as `C` (not `AGENT`); `cell(Y, col:budget)=7`; the `NODE_VALUE_UPDATED` event has `actor=C`, `actor_type=human`; `CHANGE_APPROVED` emitted; `cr_a.status=approved`, `decided_by=C`. The agent never gains the authority — the human's approval is the authority.
- **Covers:** caps `approveChange` + `updateCell`; events `NODE_VALUE_UPDATED`, `CHANGE_APPROVED`. (Agent cannot escalate privilege.)

### CHANGE_REQUEST_LIFECYCLE-052
- **Title:** An agent that DOES hold authority executes directly (no CR) — symmetry with humans
- **Level:** integration
- **Preconditions / fixtures:** a variant fixture where `AGENT` is the `column_owner` of a column `col:agentnotes` on `S`; policy false.
- **Given** `AGENT` is authorized for that column.
- **When** `AGENT` calls `updateCell(X, col:agentnotes, "auto")`.
- **Then** `Outcome.kind=="executed"`; cell mutates; one `NODE_VALUE_UPDATED` event with `actor=AGENT`, `actor_type=agent`; no CR. (Confirms the agent is not *forced* into CRs — it follows the same ACL split as humans.)
- **Covers:** cap `updateCell` (authorized agent branch); event `NODE_VALUE_UPDATED`.

---

## G. Re-resolution at decision time (tree/grants changed since proposal)

### CHANGE_REQUEST_LIFECYCLE-053
- **Title:** Grant revoked after proposal re-routes the structural CR to the fallback owner
- **Level:** integration
- **Preconditions / fixtures:** `cr` proposing `addNode(parent=P2)` filed while `g_P2` active → `resolved_approver=D`, `proposed`.
- **Given** after proposal, `revokeDelegation(g_P2)` runs (g_P2 now inactive).
- **When** any actor attempts a decision; the resolver recomputes the approver at decision time.
- **Then** the re-resolved structural approver for `P2` is now `A` (no active grant → fallback `structural_owner`); the CR's `resolved_approver` is updated to `A` and re-routed; `D` may **no longer** approve; `A` may. Approving as `A` replays `addNode` and emits `NODE_CREATED` + `CHANGE_APPROVED`.
- **Covers:** caps `approveChange` + `addNode`; events `NODE_CREATED`, `CHANGE_APPROVED`; re-resolution invariant. (ARCHITECTURE §5: "re-resolution at decision time if the tree/grants changed".)

### CHANGE_REQUEST_LIFECYCLE-054
- **Title:** New nearer grant after proposal re-routes to the new nearest grantee
- **Level:** integration
- **Preconditions / fixtures:** `cr` proposing `addNode(parent=Z)` filed when only `g_P2` (D) exists → `resolved_approver=D`, `proposed`.
- **Given** after proposal, a nearer grant `g_Z` (grantee `D2`, branch_root `Z`, active) is created.
- **When** the decision-time re-resolution runs for the CR.
- **Then** the nearest active grant on `Z`'s ancestor chain is now `g_Z` → `resolved_approver` recomputed to `D2`; `D` can no longer approve, `D2` can. (PERMISSIONS §4.2 nearest-grant-wins, evaluated at decision time.)
- **Covers:** cap `approveChange` (re-resolution + nearest-grant); structural authority. (Approval would emit `NODE_CREATED` + `CHANGE_APPROVED`.)

### CHANGE_REQUEST_LIFECYCLE-055
- **Title:** Column-editor set change after proposal: removed editor can no longer approve
- **Level:** integration
- **Preconditions / fixtures:** `cr` proposing `updateCell(X, col:status,...)` (approvers `{C, B(editor)}`), `proposed`.
- **Given** after proposal, `grantColumn(col:status, editors:[])` removes `B` as editor.
- **When** `B` calls `approveChange(cr)`.
- **Then** denied — at decision time `B ∉ resolve_column_approvers(col:status)` (now `{C}`); `proposed` preserved; only `C` may now approve.
- **Covers:** caps `grantColumn`, `approveChange` (decision-time column re-resolution). No mutation event on the denied attempt.

---

## H. Idempotency & double-decision guards

### CHANGE_REQUEST_LIFECYCLE-060
- **Title:** Double-approve is rejected — terminal state guard
- **Level:** integration
- **Preconditions / fixtures:** `cr1` already `approved` (via -010), `resulting_event` populated.
- **When** `C` calls `approveChange(cr1)` a second time.
- **Then** the call is rejected (state is terminal; only `proposed` is transition-eligible); the handler does **not** replay; `cell(X, col:status)` is mutated exactly once (version unchanged from the first approval); **no** second `NODE_VALUE_UPDATED` and **no** second `CHANGE_APPROVED` event; `resulting_event` is unchanged.
- **Covers:** cap `approveChange` (idempotency/terminal-state guard); ensures single `NODE_VALUE_UPDATED`.

### CHANGE_REQUEST_LIFECYCLE-061
- **Title:** Approve-after-reject is rejected
- **Level:** integration
- **Preconditions / fixtures:** `cr1` already `rejected` (via -020).
- **When** `C` calls `approveChange(cr1)`.
- **Then** rejected (terminal state); no replay; no mutation; no event; `status` stays `rejected`, `resulting_event` stays null.
- **Covers:** cap `approveChange` (terminal-state guard from `rejected`).

### CHANGE_REQUEST_LIFECYCLE-062
- **Title:** Reject-after-approve is rejected
- **Level:** integration
- **Preconditions / fixtures:** `cr1` already `approved`.
- **When** `C` calls `rejectChange(cr1)`.
- **Then** rejected (terminal); no `CHANGE_REJECTED` event; the prior approval/mutation are untouched; status stays `approved`.
- **Covers:** cap `rejectChange` (terminal-state guard from `approved`).

### CHANGE_REQUEST_LIFECYCLE-063
- **Title:** Withdraw-after-decision (approved or rejected) is rejected
- **Level:** integration
- **Preconditions / fixtures:** two CRs: `cr_app` (approved), `cr_rej` (rejected).
- **When** the requester `E` calls `withdrawChange` on each.
- **Then** both rejected (terminal); no events; statuses unchanged.
- **Covers:** cap `withdrawChange` (terminal-state guard).

### CHANGE_REQUEST_LIFECYCLE-064
- **Title:** Concurrent double-approve race resolves to exactly one mutation
- **Level:** integration
- **Preconditions / fixtures:** `cr1` `proposed`; approver `C` issues two near-simultaneous `approveChange(cr1)` calls (e.g. row-level lock / optimistic version on Change Request).
- **Given** both requests read `status=proposed`.
- **When** they commit concurrently.
- **Then** exactly one wins (transitions `proposed→approved`, replays once, emits one `NODE_VALUE_UPDATED` + one `CHANGE_APPROVED`); the other fails the terminal-state/version guard with no second mutation and no duplicate event. `cell` version incremented exactly once.
- **Covers:** cap `approveChange` (concurrency idempotency); event `NODE_VALUE_UPDATED` emitted exactly once.

### CHANGE_REQUEST_LIFECYCLE-065
- **Title:** Approve then withdraw race — withdraw loses if approve commits first (and vice versa)
- **Level:** integration
- **Preconditions / fixtures:** `cr1` `proposed`; `C` calls `approveChange`, `E` calls `withdrawChange` concurrently.
- **Given** both read `proposed`.
- **When** committed concurrently.
- **Then** the first to commit wins and sets a terminal state; the second is rejected by the terminal-state guard. Final state is either `approved` (with one mutation + `CHANGE_APPROVED`) or `withdrawn` (no mutation, one `CHANGE_REJECTED`) — never both, never a mutation plus a withdraw.
- **Covers:** caps `approveChange`, `withdrawChange` (mutual exclusion on terminal transition).

---

## I. moveNode CRs (two-ended authority; co-approver semantics)

### CHANGE_REQUEST_LIFECYCLE-070
- **Title:** moveNode across branches with authority over only one end → CR routed to dest approver with src co-approver
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; actor `A` attempts `moveNode(X → P2)`. src approver = `A`, dest approver = `D`.
- **Given** `A` owns the src end but not the dest (`P2` delegated to `D`).
- **When** `A` calls `execute_action("moveNode", {sheet:S, node:X, new_parent:P2})`.
- **Then** not authorized (must equal both src and dest approver); a CR is created `target_kind=node-structure`, `operation=move`, `resolved_approver=D` (dest), with `payload.co_approvers` including `A` (src); `CHANGE_PROPOSED` emitted; node `X` not moved.
- **Covers:** caps `moveNode`→`suggestChange`; event `CHANGE_PROPOSED`. (PERMISSIONS §3 A-row, §4.4.)

### CHANGE_REQUEST_LIFECYCLE-071
- **Title:** Approving a moveNode CR replays moveNode and emits NODE_MOVED
- **Level:** integration
- **Preconditions / fixtures:** the move CR from -070 (`resolved_approver=D`), `proposed`.
- **When** `D` (dest approver) calls `approveChange(cr)`.
- **Then** `X` is re-parented under `P2` (NestedSet `lft/rgt` recomputed; `X` now inside P2's range); events `NODE_MOVED` (actor `D`, payload includes old/new parent) + `CHANGE_APPROVED`; `resulting_event` → `NODE_MOVED`.
- **Covers:** caps `approveChange` + `moveNode`; events `NODE_MOVED`, `CHANGE_APPROVED`.

### CHANGE_REQUEST_LIFECYCLE-072
- **Title:** An actor authorized over BOTH ends moves directly (no CR)
- **Level:** integration
- **Preconditions / fixtures:** `D` attempts `moveNode(Y → Z)` — both `Y` and `Z` are inside `D`'s P2 branch (src approver `D`, dest approver `D`); policy false.
- **When** `D` calls `moveNode(Y → Z)`.
- **Then** `Outcome.kind=="executed"`; `Y` re-parented under `Z`; one `NODE_MOVED` event (actor `D`); no CR.
- **Covers:** cap `moveNode` (both-ends authorized branch); event `NODE_MOVED`. (Confirms CR is only produced when an end is unauthorized.)

---

## J. Snapshot / event-log consistency & boundary conditions

### CHANGE_REQUEST_LIFECYCLE-080
- **Title:** A proposed CR produces no mutation event in the log between proposal and decision
- **Level:** integration
- **Preconditions / fixtures:** `cr1` `proposed` (from -001); no decision yet.
- **When** querying `events_of(cr1)`.
- **Then** exactly one event exists for the CR: `CHANGE_PROPOSED`. No `NODE_VALUE_UPDATED` and no `CHANGE_APPROVED`/`CHANGE_REJECTED` appears until a terminal transition. `cr1.resulting_event` is null.
- **Covers:** event-stream invariant for `CHANGE_PROPOSED`.

### CHANGE_REQUEST_LIFECYCLE-081
- **Title:** Full event ordering for an approved CR: PROPOSED → mutation → APPROVED
- **Level:** integration
- **Preconditions / fixtures:** `cr1` proposed then approved (replays `updateCell`).
- **When** querying `events_of(cr1)` in chronological order.
- **Then** order is exactly `[CHANGE_PROPOSED, NODE_VALUE_UPDATED, CHANGE_APPROVED]`; the middle event is the one linked by `resulting_event`; all three share the same `sheet` and `change_request` link.
- **Covers:** events `CHANGE_PROPOSED`, `NODE_VALUE_UPDATED`, `CHANGE_APPROVED` (ordering + linkage).

### CHANGE_REQUEST_LIFECYCLE-082
- **Title:** approveChange on a non-existent / wrong-sheet change_request fails cleanly
- **Level:** unit
- **Preconditions / fixtures:** an invalid `change_request` id.
- **When** `C` calls `approveChange({change_request:"does-not-exist"})`.
- **Then** a not-found error is raised; no event emitted; no mutation. (Boundary: invalid CR reference.)
- **Covers:** cap `approveChange` (input boundary).

### CHANGE_REQUEST_LIFECYCLE-083
- **Title:** Replayed handler failure on approval does not leave a half-applied state or orphan event
- **Level:** integration
- **Preconditions / fixtures:** a CR whose deferred op would now violate an integrity constraint at decision time — e.g. `addColumn(field:"name")` proposed while a `(sheet, field="name")` column already exists (unique `(sheet, field)`); or `moveNode` whose target would create a NestedSet cycle.
- **Given** `cr.status=proposed`.
- **When** the approver calls `approveChange(cr)` and the replayed `cap.handler` raises.
- **Then** the transaction rolls back: `cr.status` stays `proposed` (not `approved`), `resulting_event` stays null, **no** mutation event and **no** `CHANGE_APPROVED` event are committed; the error surfaces to the approver. (Atomicity of replay + event emission.)
- **Covers:** cap `approveChange` (atomic replay); guards against orphaned `CHANGE_APPROVED`.

### CHANGE_REQUEST_LIFECYCLE-084
- **Title:** Stale cell version at approval is handled deterministically (no silent overwrite gap)
- **Level:** integration
- **Preconditions / fixtures:** `cr` proposing `updateCell(X, col:status, "done")` filed when `cell(X,col:status).version=n`. Before approval, `C` (or `B`) directly updates the same cell to `"wip"` (version `n+1`).
- **Given** the CR payload carries the original value but no stale version lock failure is expected (updateCell payload has no version field per schema), the replay applies on top of current state.
- **When** the approver approves the CR.
- **Then** the replay runs against current state: `cell(X,col:status)` becomes `"done"` with `version=n+2`; the `NODE_VALUE_UPDATED` payload's `old_value` reflects the **current** value `"wip"` (not the stale `n`-era value), `new_value="done"`. (Documents the spec's last-writer-wins replay; the event audit trail remains truthful about what was overwritten.)
- **Covers:** caps `approveChange` + `updateCell`; event `NODE_VALUE_UPDATED` (truthful old_value under concurrent edits).

---

## K. Notifications derived from CR lifecycle events (cross-consumer integration)

### CHANGE_REQUEST_LIFECYCLE-090
- **Title:** CHANGE_PROPOSED fans out a notification to the resolved approver's subscription
- **Level:** integration
- **Preconditions / fixtures:** `C` has `subscribe(scope=column, target=col:status, event_types=[CHANGE_PROPOSED], delivery=in-app)`; `E` proposes `updateCell(X, col:status,...)`.
- **When** the `CHANGE_PROPOSED` event is emitted and the notification dispatcher runs.
- **Then** a Notification row is created `(tree_event=CHANGE_PROPOSED event, change_request=cr, recipient=C, channel=in-app)` with `delivered_at` set; the dispatcher contains no mutation logic and reads only the event stream.
- **Covers:** event `CHANGE_PROPOSED` → notification dispatcher (derived consumer). (ARCHITECTURE §6.)

### CHANGE_REQUEST_LIFECYCLE-091
- **Title:** CHANGE_APPROVED + resulting mutation event notify a branch subscriber with requires_ack, and the ack ledger reconciles
- **Level:** e2e
- **Preconditions / fixtures:** persona `G` with `subscribe(scope=branch, target=P2, event_types=[CHANGE_APPROVED, NODE_DELETED], delivery=in-app, requires_ack=true)`; a CR deleting `Z` (in P2) proposed by `E` (approver `D`).
- **When** `D` approves; both `NODE_DELETED` (Z ∈ P2) and `CHANGE_APPROVED` events match `G`'s branch subscription.
- **Then** Notification rows are created for `G` (with `requires_ack=1`); `ack_report(NODE_DELETED event)` == `(1 notified, 0 acked)`; after `G` calls `acknowledge(notification)`, an Acknowledgement row exists and `ack_report` == `(1 notified, 1 acked)`.
- **Covers:** caps `approveChange` + `deleteNode` + `acknowledge`; events `NODE_DELETED`, `CHANGE_APPROVED` (cross-consumer accountability). (PERMISSIONS §3 G-row.)

### CHANGE_REQUEST_LIFECYCLE-092
- **Title:** CHANGE_REJECTED notifies a subscriber but emits no mutation event
- **Level:** integration
- **Preconditions / fixtures:** subscriber on `col:status` watching `[CHANGE_REJECTED]`; `cr1` rejected by `C`.
- **When** `rejectChange(cr1)` emits `CHANGE_REJECTED` and the dispatcher runs.
- **Then** a Notification for the subscriber is created off the `CHANGE_REJECTED` event; no `NODE_VALUE_UPDATED` exists for this CR; `cr1.resulting_event` null.
- **Covers:** event `CHANGE_REJECTED` → notification (no mutation cross-check).

---

## L. Surface parity (web ≡ REST ≡ agent) for the CR lifecycle

### CHANGE_REQUEST_LIFECYCLE-100
- **Title:** Identical CR is produced whether the unauthorized updateCell comes via web executeAction, REST method, or agent tool
- **Level:** e2e
- **Preconditions / fixtures:** the same actor `E` (and `AGENT` for the agent leg); same params `updateCell(X, col:status, "done")`.
- **Given** three entrypoints: web `executeAction`, `POST /api/method/arbor.update_cell`, and the agent tool `updateCell`.
- **When** each is invoked with the same params/actor.
- **Then** all three resolve `resolved_approver=C`, create a CR with identical `target_kind`/`operation`/`payload`, and emit one `CHANGE_PROPOSED` each (differing only by `actor`/`actor_type` for the agent leg). The authority decision, CR shape, and event type are identical across surfaces.
- **Covers:** caps `updateCell`→`suggestChange`; event `CHANGE_PROPOSED` (ARCHITECTURE §11 parity invariant).

### CHANGE_REQUEST_LIFECYCLE-101
- **Title:** approveChange behaves identically via REST method and via web executeAction
- **Level:** e2e
- **Preconditions / fixtures:** two equivalent CRs `cr_web`, `cr_api` in identical state (`proposed`, approver `C`).
- **When** `cr_web` is approved through web `executeAction("approveChange", …)` and `cr_api` through `POST /api/method/arbor.approve_change`.
- **Then** both replay the same handler as `C`, mutate equivalently, and emit the same `[mutation event, CHANGE_APPROVED]` pair with the same actor attribution; both set `resulting_event` and `status=approved`.
- **Covers:** cap `approveChange`; events mutation + `CHANGE_APPROVED` (surface parity).

### CHANGE_REQUEST_LIFECYCLE-102
- **Title:** EXT (external API consumer) filing an unauthorized change is bound to the same CR path
- **Level:** integration
- **Preconditions / fixtures:** `EXT`'s Frappe User; `EXT` owns/edits no column; `POST /api/method/arbor.update_cell {sheet:S, node:Y, column:col:budget, value:5}`.
- **When** the API write executes as `EXT`.
- **Then** a CR is created (`requester=EXT`, `resolved_approver=C`), `CHANGE_PROPOSED` emitted; no privileged bypass for external systems. (PERMISSIONS §3 EXT-row.)
- **Covers:** caps `updateCell`→`suggestChange`; event `CHANGE_PROPOSED` (external-system parity).

---

## M. Withdrawn/rejected CR cannot resurrect; append-only log integrity

### CHANGE_REQUEST_LIFECYCLE-110
- **Title:** Re-proposing after withdrawal creates a brand-new CR (no reuse of the withdrawn row)
- **Level:** integration
- **Preconditions / fixtures:** `cr1` `withdrawn` by `E`.
- **When** `E` re-files the same `updateCell` suggestion.
- **Then** a **new** Change Request row is created (`cr2`, `status=proposed`) with its own `CHANGE_PROPOSED` event; `cr1` stays `withdrawn` and is not mutated; the two are independent rows.
- **Covers:** cap `suggestChange`; event `CHANGE_PROPOSED` (terminal CRs immutable; no resurrection).

### CHANGE_REQUEST_LIFECYCLE-111
- **Title:** Tree Event log is append-only across the CR lifecycle (no events updated/deleted on decision)
- **Level:** integration
- **Preconditions / fixtures:** `cr1` proposed (emits event `e_prop`), then approved (emits `e_mut`, `e_app`).
- **When** the approval transition runs.
- **Then** `e_prop` row is unchanged after approval (not back-edited to reference the approval); three distinct append-only rows exist; no Tree Event is updated or deleted by any CR transition. `cr1.resulting_event` points to `e_mut` while `e_prop` and `e_app` remain intact.
- **Covers:** events `CHANGE_PROPOSED`, `NODE_VALUE_UPDATED`, `CHANGE_APPROVED` (append-only invariant; DATA-MODEL §12/§13).
