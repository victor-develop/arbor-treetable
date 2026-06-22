# Arbor — Test-Case Catalog: REST API (first-class peer)

> **Surface:** REST API as a first-class peer to Web (ARCHITECTURE §8/§9/§11, PERMISSIONS §3
> EXT row, §4 invariant 6). Test-first catalog written before implementation.
>
> **Scope of THIS surface:** every capability reachable via REST with an ACL outcome
> identical to web; auth enforcement; CRUD on sheets/columns/nodes/values; `getSheetSnapshot`;
> suggest/approve via API; pagination & filtering of large trees; error contracts
> (401/403/404/409); the external system using the tree as a queryable base-of-record;
> and the **surface-parity** guarantee that API and Web share one capability + ACL path.
>
> **Out of scope (covered by sibling catalogs):** web `executeAction` internals, agent
> Re-Act loop, webhook HMAC/retry delivery semantics, notification/ack dispatcher fan-out,
> SSO provider plumbing. Where this surface *touches* those (e.g. an API write must emit the
> event a webhook later consumes), we assert only the API-side contract — the emitted Tree
> Event — and defer downstream fan-out to those catalogs.

---

## Shared canonical fixtures (DRY — referenced, never redefined per test)

These are the **canonical personas and sample sheet** from `PERMISSIONS.md` §2. Every case
below references them by name; no test invents a bespoke world.

**Personas (each a Frappe User with an API key/secret unless noted):**

| Ref | Role |
|---|---|
| **A** | root `structural_owner` of sheet `S`; owns no columns |
| **B** | column owner of `col:name` (is_label) and `col:notes`; **editor** on `col:status` |
| **C** | column owner of `col:status` and `col:budget` |
| **D** | delegated sub-branch owner — active Branch Grant on node **P2** (`scope=structure`) |
| **E**, **F** | suggest-only users (no grants, own no columns) |
| **G** | sensitive subscriber, `requires_ack=true`; no edit authority |
| **EXT** | external system = API consumer (own User + API key) **+** a Webhook Endpoint subscriber |

**Sample sheet `S`** (root owner A), NestedSet tree:

```
root R          (struct authority: A)
├── P1          (struct authority: A)
│   └── X       (struct authority: A)
└── P2          (Branch Grant → D, active; struct authority: D)
    ├── Y       (struct authority: D, inherited)
    └── Z       (struct authority: D, inherited)
```

Columns: `col:name` (is_label, owner B), `col:status` (owner C, editors:[B]),
`col:budget` (owner C), `col:notes` (owner B). Default
`settings.owners_must_use_change_requests = false` unless a case states otherwise.

**Endpoint shorthand** (ARCHITECTURE §8.1). All capability methods are
`POST /api/method/arbor.<method>`; generic dispatch is
`POST /api/method/arbor.execute_action {action_id, params}`; snapshot is
`GET /api/method/arbor.get_sheet_snapshot?sheet=…`; DocType reads are
`GET /api/resource/<DocType>?filters=…`. Auth is a Frappe `Authorization: token <key>:<secret>`
header (or OIDC bearer) unless a case omits it deliberately.

**Standard envelope assertions** (apply to every case unless overridden): a 2xx response
carries the capability `Outcome` (`kind` ∈ {`executed`,`suggested`}); an authorized mutation
returns the created/updated record refs **and** an `event` ref; error responses use the
documented HTTP status with a stable machine-readable error code, never a stack trace.

---

## A. Authentication & authorization gate (transport-level)

### API-001
- **Title:** Unauthenticated capability call is rejected with 401
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; no auth header supplied.
- **Given** a request with **no** `Authorization` header
  **When** `POST /api/method/arbor.update_cell {sheet:S, node:X, column:col:status, value:"done"}`
  **Then** respond **401**; the body carries an auth error code; **no** Tree Node Value is
  mutated and **no** Tree Event of any type is emitted.
- **Covers:** `updateCell` (auth gate precedes ACL); no event.

### API-002
- **Title:** Expired / invalid token is rejected with 401 before ACL resolution
- **Level:** integration
- **Preconditions / fixtures:** EXT with a revoked/garbage API key.
- **Given** an invalid bearer/token credential
  **When** any `POST /api/method/arbor.*` capability is called
  **Then** **401**; resolver is never invoked (assert via spy/log that
  `arbor.acl.resolver` was not entered); no event emitted.
- **Covers:** auth precedence over ACL; no event.

### API-003
- **Title:** Authenticated request resolves actor identity used by the ACL
- **Level:** integration
- **Preconditions / fixtures:** B's API key.
- **Given** B's valid credentials
  **When** `POST /api/method/arbor.update_cell {sheet:S, node:Z, column:col:name, value:"Zed"}`
  **Then** **200**, `kind="executed"`; the emitted `NODE_VALUE_UPDATED` event has
  `actor == B` and `actor_type == "human"` (API callers are humans/external, never `agent`).
- **Covers:** `updateCell`; `NODE_VALUE_UPDATED`.

### API-004
- **Title:** Read endpoints also require auth
- **Level:** integration
- **Preconditions / fixtures:** none authenticated.
- **Given** no credentials
  **When** `GET /api/method/arbor.get_sheet_snapshot?sheet=S` **and**
  `GET /api/resource/Tree Event?filters=[["sheet","=","S"]]`
  **Then** both return **401**.
- **Covers:** `getSheetSnapshot`; auto-REST read auth gate.

---

## B. Surface-parity invariant (the primary anchor — ARCHITECTURE §11, PERMISSIONS §4.6)

### API-010
- **Title:** API authorized write ≡ Web `executeAction` — identical authority, mutation, event
- **Level:** integration
- **Preconditions / fixtures:** C's API key; clean `col:budget` cell on Y.
- **Given** the same capability+params+actor invoked two ways
  **When** (1) `execute_action("updateCell", {sheet:S,node:Y,column:col:budget,value:42}, C)`
  directly (web path) on a reset fixture, and (2) the REST call
  `POST /api/method/arbor.update_cell` with the same params as C on an identically reset fixture
  **Then** both yield `kind="executed"`, both increment `Tree Node Value.version` by exactly 1,
  both emit exactly one `NODE_VALUE_UPDATED` with payload `{node:Y, column:col:budget,
  old_value, new_value:42, version}`, and the two outcomes are field-for-field equal except
  for ids/timestamps.
- **Covers:** `updateCell`; `NODE_VALUE_UPDATED`; surface parity.

### API-011
- **Title:** API unauthorized write ≡ Web — identical Change Request + CHANGE_PROPOSED
- **Level:** integration
- **Preconditions / fixtures:** A's API key (A owns no columns).
- **Given** A attempting an Axis-2 edit two ways (web `executeAction` vs REST)
  **When** `updateCell {sheet:S, node:X, column:col:budget, value:7}` as A on each surface
  **Then** both yield `kind="suggested"`; both create a Change Request with
  `target_kind="cell-value"`, `operation="update"`, `requester=A`, `resolved_approver=C`,
  `payload` == the original params; both emit exactly one `CHANGE_PROPOSED`
  (`change_request` linked); **no** `NODE_VALUE_UPDATED`. The two CRs are equal except ids.
- **Covers:** `updateCell` → CR; `CHANGE_PROPOSED`; surface parity (suggest branch).

### API-012
- **Title:** `execute_action` generic dispatch ≡ named capability method
- **Level:** integration
- **Preconditions / fixtures:** B's API key.
- **Given** the generic and the named REST forms of the same capability
  **When** `POST /api/method/arbor.execute_action {action_id:"updateCell",
  params:{sheet:S,node:Z,column:col:notes,value:"n"}}` vs
  `POST /api/method/arbor.update_cell {sheet:S,node:Z,column:col:notes,value:"n"}`
  **Then** identical outcome, identical single `NODE_VALUE_UPDATED` shape. Both routes funnel
  through the one `arbor.capabilities.execute.execute_action`.
- **Covers:** `updateCell`; dispatch parity.

### API-013
- **Title:** Every LLM-exposed capability has a reachable REST method
- **Level:** unit
- **Preconditions / fixtures:** capability registry loaded.
- **Given** `registry.all()`
  **When** enumerating capabilities
  **Then** every capability (except `internalReset`) is reachable both as
  `arbor.execute_action(action_id=…)` and as its named `arbor.<method>` endpoint listed in
  §8.1; `internalReset` is **not** auto-exposed as an unauthenticated/whitelisted convenience
  method.
- **Covers:** registry→REST exposure completeness; `internalReset` exclusion.

---

## C. CRUD on sheets / columns / nodes / values — happy paths

### API-020
- **Title:** Add a node under an owned branch (Axis 1 happy path)
- **Level:** integration
- **Preconditions / fixtures:** A's API key.
- **Given** A owns root structure
  **When** `POST /api/method/arbor.add_node {sheet:S, parent:P1, after:X,
  values:{name:"X2"}}`
  **Then** **200**, `kind="executed"`; a Tree Node is created under P1 with valid NestedSet
  `lft/rgt` and `idx` after X; the initial `name` value is written; emits exactly one
  `NODE_CREATED`. (Initial `values` writes are part of the create handler, not separate
  `updateCell` events.)
- **Covers:** `addNode`; `NODE_CREATED`.

### API-021
- **Title:** Add a root-level node (parent=null → sheet owner authority)
- **Level:** integration
- **Preconditions / fixtures:** A's API key.
- **Given** `parent:null`
  **When** `POST /api/method/arbor.add_node {sheet:S, parent:null}`
  **Then** authority resolves directly to `S.structural_owner` (A) → `kind="executed"`;
  new root-level node created; one `NODE_CREATED`.
- **Covers:** `addNode` (null-parent branch); `NODE_CREATED`.

### API-022
- **Title:** Update a cell value as the column owner
- **Level:** integration
- **Preconditions / fixtures:** C's API key.
- **Given** C owns `col:budget`
  **When** `POST /api/method/arbor.update_cell {sheet:S, node:X, column:col:budget, value:100}`
  **Then** **200**, `kind="executed"`; `version` increments by 1; one `NODE_VALUE_UPDATED`
  carrying `old_value`, `new_value:100`, new `version`.
- **Covers:** `updateCell`; `NODE_VALUE_UPDATED`.

### API-023
- **Title:** Move a node within a single owned branch
- **Level:** integration
- **Preconditions / fixtures:** D's API key; both source and destination inside P2.
- **Given** D owns P2 subtree (so src=Y's parent and dest are both under D's grant)
  **When** `POST /api/method/arbor.move_node {sheet:S, node:Z, new_parent:Y}`
  **Then** src approver == dest approver == D == actor → `kind="executed"`; NestedSet
  `lft/rgt` re-nested consistently; one `NODE_MOVED`.
- **Covers:** `moveNode` (both ends owned); `NODE_MOVED`.

### API-024
- **Title:** Delete a node in an owned branch (cascade)
- **Level:** integration
- **Preconditions / fixtures:** D's API key.
- **Given** D owns P2
  **When** `POST /api/method/arbor.delete_node {sheet:S, node:Z, cascade:true}`
  **Then** **200**, `kind="executed"`; Z (and any descendants) removed; one `NODE_DELETED`.
- **Covers:** `deleteNode`; `NODE_DELETED`.

### API-025
- **Title:** Add a column (meta — sheet structural_owner authority)
- **Level:** integration
- **Preconditions / fixtures:** A's API key.
- **Given** A is sheet `structural_owner`
  **When** `POST /api/method/arbor.add_column {sheet:S, field:"due", label:"Due",
  type:"text", column_owner:C}`
  **Then** **200**, `kind="executed"`; a Tree Column row added with `(sheet,field)` unique;
  one `COLUMN_CONFIG_UPDATED`.
- **Covers:** `addColumn`; `COLUMN_CONFIG_UPDATED`.

### API-026
- **Title:** Update a column config as column owner
- **Level:** integration
- **Preconditions / fixtures:** C's API key.
- **Given** C owns `col:status`
  **When** `POST /api/method/arbor.update_column {sheet:S, column:col:status,
  patch:{width:240}}`
  **Then** **200**, `kind="executed"`; column width updated; one `COLUMN_CONFIG_UPDATED`.
- **Covers:** `updateColumn`; `COLUMN_CONFIG_UPDATED`.

### API-027
- **Title:** Delete a column as column owner
- **Level:** integration
- **Preconditions / fixtures:** C's API key.
- **Given** C owns `col:budget`
  **When** `POST /api/method/arbor.delete_column {sheet:S, column:col:budget}`
  **Then** **200**, `kind="executed"`; Tree Column removed; one `COLUMN_CONFIG_UPDATED`.
- **Covers:** `deleteColumn`; `COLUMN_CONFIG_UPDATED`.

### API-028
- **Title:** Editing the node label routes through Axis 2 (label is a column value)
- **Level:** integration
- **Preconditions / fixtures:** B's API key (owner of is_label `col:name`).
- **Given** the node label is the value of the `is_label` column
  **When** `POST /api/method/arbor.update_cell {sheet:S, node:X, column:col:name,
  value:"Renamed"}`
  **Then** **200**, `kind="executed"` (Axis 2, B authorized); one `NODE_VALUE_UPDATED`,
  **not** a structural event — confirming label edits are Axis 2 even though node creation
  was Axis 1.
- **Covers:** `updateCell` on is_label column; `NODE_VALUE_UPDATED`.

---

## D. Permission-DENIED paths via REST (Axis-correct routing)

### API-040
- **Title:** Root owner editing a non-owned column → CR to column owner (axis independence)
- **Level:** integration
- **Preconditions / fixtures:** A's API key.
- **Given** A owns structure but no columns
  **When** `POST /api/method/arbor.update_cell {sheet:S, node:X, column:col:budget, value:9}`
  **Then** `kind="suggested"`; CR `resolved_approver=C`, `target_kind="cell-value"`; one
  `CHANGE_PROPOSED`; HTTP **200** (suggest is a success outcome, not a 403 — the request was
  accepted and converted to a proposal).
- **Covers:** `updateCell` → CR; `CHANGE_PROPOSED`; Axis-2 independence.

### API-041
- **Title:** Column owner attempting a structural add → CR to structural owner
- **Level:** integration
- **Preconditions / fixtures:** B's API key.
- **Given** B has no structural authority
  **When** `POST /api/method/arbor.add_node {sheet:S, parent:P1}`
  **Then** `kind="suggested"`; CR `resolved_approver=A`, `target_kind="node-structure"`,
  `operation="add"`; one `CHANGE_PROPOSED`; no `NODE_CREATED`.
- **Covers:** `addNode` → CR; `CHANGE_PROPOSED`.

### API-042
- **Title:** Suggest-only user value edit → CR
- **Level:** integration
- **Preconditions / fixtures:** E's API key.
- **Given** E owns nothing
  **When** `POST /api/method/arbor.update_cell {sheet:S, node:X, column:col:status,
  value:"blocked"}`
  **Then** `kind="suggested"`; CR `resolved_approver=C` (owner; editors B also eligible to
  approve); one `CHANGE_PROPOSED`.
- **Covers:** `updateCell` → CR; `CHANGE_PROPOSED`.

### API-043
- **Title:** External system write bound by identical two-axis ACL (no special privilege)
- **Level:** integration
- **Preconditions / fixtures:** EXT's API key; EXT owns/edits no columns.
- **Given** EXT is just an API consumer
  **When** `POST /api/method/arbor.update_cell {sheet:S, node:Y, column:col:status,
  value:"ext"}`
  **Then** `kind="suggested"`; CR routed to C; one `CHANGE_PROPOSED`. EXT gets no bypass.
  (PERMISSIONS §3 EXT row.)
- **Covers:** `updateCell` → CR; `CHANGE_PROPOSED`; external-system parity.

### API-044
- **Title:** Approve attempt by a non-approver is denied (403), not converted to a CR
- **Level:** integration
- **Preconditions / fixtures:** an existing PROPOSED CR `cr1` with `resolved_approver=C`; E's key.
- **Given** E is neither approver nor column editor
  **When** `POST /api/method/arbor.approve_change {change_request:cr1}`
  **Then** **403**; CR stays `proposed`; no replay event, no `CHANGE_APPROVED`. (Decision
  capabilities have a hard ACL — they are not themselves suggestible.)
- **Covers:** `approveChange` ACL DENY; no event.

### API-045
- **Title:** Reject attempt by a non-approver is denied (403)
- **Level:** integration
- **Preconditions / fixtures:** PROPOSED CR `cr1` (`resolved_approver=C`); F's key.
- **Given** F is not the approver
  **When** `POST /api/method/arbor.reject_change {change_request:cr1}`
  **Then** **403**; CR stays `proposed`; no `CHANGE_REJECTED`.
- **Covers:** `rejectChange` ACL DENY; no event.

### API-046
- **Title:** Withdraw by a non-requester is denied (403)
- **Level:** integration
- **Preconditions / fixtures:** CR `cr2` with `requester=E`; F's key.
- **Given** F did not file `cr2`
  **When** `POST /api/method/arbor.withdraw_change {change_request:cr2}`
  **Then** **403**; CR stays `proposed`; no event.
- **Covers:** `withdrawChange` ACL DENY (requester-only); no event.

### API-047
- **Title:** Raw auto-REST write to a governed DocType is denied (must go through capability)
- **Level:** integration
- **Preconditions / fixtures:** C's API key (a legitimate owner).
- **Given** governed DocTypes forbid direct writes (DATA-MODEL §13)
  **When** `POST /api/resource/Tree Node Value {node:X, column:col:budget, value:5}` (raw
  Frappe write) and `PUT /api/resource/Tree Event/<id>` (append-only)
  **Then** both **403** (Frappe permission denies write); the **only** sanctioned mutation path
  is `execute_action`; no Tree Event emitted by the raw attempt.
- **Covers:** governed-DocType write lockdown; append-only enforcement; no event.

### API-048
- **Title:** Column editor can edit but cannot delete a column they only edit
- **Level:** integration
- **Preconditions / fixtures:** B's API key (editor on `col:status`, not owner).
- **Given** B is in `col:status.editors` but is not `column_owner`
  **When** (1) `update_cell {…,column:col:status,…}` and (2)
  `delete_column {column:col:status}`
  **Then** (1) executes (editors are owner-equivalent for value/approval per
  `resolve_column_approvers`) → `NODE_VALUE_UPDATED`; (2) **also** executes — `deleteColumn`
  ACL is `resolve_column_approvers` which includes editors → `COLUMN_CONFIG_UPDATED`.
  Asserts the resolver set is applied uniformly (editor == approver for the column's meta ops).
- **Covers:** `updateCell`, `deleteColumn`; `NODE_VALUE_UPDATED`, `COLUMN_CONFIG_UPDATED`;
  editor-set semantics.

---

## E. Delegation edge cases via REST

### API-060
- **Title:** Delegated owner adds within granted subtree → executes
- **Level:** integration
- **Preconditions / fixtures:** D's API key.
- **Given** active Branch Grant P2→D
  **When** `POST /api/method/arbor.add_node {sheet:S, parent:Y}` (Y under P2)
  **Then** ancestor walk Y→P2 hits the active grant → approver D == actor → `kind="executed"`;
  one `NODE_CREATED`.
- **Covers:** `addNode` (delegated); `NODE_CREATED`.

### API-061
- **Title:** Delegated owner acting OUTSIDE the grant → CR to root owner
- **Level:** integration
- **Preconditions / fixtures:** D's API key.
- **Given** the grant is scoped to P2 only
  **When** `POST /api/method/arbor.add_node {sheet:S, parent:P1}` (P1 not under P2)
  **Then** walk P1→root finds no grant → approver A → `kind="suggested"`; CR
  `resolved_approver=A`; one `CHANGE_PROPOSED`.
- **Covers:** `addNode` → CR; delegation scoping; `CHANGE_PROPOSED`.

### API-062
- **Title:** Nearest-grant-wins with nested delegation
- **Level:** integration
- **Preconditions / fixtures:** an additional active Branch Grant on **Z** → **D2** (nested
  inside D's P2 grant); D2's API key; a child node `Zc` under Z.
- **Given** grants on both P2 (D) and Z (D2)
  **When** `POST /api/method/arbor.delete_node {sheet:S, node:Zc}` as D2
  **Then** ancestor walk Zc→Z hits the **nearest** active grant (D2) before P2 → approver D2
  == actor → `kind="executed"`; one `NODE_DELETED`. (PERMISSIONS §4.2 nearest-grant-wins.)
- **Covers:** `deleteNode` (nested delegation); `NODE_DELETED`.

### API-063
- **Title:** Sub-delegation within an owned branch via API
- **Level:** integration
- **Preconditions / fixtures:** D's API key.
- **Given** `resolve_structural_approver(Z) = D`
  **When** `POST /api/method/arbor.delegate_branch {sheet:S, branch_root:Z, grantee:F}`
  **Then** **200**, `kind="executed"`; a Branch Grant (Z→F, active, `granted_by=D`) created;
  one `DELEGATION_CHANGED`. F now resolves as approver for structural ops at/under Z.
- **Covers:** `delegateBranch`; `DELEGATION_CHANGED`.

### API-064
- **Title:** Delegating a branch you don't own → CR to the real approver
- **Level:** integration
- **Preconditions / fixtures:** F's API key (no authority over P1).
- **Given** `delegateBranch` is Axis-1 (`resolve_structural_approver(branch_root)`)
  **When** `POST /api/method/arbor.delegate_branch {sheet:S, branch_root:P1, grantee:F}`
  **Then** approver resolves to A; F ≠ A → `kind="suggested"`; CR routed to A; one
  `CHANGE_PROPOSED`; no Branch Grant created yet.
- **Covers:** `delegateBranch` → CR; `CHANGE_PROPOSED`.

### API-065
- **Title:** Revoke delegation, then a previously-delegated action falls back to root owner
- **Level:** integration
- **Preconditions / fixtures:** A's API key (or D as `granted_by`); the P2→D grant.
- **Given** D currently owns P2
  **When** (1) `POST /api/method/arbor.revoke_delegation {branch_grant:<P2→D>}` as A →
  expect `DELEGATION_CHANGED`, grant `active=0`; then (2) `add_node {sheet:S, parent:Y}` as D
  **Then** (1) executes; (2) ancestor walk now finds **no** active grant → approver A →
  D's add becomes `kind="suggested"` (CR to A). Demonstrates revocation re-routes future
  authority immediately.
- **Covers:** `revokeDelegation`, `addNode`; `DELEGATION_CHANGED`, `CHANGE_PROPOSED`.

### API-066
- **Title:** Revoke by a party with no authority over the grant → denied/suggested
- **Level:** integration
- **Preconditions / fixtures:** E's API key; P2→D grant.
- **Given** `revokeDelegation` ACL = `granted_by` **or** ancestor structural owner
  **When** `POST /api/method/arbor.revoke_delegation {branch_grant:<P2→D>}` as E
  **Then** E is neither `granted_by` (A) nor an ancestor structural owner → not authorized →
  `kind="suggested"` (CR to the resolved structural approver of P2's parent chain, i.e. A);
  grant stays `active=1`; one `CHANGE_PROPOSED`.
- **Covers:** `revokeDelegation` → CR; `CHANGE_PROPOSED`.

### API-067
- **Title:** Move requires authority over BOTH ends (cross-branch) → CR to dest, src co-approver
- **Level:** integration
- **Preconditions / fixtures:** A's API key; move X (under A's branch) into P2 (D's branch).
- **Given** src approver = A, dest approver = D, actor A ≠ both
  **When** `POST /api/method/arbor.move_node {sheet:S, node:X, new_parent:P2}`
  **Then** `kind="suggested"`; CR `resolved_approver=D` (dest) with `payload.co_approvers`
  containing A (src); one `CHANGE_PROPOSED`; no `NODE_MOVED`. (PERMISSIONS §4.4.)
- **Covers:** `moveNode` → CR; co-approver; `CHANGE_PROPOSED`.

### API-068
- **Title:** Grant column ownership via API (Axis 2 admin)
- **Level:** integration
- **Preconditions / fixtures:** C's API key (current owner of `col:budget`).
- **Given** `grantColumn` ACL = current `column_owner` or sheet `structural_owner`
  **When** `POST /api/method/arbor.grant_column {sheet:S, column:col:budget,
  column_owner:E, editors:[F]}`
  **Then** **200**, `kind="executed"`; `col:budget.column_owner=E`, editors=[F]; one
  `COLUMN_CONFIG_UPDATED`. A subsequent `updateCell` on `col:budget` by E now executes.
- **Covers:** `grantColumn`; `COLUMN_CONFIG_UPDATED`.

---

## F. Suggest / approve / reject / withdraw lifecycle via API

### API-080
- **Title:** Explicit `suggestChange` is always allowed
- **Level:** integration
- **Preconditions / fixtures:** E's API key.
- **Given** `suggestChange` ACL = always allowed
  **When** `POST /api/method/arbor.suggest_change {sheet:S, target_kind:"cell-value",
  operation:"update", payload:{sheet:S,node:X,column:col:budget,value:3}}`
  **Then** **200**, `kind="suggested"`; CR created (`requester=E`, `resolved_approver=C`); one
  `CHANGE_PROPOSED`. No prior failed mutation attempt needed.
- **Covers:** `suggestChange`; `CHANGE_PROPOSED`.

### API-081
- **Title:** Approve a value-edit CR replays the handler as approver and emits real event
- **Level:** integration
- **Preconditions / fixtures:** CR `cr1` (cell-value update on col:budget, `resolved_approver=C`);
  C's API key.
- **Given** a PROPOSED CR
  **When** `POST /api/method/arbor.approve_change {change_request:cr1}` as C
  **Then** **200**; handler re-runs **as C** → emits the real `NODE_VALUE_UPDATED` (the cell
  changes, `version` increments), then emits `CHANGE_APPROVED`; CR → `approved` (terminal),
  `decided_by=C`, `decided_at` set, `resulting_event` linked to the `NODE_VALUE_UPDATED`.
- **Covers:** `approveChange`; `NODE_VALUE_UPDATED` + `CHANGE_APPROVED`.

### API-082
- **Title:** Column editor (not owner) may approve a column CR
- **Level:** integration
- **Preconditions / fixtures:** CR on `col:status` (`resolved_approver=C`, owner); B's API key
  (editor on `col:status`).
- **Given** approval ACL = approver **or** column editor
  **When** `POST /api/method/arbor.approve_change {change_request:<cr>}` as B
  **Then** **200**; replay runs as B; `NODE_VALUE_UPDATED` + `CHANGE_APPROVED`; CR `approved`,
  `decided_by=B`. (Editors are owner-equivalent for column approvals.)
- **Covers:** `approveChange` (editor); `NODE_VALUE_UPDATED`, `CHANGE_APPROVED`.

### API-083
- **Title:** Reject a CR — no data mutation
- **Level:** integration
- **Preconditions / fixtures:** PROPOSED CR `cr1` (`resolved_approver=C`); C's API key.
- **Given** an approver rejecting
  **When** `POST /api/method/arbor.reject_change {change_request:cr1, comment:"no"}`
  **Then** **200**; one `CHANGE_REJECTED`; **no** mutation event; CR → `rejected` (terminal),
  `decided_by=C`, `resulting_event` null.
- **Covers:** `rejectChange`; `CHANGE_REJECTED`.

### API-084
- **Title:** Requester withdraws their own CR
- **Level:** integration
- **Preconditions / fixtures:** CR `cr2` (`requester=E`); E's API key.
- **Given** withdraw ACL = requester only
  **When** `POST /api/method/arbor.withdraw_change {change_request:cr2}` as E
  **Then** **200**; CR → `withdrawn` (terminal); emits `CHANGE_REJECTED` with status=withdrawn
  semantics (per registry); no data mutation.
- **Covers:** `withdrawChange`; `CHANGE_REJECTED` (withdrawn).

### API-085
- **Title:** Approving a terminal CR is a 409 conflict (idempotency of decisions)
- **Level:** integration
- **Preconditions / fixtures:** CR `cr1` already `approved`; C's API key.
- **Given** a terminal CR
  **When** `POST /api/method/arbor.approve_change {change_request:cr1}` again
  **Then** **409**; no second replay, no duplicate `NODE_VALUE_UPDATED`/`CHANGE_APPROVED`; the
  original `resulting_event` is unchanged. Decisions are single-shot.
- **Covers:** `approveChange` conflict; no duplicate event.

### API-086
- **Title:** Stale CR re-resolves approver at decision time and re-routes
- **Level:** integration
- **Preconditions / fixtures:** CR `cr3` proposed against a node in P2 with
  `resolved_approver=D`; then the P2→D grant is revoked (now A is the approver); C/A keys.
- **Given** the ancestor grants changed after proposal
  **When** D attempts `approve_change {cr3}` after revocation
  **Then** re-resolution at decision time recomputes approver = A; D is no longer the approver
  → **403** for D, and `cr3.resolved_approver` is updated/re-routed to A (per ARCHITECTURE §5
  "re-routed if stale"); A approving then succeeds. No mutation occurs under D's attempt.
- **Covers:** `approveChange` re-resolution; CR re-route; (on A's approve) the structural event.

### API-087
- **Title:** Approving a cell-value CR detects an intervening change → 409 stale conflict
- **Level:** integration
- **Preconditions / fixtures:** CR `crv` proposing `col:budget=10` on X captured at
  `version=v`; then C directly sets `col:budget=20` (now `version=v+1`); C's API key.
- **Given** the target cell's `version` advanced since the CR's payload was captured
  **When** C approves `crv`
  **Then** **409** stale-value conflict (the replay's optimistic version check fails); no
  `NODE_VALUE_UPDATED` from the replay; CR remains `proposed` (or transitions to a
  needs-rebase state per impl) — assert the value is NOT silently overwritten to 10.
- **Covers:** `approveChange` (cell) stale conflict; no spurious `NODE_VALUE_UPDATED`.

---

## G. `getSheetSnapshot` — the shared read serializer

### API-100
- **Title:** Snapshot returns one canonical shape (tree + column config + ownership)
- **Level:** integration
- **Preconditions / fixtures:** B's API key (any reader who can view S).
- **Given** sheet S
  **When** `GET /api/method/arbor.get_sheet_snapshot?sheet=S`
  **Then** **200**; body includes the column configs (each with `column_owner` + `editors`),
  the node tree with NestedSet ordering, and per-cell values+versions; **no** Tree Event is
  emitted (read). Shape matches the shared serializer used by web/agent (assert against the
  serializer contract, not a bespoke shape).
- **Covers:** `getSheetSnapshot`; no event.

### API-101
- **Title:** Snapshot serializer parity across surfaces
- **Level:** integration
- **Preconditions / fixtures:** B's API key + direct `arbor.snapshot.serializer.get_sheet_snapshot(S)`.
- **Given** the same sheet read two ways
  **When** REST `get_sheet_snapshot?sheet=S` vs the in-process serializer call
  **Then** byte-equivalent payload (modulo transport envelope) — one serializer, no
  surface-specific reshaping.
- **Covers:** `getSheetSnapshot`; serializer DRY.

### API-102
- **Title:** Snapshot of an unknown sheet → 404
- **Level:** integration
- **Preconditions / fixtures:** B's API key.
- **Given** a non-existent sheet id
  **When** `GET /api/method/arbor.get_sheet_snapshot?sheet=does-not-exist`
  **Then** **404**; documented not-found error code; no partial body.
- **Covers:** `getSheetSnapshot` 404.

### API-103
- **Title:** Snapshot reflects committed mutations (read-after-write consistency)
- **Level:** integration
- **Preconditions / fixtures:** C's API key.
- **Given** a successful `update_cell {node:X, column:col:budget, value:77}`
  **When** a subsequent `get_sheet_snapshot?sheet=S`
  **Then** the X/`col:budget` cell shows `value:77` and the incremented `version`. API write
  then API read are consistent.
- **Covers:** `updateCell` + `getSheetSnapshot`; `NODE_VALUE_UPDATED`.

---

## H. Pagination & filtering of large trees (base-of-record queries)

### API-120
- **Title:** Paginated subtree read via auto-REST with limit/offset
- **Level:** integration
- **Preconditions / fixtures:** a large fixture variant of S with ≥ 500 Tree Nodes (canonical
  "big tree" fixture); EXT's API key.
- **Given** an external system querying the tree as a base-of-record
  **When** `GET /api/resource/Tree Node?filters=[["sheet","=","S"]]&limit_page_length=100&limit_start=0`
  then `limit_start=100`
  **Then** stable ordering (by `lft`), exactly 100 rows per page, no overlap/gap across pages,
  and a total-count affordance; auth required.
- **Covers:** auto-REST list pagination (read; no event).

### API-121
- **Title:** Filter nodes by NestedSet descendant range (branch query)
- **Level:** integration
- **Preconditions / fixtures:** big-tree S; EXT's API key.
- **Given** P2's `lft`/`rgt`
  **When** `GET /api/resource/Tree Node?filters=[["sheet","=","S"],["lft",">",<P2.lft>],
  ["rgt","<",<P2.rgt>]]`
  **Then** returns exactly P2's descendants (Y, Z, …) and excludes nodes outside P2 — proving
  the tree is queryable as a relational base-of-record using documented indexed columns.
- **Covers:** auto-REST descendant filter (read; no event).

### API-122
- **Title:** Filter Tree Node Value by column (column slice across the tree)
- **Level:** integration
- **Preconditions / fixtures:** big-tree S; EXT's API key.
- **Given** an external report needs all `col:status` values
  **When** `GET /api/resource/Tree Node Value?filters=[["sheet","=","S"],
  ["column","=","col:status"]]&fields=["node","value","version"]&limit_page_length=200`
  **Then** returns the column slice with field projection honored; paginates; auth required.
- **Covers:** auto-REST value query + field projection (read; no event).

### API-123
- **Title:** Query the Tree Event log as an audit base-of-record (filter + order)
- **Level:** integration
- **Preconditions / fixtures:** S with prior events; EXT's API key.
- **Given** an external auditor
  **When** `GET /api/resource/Tree Event?filters=[["sheet","=","S"],
  ["type","=","NODE_VALUE_UPDATED"]]&order_by=creation desc&limit_page_length=50`
  **Then** returns only matching events in descending time order, paginated; the log is
  read-only via REST (writes 403 per API-047).
- **Covers:** Tree Event auto-REST query (read; append-only).

### API-124
- **Title:** Pagination boundary — offset past the end returns empty, not error
- **Level:** integration
- **Preconditions / fixtures:** big-tree S; EXT's API key.
- **Given** `limit_start` beyond total row count
  **When** `GET /api/resource/Tree Node?filters=[["sheet","=","S"]]&limit_start=100000`
  **Then** **200** with an empty result set (and total count if requested), not a 4xx/5xx.
- **Covers:** pagination boundary (read; no event).

### API-125
- **Title:** Malformed filter JSON → 400 with stable error
- **Level:** integration
- **Preconditions / fixtures:** EXT's API key.
- **Given** a syntactically invalid `filters` param
  **When** `GET /api/resource/Tree Node?filters=NOT-JSON`
  **Then** **400**; documented bad-request error code; no stack trace leaked.
- **Covers:** filter validation 400.

---

## I. Error contracts & boundary conditions

### API-140
- **Title:** Schema-invalid params → 400 before ACL/handler
- **Level:** integration
- **Preconditions / fixtures:** C's API key.
- **Given** a required param is missing
  **When** `POST /api/method/arbor.update_cell {sheet:S, node:X}` (no `column`/`value`)
  **Then** **400** from `validate_schema`; resolver and handler never run; no event. (Param
  validation precedes authority in `execute_action`.)
- **Covers:** `updateCell` schema 400; no event.

### API-141
- **Title:** Wrong-typed param rejected by JSON-schema → 400
- **Level:** integration
- **Preconditions / fixtures:** A's API key.
- **Given** `addColumn` `type` outside the enum
  **When** `POST /api/method/arbor.add_column {sheet:S, field:"f", label:"F", type:"date"}`
  **Then** **400** (`type` not in the documented enum); no Tree Column created; no event.
- **Covers:** `addColumn` schema 400; no event.

### API-142
- **Title:** Unknown action_id via generic dispatch → 404/400
- **Level:** integration
- **Preconditions / fixtures:** C's API key.
- **Given** an action not in the registry
  **When** `POST /api/method/arbor.execute_action {action_id:"frobnicate", params:{}}`
  **Then** error (404 unknown-capability, or 400 per impl convention — assert a stable
  documented code, not 500); no event.
- **Covers:** registry lookup failure; no event.

### API-143
- **Title:** Reference to a non-existent node → 404
- **Level:** integration
- **Preconditions / fixtures:** C's API key.
- **Given** a valid schema but a dangling `node`
  **When** `POST /api/method/arbor.update_cell {sheet:S, node:"ghost", column:col:budget,
  value:1}`
  **Then** **404** (node not found); no event. (Existence checks distinct from ACL DENY.)
- **Covers:** `updateCell` 404.

### API-144
- **Title:** Reference to a non-existent column → 404
- **Level:** integration
- **Preconditions / fixtures:** C's API key.
- **Given** a dangling `column`
  **When** `POST /api/method/arbor.update_cell {sheet:S, node:X, column:"col:ghost", value:1}`
  **Then** **404**; no event.
- **Covers:** `updateCell` 404 (column).

### API-145
- **Title:** Approve/withdraw a non-existent Change Request → 404
- **Level:** integration
- **Preconditions / fixtures:** C's API key.
- **Given** a dangling CR id
  **When** `POST /api/method/arbor.approve_change {change_request:"cr-ghost"}`
  **Then** **404**; no event.
- **Covers:** `approveChange` 404.

### API-146
- **Title:** Move that would create a cycle → 409 (NestedSet integrity)
- **Level:** integration
- **Preconditions / fixtures:** D's API key (authorized over P2 both ends).
- **Given** D is authorized but the move is structurally illegal
  **When** `POST /api/method/arbor.move_node {sheet:S, node:P2, new_parent:Y}` (Y is a
  descendant of P2)
  **Then** **409** integrity conflict (a node cannot be moved under its own descendant); no
  `NODE_MOVED`; NestedSet `lft/rgt` unchanged. ACL passing does not bypass structural validity.
- **Covers:** `moveNode` integrity 409; no event.

### API-147
- **Title:** Duplicate column field violates `(sheet, field)` uniqueness → 409
- **Level:** integration
- **Preconditions / fixtures:** A's API key.
- **Given** `col:status` already exists
  **When** `POST /api/method/arbor.add_column {sheet:S, field:"status", label:"Dup",
  type:"text"}`
  **Then** **409** uniqueness conflict; no second Tree Column; no event.
- **Covers:** `addColumn` uniqueness 409; no event.

### API-148
- **Title:** Adding a second is_label column rejected → 409
- **Level:** integration
- **Preconditions / fixtures:** A's API key (col:name is already is_label).
- **Given** exactly one is_label per sheet (DATA-MODEL §13)
  **When** `POST /api/method/arbor.add_column {sheet:S, field:"alt", label:"Alt",
  type:"text", is_label:true}`
  **Then** **409**; no event. Enforces the single-label invariant.
- **Covers:** `addColumn` is_label uniqueness 409; no event.

### API-149
- **Title:** `internalReset` is not callable as an authenticated user capability
- **Level:** integration
- **Preconditions / fixtures:** A's API key (sheet owner, still not system/admin).
- **Given** `internalReset` ACL = system/admin only and `is_exposed_to_llm=false`
  **When** `POST /api/method/arbor.execute_action {action_id:"internalReset",
  params:{sheet:S, confirm:true}}` as A
  **Then** **403**; no purge; no event on the stream. Confirms the destructive op is not
  reachable via the ordinary capability surface.
- **Covers:** `internalReset` lockdown; no event.

---

## J. Idempotency & concurrency on the write path

### API-160
- **Title:** Optimistic-concurrency stale move → 409 (the spec's named conflict case)
- **Level:** integration
- **Preconditions / fixtures:** D's API key; client reads node Z at NestedSet snapshot `s0`.
- **Given** between read and write another authorized actor moves/deletes Z's parent so the
  client's positional assumption (`after`/`new_parent` revision) is stale
  **When** D submits `move_node {sheet:S, node:Z, new_parent:Y, after:<stale sibling>}`
  carrying the stale revision token
  **Then** **409** stale-move conflict; no `NODE_MOVED`; the client must re-read the snapshot
  and retry. This is the explicit "409 conflict on stale move" contract.
- **Covers:** `moveNode` stale-conflict 409; no event.

### API-161
- **Title:** Concurrent cell updates serialize via `version` (lost-update prevention)
- **Level:** integration
- **Preconditions / fixtures:** C's API key (two concurrent clients as C).
- **Given** both clients read X/`col:budget` at `version=v`
  **When** both `POST update_cell` with `value` and an `If-Match`/expected `version=v`
  **Then** exactly one succeeds (`version→v+1`, one `NODE_VALUE_UPDATED`); the other gets
  **409** stale-version and zero events. No lost update; the cell ends with one writer's value.
- **Covers:** `updateCell` optimistic concurrency 409; exactly-one `NODE_VALUE_UPDATED`.

### API-162
- **Title:** Idempotency key replays the same response without double mutation
- **Level:** integration
- **Preconditions / fixtures:** C's API key; an `Idempotency-Key` header supported by the
  capability transport.
- **Given** a first `update_cell {…,value:55}` with `Idempotency-Key:k1` that succeeds
  **When** the identical request with the same `Idempotency-Key:k1` is retried (e.g. client
  timeout/retry)
  **Then** the second call returns the **same** Outcome/event ref and does **not** emit a
  second `NODE_VALUE_UPDATED` nor increment `version` again. Safe client retries.
- **Covers:** `updateCell` idempotent retry; single `NODE_VALUE_UPDATED`.

### API-163
- **Title:** Owner-self policy via API — authorized owner still produces a CR
- **Level:** integration
- **Preconditions / fixtures:** a sheet variant `S'` with
  `settings.owners_must_use_change_requests=true`; C's API key (owns `col:budget`).
- **Given** the audit-trail policy is on
  **When** `POST /api/method/arbor.update_cell {sheet:S', node:X, column:col:budget, value:1}`
  as C
  **Then** despite C being authorized, `kind="suggested"`; a CR is created with C as its **own**
  `resolved_approver`; emits `CHANGE_PROPOSED` (not `NODE_VALUE_UPDATED` yet). The same flag
  governs web identically (parity). (PERMISSIONS §4.8.)
- **Covers:** `updateCell` under owner-self policy; `CHANGE_PROPOSED`.

---

## K. Event-emission contract from the API surface (one event per success)

### API-180
- **Title:** Each authorized API mutation emits exactly one Tree Event
- **Level:** integration
- **Preconditions / fixtures:** C's API key; event-stream spy.
- **Given** a single authorized `update_cell`
  **When** the call returns `kind="executed"`
  **Then** exactly **one** Tree Event (`NODE_VALUE_UPDATED`) is appended — not zero, not two —
  with the correct `actor`, `actor_type`, `sheet`, and `change_request=null`. (ARCHITECTURE
  §4.2 "exactly one Tree Event".)
- **Covers:** `updateCell`; `NODE_VALUE_UPDATED` cardinality.

### API-181
- **Title:** Each suggested API call emits exactly one CHANGE_PROPOSED and no mutation event
- **Level:** integration
- **Preconditions / fixtures:** A's API key; event-stream spy.
- **Given** an unauthorized `update_cell` by A
  **When** the call returns `kind="suggested"`
  **Then** exactly one `CHANGE_PROPOSED` (linked to the new CR) and **zero**
  `NODE_VALUE_UPDATED`.
- **Covers:** `updateCell`→CR; `CHANGE_PROPOSED` cardinality.

### API-182
- **Title:** API actor_type is never "agent"
- **Level:** unit
- **Preconditions / fixtures:** EXT and B keys; event-stream spy.
- **Given** mutations via REST by a human/external user
  **When** events are emitted
  **Then** `actor_type ∈ {human}` for these API callers (the `agent` type is reserved for the
  server-side Re-Act agent identity, even though it shares the same `execute_action`); never
  silently `system`.
- **Covers:** event `actor_type` provenance.
