# Arbor — Test-Case Catalog: Admin & Schema Co-Design

> **Surface:** Admin & Schema Co-Design.
> **Scope:** Sheet creation & lifecycle; collaborative schema design (propose/approve
> columns); assigning `structural_owner` and column owners/editors; granting/revoking
> branch delegations; managing subscriptions & webhook endpoints; viewing the event stream
> & accountability reports; role setup; admin-only gating.
> **Test-first:** written against the canonical spec before implementation.
> Authoritative refs: `ARCHITECTURE.md`, `PERMISSIONS.md`, `CAPABILITIES.md`,
> `DATA-MODEL.md`.

---

## Shared canonical fixtures (DRY — referenced, never redefined per test)

All cases below assume these canonical fixtures exist; do **not** invent bespoke worlds.

**Personas (Frappe Users):**
- **A** — root `structural_owner` of sample sheet `S`.
- **B** — column owner of `col:name` (is_label) and `col:notes`; **editor** on `col:status`.
- **C** — column owner of `col:status` and `col:budget`.
- **D** — grantee of an active Branch Grant on node **P2** (`scope=structure`).
- **E**, **F** — suggest-only users (no grants, no columns).
- **G** — sensitive subscriber; subscription has `requires_ack=true`.
- **EXT** — external system: an API-consumer User **and** a Webhook Endpoint subscriber.
- **ADMIN** — Arbor admin/System Manager (used for admin-only gating cases).
- **OUTSIDER** — authenticated User with **no** role/membership on sheet `S`.

**Sample sheet `S`** (status `active`, `structural_owner=A`):
```
root R               (struct authority: A)
├── P1               (struct authority: A)
│   └── X            (struct authority: A)
└── P2  ── Branch Grant: grantee=D, active   (struct authority: D)
    ├── Y            (struct authority: D, inherited)
    └── Z            (struct authority: D, inherited)
```
Columns: `col:name` (is_label, owner B), `col:status` (owner C, editors:[B]),
`col:budget` (owner C), `col:notes` (owner B).
Unless a case states otherwise, `S.settings.owners_must_use_change_requests = false`.

**Shared helpers assumed available:** `as(actor)` (acts under a User identity / API key),
`last_event(sheet)`, `events_of_type(sheet, type)`, `cr(name)`, `count_events(...)`,
`snapshot(sheet)`, mock `AuthProvider`, a webhook receiver stub with controllable HTTP
responses, and a clock/scheduler control for retry/backoff.

**Invariant anchors referenced repeatedly:** Surface parity (ARCHITECTURE §11);
exactly-one-event-per-successful-capability (§4.2); append-only Tree Event log; one
mutation site (`execute_action`); DRY dispatchers.

---

## A. Sheet creation & lifecycle

### ADMIN-001
- **Title:** Create a Tree Sheet with a structural owner (happy path)
- **Level:** integration
- **Preconditions / fixtures:** ADMIN authenticated; persona A exists.
- **Given** a request to create sheet titled "Q3 Plan" with `structural_owner=A`, `status=draft`
  **When** ADMIN creates the sheet
  **Then** a `Tree Sheet` row exists with `title="Q3 Plan"`, `structural_owner=A`, `status="draft"`,
  and `settings` defaulting to `{}` (or `owners_must_use_change_requests=false`);
  **And** the creator can subsequently resolve A as the Axis-1 fallback approver for root-level adds.
- **Covers:** Tree Sheet creation; `resolve_structural_approver(node=None) → structural_owner`.

### ADMIN-002
- **Title:** New sheet requires a `structural_owner`
- **Level:** unit
- **Preconditions / fixtures:** ADMIN authenticated.
- **Given** a create-sheet request omitting `structural_owner`
  **When** validated
  **Then** creation is rejected (required-field violation); no Tree Sheet row is persisted.
- **Covers:** Tree Sheet integrity (`structural_owner` required).

### ADMIN-003
- **Title:** Non-admin cannot create a sheet (admin-only gating)
- **Level:** integration
- **Preconditions / fixtures:** OUTSIDER authenticated (no admin role).
- **Given** OUTSIDER attempts to create a Tree Sheet
  **When** the request is processed
  **Then** it is denied with a permission error; no Tree Sheet row created; no Tree Event emitted.
- **Covers:** Admin-only gating on sheet creation.

### ADMIN-004
- **Title:** Sheet status transitions draft → active → archived
- **Level:** integration
- **Preconditions / fixtures:** sheet `S` in `draft`, ADMIN/owner A.
- **Given** `S.status=draft`
  **When** A activates then archives the sheet
  **Then** `status` is `active` then `archived`; transitions are persisted; only the
  `draft|active|archived` enum values are accepted (any other value rejected).
- **Covers:** Tree Sheet `status` Select enum.

### ADMIN-005
- **Title:** Mutations on an archived sheet are blocked
- **Level:** integration
- **Preconditions / fixtures:** sheet `S` with `status=archived`; A is owner.
- **Given** S is archived
  **When** A calls `addNode(parent=P1)` via `execute_action`
  **Then** the action is refused (sheet not writable); no `NODE_CREATED` event; no Tree Node row added.
- **Covers:** `addNode`; archived-sheet boundary (no event).

### ADMIN-006
- **Title:** `internalReset` is admin/system-only and never exposed to LLM
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; ADMIN and persona E available; `getLLMTools()` callable.
- **Given** the capability registry
  **When** `getLLMTools()` is enumerated **and** E calls `internalReset(sheet=S, confirm=true)`
  **Then** `internalReset` is **absent** from `getLLMTools()` output (`is_exposed_to_llm=false`);
  **And** E's call is denied (system/admin only); ADMIN's call with `confirm=true` succeeds;
  **And** `internalReset` produces **no** Tree Event (administrative, not on the stream).
- **Covers:** `internalReset`; getLLMTools filter; admin-only gating; event-stream exclusion.

### ADMIN-007
- **Title:** `internalReset` requires explicit `confirm:true`
- **Level:** unit
- **Preconditions / fixtures:** ADMIN; sheet `S`.
- **Given** `internalReset(sheet=S)` without `confirm` (or `confirm:false`)
  **When** params are validated against the schema
  **Then** validation fails (`confirm` is `const:true`); no reset performed.
- **Covers:** `internalReset` params_schema boundary.

---

## B. Role setup & admin gating

### ADMIN-008
- **Title:** Pluggable AuthProvider maps external identity to an Arbor User
- **Level:** unit
- **Preconditions / fixtures:** mock `AuthProvider` configured via `arbor.auth.provider_class`.
- **Given** an inbound auth request carrying claims for persona B
  **When** `authenticate()` / `map_identity(claims)` runs
  **Then** it resolves to the existing Arbor User B (a `UserIdentity`); no SSO-overlay SDK import is referenced by core.
- **Covers:** SSO seam (ARCHITECTURE §10); core open-source isolation.

### ADMIN-009
- **Title:** Active provider is selected by site config
- **Level:** unit
- **Preconditions / fixtures:** site config switching `arbor.auth.provider_class` between Local and generic OIDC.
- **Given** `provider_class=LocalAuthProvider` then `OIDCAuthProvider`
  **When** the auth layer initializes
  **Then** the correct provider instance is active each time; `get_login_url(redirect)` returns the provider-appropriate URL.
- **Covers:** AuthProvider selection seam.

### ADMIN-010
- **Title:** Role assignment is required to act on a sheet; OUTSIDER is read-gated
- **Level:** integration
- **Preconditions / fixtures:** OUTSIDER (no role on `S`); sheet `S`.
- **Given** OUTSIDER with no membership/role on `S`
  **When** OUTSIDER calls `getSheetSnapshot(sheet=S)`
  **Then** access is denied per the "reader can view sheet" ACL rule; no snapshot returned.
- **Covers:** `getSheetSnapshot` ACL ("reader can view sheet"); role gating.

### ADMIN-011
- **Title:** Admin-only capabilities are uniformly gated across web and REST
- **Level:** integration
- **Preconditions / fixtures:** OUTSIDER with API key; sheet `S`.
- **Given** OUTSIDER calls `POST /api/method/arbor.execute_action {action_id:"internalReset", ...}`
  **When** processed via REST
  **Then** the REST path enforces the **same** admin gate as web (surface parity); denied identically; no event.
- **Covers:** Surface parity (ARCHITECTURE §11) on admin gating; `internalReset`.

---

## C. Collaborative schema design — addColumn (Axis = meta)

### ADMIN-012
- **Title:** Sheet structural owner adds a column (happy path)
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; A is `structural_owner`.
- **Given** A calls `addColumn(sheet=S, field="priority", label="Priority", type="number", column_owner=C)`
  **When** `execute_action` resolves authority (meta → sheet `structural_owner`)
  **Then** A is authorized → a `Tree Column` row `(S, priority)` is created with `column_owner=C`;
  **And** exactly one `COLUMN_CONFIG_UPDATED` event is emitted; no Change Request created.
- **Covers:** `addColumn`; `COLUMN_CONFIG_UPDATED`.

### ADMIN-013
- **Title:** Non-owner proposing a column becomes a Change Request to the sheet owner
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; persona E (suggest-only).
- **Given** E calls `addColumn(sheet=S, field="risk", label="Risk", type="text")`
  **When** authority resolves (meta → sheet `structural_owner=A`)
  **Then** E ≠ A → **no** column created; a Change Request (`target_kind=column-schema`, `operation=add`)
  is created with `resolved_approver=A`, `payload` = original params;
  **And** exactly one `CHANGE_PROPOSED` event is emitted (not `COLUMN_CONFIG_UPDATED`).
- **Covers:** `addColumn` DENIED path; `CHANGE_PROPOSED`.

### ADMIN-014
- **Title:** Approving a proposed column replays addColumn as the owner
- **Level:** integration
- **Preconditions / fixtures:** the CR from ADMIN-013 exists (`resolved_approver=A`).
- **Given** A calls `approveChange(change_request=cr)`
  **When** the handler replays `addColumn(payload, actor=A)`
  **Then** the `Tree Column (S, risk)` is created; a `COLUMN_CONFIG_UPDATED` event is emitted
  and linked to `cr.resulting_event`; then a `CHANGE_APPROVED` event is emitted;
  **And** `cr.status=approved`, `decided_by=A`, `decided_at` set.
- **Covers:** `approveChange`; replay → `COLUMN_CONFIG_UPDATED` + `CHANGE_APPROVED`; `resulting_event` link.

### ADMIN-015
- **Title:** addColumn enforces type enum
- **Level:** unit
- **Preconditions / fixtures:** A; sheet `S`.
- **Given** `addColumn(..., type="boolean")` (not in the enum)
  **When** params validated against `addColumn` schema
  **Then** validation fails (`type` must be one of text|multiline-text|number|single-select-split|multi-select-split);
  no column created; no event.
- **Covers:** `addColumn` params_schema boundary.

### ADMIN-016
- **Title:** addColumn rejects duplicate `(sheet, field)`
- **Level:** integration
- **Preconditions / fixtures:** sheet `S` already has `col:status`.
- **Given** A calls `addColumn(sheet=S, field="status", label="Status 2", type="text")`
  **When** the handler attempts to persist
  **Then** the unique `(sheet, field)` constraint rejects it; no second column row; no `COLUMN_CONFIG_UPDATED`.
- **Covers:** Tree Column `(sheet, field)` uniqueness; `addColumn` conflict.

### ADMIN-017
- **Title:** Adding a second `is_label` column is rejected
- **Level:** integration
- **Preconditions / fixtures:** sheet `S` already has `col:name` with `is_label=1`.
- **Given** A calls `addColumn(sheet=S, field="alt_label", label="Alt", type="text", is_label=true)`
  **When** the handler persists
  **Then** the "exactly one `is_label=1` per sheet" constraint rejects it; no column created; no event.
- **Covers:** Tree Column single-`is_label` integrity; `addColumn` boundary.

### ADMIN-018
- **Title:** addColumn missing a required field fails validation
- **Level:** unit
- **Preconditions / fixtures:** A; sheet `S`.
- **Given** `addColumn(sheet=S, field="x", type="text")` (no `label`)
  **When** validated
  **Then** validation fails (required: sheet, field, label, type); no column; no event.
- **Covers:** `addColumn` required-params boundary.

---

## D. Collaborative schema design — updateColumn / deleteColumn (Axis = meta, column approvers)

### ADMIN-019
- **Title:** Column owner updates a column's schema (happy path)
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; C owns `col:budget`.
- **Given** C calls `updateColumn(sheet=S, column=col:budget, patch={"label":"Budget (USD)","width":160})`
  **When** authority resolves (meta update → `resolve_column_approvers(col:budget)={C}`)
  **Then** C ∈ approvers → the column row is patched; exactly one `COLUMN_CONFIG_UPDATED` emitted; no CR.
- **Covers:** `updateColumn`; `COLUMN_CONFIG_UPDATED`.

### ADMIN-020
- **Title:** Column editor may update column schema
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; `col:status` owner C, editor B.
- **Given** B (editor on `col:status`) calls `updateColumn(sheet=S, column=col:status, patch={"width":120})`
  **When** authority resolves (approvers = {C, B})
  **Then** B ∈ approvers → update executes; one `COLUMN_CONFIG_UPDATED`; no CR.
- **Covers:** `updateColumn` with editor authority (Axis 2 editors child table).

### ADMIN-021
- **Title:** Non-owner updateColumn becomes a Change Request to the column owner
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; E (no column authority).
- **Given** E calls `updateColumn(sheet=S, column=col:budget, patch={"label":"X"})`
  **When** authority resolves (approvers={C})
  **Then** E ∉ → CR created (`target_kind=column-schema`, `operation=update`, `resolved_approver=C`);
  one `CHANGE_PROPOSED`; column unchanged.
- **Covers:** `updateColumn` DENIED; `CHANGE_PROPOSED`.

### ADMIN-022
- **Title:** Column owner deletes a column (happy path)
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; C owns `col:budget`.
- **Given** C calls `deleteColumn(sheet=S, column=col:budget)`
  **When** authority resolves (approvers={C})
  **Then** the `Tree Column` row is removed; one `COLUMN_CONFIG_UPDATED` emitted; no CR.
- **Covers:** `deleteColumn`; `COLUMN_CONFIG_UPDATED`.

### ADMIN-023
- **Title:** Deleting the `is_label` column is rejected (boundary)
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; B owns `col:name` (is_label).
- **Given** B calls `deleteColumn(sheet=S, column=col:name)`
  **When** the handler attempts deletion
  **Then** it is rejected (a sheet must always retain exactly one `is_label` column); column retained; no event.
- **Covers:** `deleteColumn` boundary; single-`is_label` integrity.

### ADMIN-024
- **Title:** deleteColumn by non-approver becomes a Change Request
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; F (no authority).
- **Given** F calls `deleteColumn(sheet=S, column=col:notes)` (owner B)
  **When** authority resolves (approvers={B})
  **Then** CR (`column-schema`/`delete`, `resolved_approver=B`); one `CHANGE_PROPOSED`; column intact.
- **Covers:** `deleteColumn` DENIED; `CHANGE_PROPOSED`.

### ADMIN-025
- **Title:** Snapshot reflects schema changes (serializer consistency)
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; after ADMIN-012 added `col:priority`.
- **Given** `getSheetSnapshot(sheet=S)` is called
  **When** the shared serializer renders column config
  **Then** the snapshot column config includes `col:priority` with its `column_owner` and type;
  **And** the same shape is returned to web, REST, and agent callers.
- **Covers:** `getSheetSnapshot` (shared serializer); schema-as-data.

---

## E. Assigning structural owner & column ownership

### ADMIN-026
- **Title:** grantColumn sets column owner and editors (by current owner)
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; C owns `col:budget`.
- **Given** C calls `grantColumn(sheet=S, column=col:budget, column_owner=B, editors=[E])`
  **When** authority resolves (current `column_owner` C or sheet owner A)
  **Then** C is authorized → `col:budget.column_owner=B`, `editors=[E]`; one `COLUMN_CONFIG_UPDATED`; no CR.
- **Covers:** `grantColumn`; Axis-2 ownership reassignment; `COLUMN_CONFIG_UPDATED`.

### ADMIN-027
- **Title:** grantColumn allowed for sheet structural owner even if not column owner
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; A is `structural_owner`, does **not** own `col:status`.
- **Given** A calls `grantColumn(sheet=S, column=col:status, column_owner=C, editors=[B,E])`
  **When** authority resolves (sheet `structural_owner` is permitted)
  **Then** A is authorized → editors updated; one `COLUMN_CONFIG_UPDATED`; no CR.
- **Covers:** `grantColumn` dual-authority (current owner OR sheet owner).

### ADMIN-028
- **Title:** grantColumn by an unrelated user is denied / suggested
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; E (neither owner nor sheet owner).
- **Given** E calls `grantColumn(sheet=S, column=col:budget, column_owner=E)`
  **When** authority resolves (must be current owner C or sheet owner A)
  **Then** E ∉ authorized set → outcome is a Change Request routed to the column owner (C); column ownership unchanged.
- **Covers:** `grantColumn` DENIED (privilege-escalation guard); `CHANGE_PROPOSED`.

### ADMIN-029
- **Title:** grantColumn replacing owner changes who can subsequently approve cell edits
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; after ADMIN-026 `col:budget.column_owner=B`.
- **Given** the reassignment is in effect
  **When** B calls `updateCell(node=X, column=col:budget, value=10)` and C calls the same
  **Then** B (new owner) executes → `NODE_VALUE_UPDATED`; C (former owner, no longer in approver set) → Change Request to B.
- **Covers:** `grantColumn` downstream effect on `resolve_column_approvers`; Axis 2.

### ADMIN-030
- **Title:** Reassigning `structural_owner` reroutes the Axis-1 fallback approver
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; ADMIN/owner A; new owner candidate is F.
- **Given** the sheet's `structural_owner` is changed from A to F (admin action)
  **When** E calls `addNode(parent=P1)` (no grant on P1/root chain)
  **Then** `resolve_structural_approver(P1)` falls back to **F** now → CR routed to F (not A).
- **Covers:** Tree Sheet `structural_owner`; Axis-1 root fallback rerouting.

---

## F. Branch delegation — delegateBranch / revokeDelegation (Axis 1)

### ADMIN-031
- **Title:** Root owner delegates a sub-branch (happy path)
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; A owns structure; node P1 has no grant.
- **Given** A calls `delegateBranch(sheet=S, branch_root=P1, grantee=E)`
  **When** authority resolves (`resolve_structural_approver(P1)=A`)
  **Then** an active `Branch Grant (branch_root=P1, grantee=E, scope=structure, granted_by=A, active=1)` is created;
  one `DELEGATION_CHANGED` event; subsequent `addNode(parent=X)` resolves approver **E**.
- **Covers:** `delegateBranch`; `DELEGATION_CHANGED`; resolver re-route.

### ADMIN-032
- **Title:** Delegated owner may sub-delegate within their own branch
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; D holds grant on P2.
- **Given** D calls `delegateBranch(sheet=S, branch_root=Z, grantee=F)`
  **When** authority resolves (`resolve_structural_approver(Z)=D` via P2 grant)
  **Then** D is authorized → new active grant on Z (grantee F); one `DELEGATION_CHANGED`.
- **Covers:** `delegateBranch` sub-delegation (PERMISSIONS §3 persona D).

### ADMIN-033
- **Title:** Nearest-grant-wins after nested delegation
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; grant on P2→D and (from ADMIN-032) grant on Z→F.
- **Given** F adds a child under Z
  **When** `resolve_structural_approver(child_of_Z)` walks nearest-first
  **Then** the **Z** grant (F) wins over the ancestor **P2** grant (D); approver = F (executes).
- **Covers:** Resolver "nearest-grant-wins" invariant (PERMISSIONS §4.2).

### ADMIN-034
- **Title:** Delegation is scoped — D cannot act outside P2
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; D holds P2 grant only.
- **Given** D calls `addNode(parent=P1)`
  **When** `resolve_structural_approver(P1)` walks P1→root, no grant
  **Then** approver = A; D ≠ A → Change Request to A; no node added.
- **Covers:** `addNode` DENIED outside delegated scope; subtree-bounded delegation; `CHANGE_PROPOSED`.

### ADMIN-035
- **Title:** Non-owner delegateBranch becomes a Change Request
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; E (no structural authority over P1).
- **Given** E calls `delegateBranch(sheet=S, branch_root=P1, grantee=F)`
  **When** authority resolves (`resolve_structural_approver(P1)=A`)
  **Then** E ∉ → CR routed to A; no Branch Grant created; one `CHANGE_PROPOSED`.
- **Covers:** `delegateBranch` DENIED; `CHANGE_PROPOSED`.

### ADMIN-036
- **Title:** Revoke a delegation by the granting owner
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; active grant on P2 (grantee D, granted_by A).
- **Given** A calls `revokeDelegation(branch_grant=grant_P2)`
  **When** authority resolves (`granted_by` or ancestor structural owner)
  **Then** the grant's `active=0`; one `DELEGATION_CHANGED`; afterward `addNode(parent=Y)` resolves approver **A** (grant ignored).
- **Covers:** `revokeDelegation`; `DELEGATION_CHANGED`; resolver ignores inactive grants.

### ADMIN-037
- **Title:** Ancestor structural owner may revoke a sub-delegation they did not grant
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; grant on Z (granted_by D); A is root owner above.
- **Given** A calls `revokeDelegation(branch_grant=grant_Z)` (A is ancestor structural owner, not `granted_by`)
  **When** authority resolves (`granted_by` OR ancestor structural owner)
  **Then** A is authorized → grant deactivated; one `DELEGATION_CHANGED`.
- **Covers:** `revokeDelegation` ancestor-owner authority.

### ADMIN-038
- **Title:** Unauthorized revoke is denied / suggested
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; grant on P2 (granted_by A); E unrelated.
- **Given** E calls `revokeDelegation(branch_grant=grant_P2)`
  **When** authority resolves (neither `granted_by` nor ancestor owner)
  **Then** E is not authorized → no deactivation occurs (CR to the authorized approver); grant stays active.
- **Covers:** `revokeDelegation` DENIED.

### ADMIN-039
- **Title:** Revoking an already-inactive grant is idempotent
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; grant on P2 already `active=0`.
- **Given** A calls `revokeDelegation(branch_grant=grant_P2)` again
  **When** processed
  **Then** the grant remains `active=0` (no state change); behavior is idempotent — implementation either no-ops or emits at most one `DELEGATION_CHANGED`; resolver result unchanged.
- **Covers:** `revokeDelegation` idempotency boundary.

### ADMIN-040
- **Title:** Revoking a parent delegation does not auto-revoke nested sub-delegations
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; grants on P2→D and Z→F both active.
- **Given** A revokes the P2 grant
  **When** `resolve_structural_approver(child_of_Z)` is re-evaluated
  **Then** the Z→F grant is still active → approver = F (the nested grant survives independently);
  for a node directly under P2 but outside Z, approver falls back to A.
- **Covers:** `revokeDelegation` scoping; nearest-active-grant resolution after revoke.

### ADMIN-041
- **Title:** Stale-approver re-resolution: CR re-routed after delegation change
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; E filed a CR `addNode(parent=Y)` (then resolved_approver=D).
- **Given** before decision, A revokes the P2 grant (now Y's approver = A)
  **When** the CR is opened for decision
  **Then** `resolved_approver` is recomputed to **A** and the CR is re-routed; D may no longer decide it; A may.
- **Covers:** CR re-resolution at decision time (ARCHITECTURE §5; PERMISSIONS §4.7).

---

## G. Change Request lifecycle for schema/ownership proposals

### ADMIN-042
- **Title:** Only the resolved approver may approve a column-schema CR
- **Level:** integration
- **Preconditions / fixtures:** CR from ADMIN-013 (`resolved_approver=A`); E is requester.
- **Given** E calls `approveChange(change_request=cr)`
  **When** authority is checked (actor must == `resolved_approver` or column editor)
  **Then** E is rejected by ACL; CR remains `proposed`; no `CHANGE_APPROVED`, no column created.
- **Covers:** `approveChange` approver-only invariant (PERMISSIONS §4.7).

### ADMIN-043
- **Title:** Column editor may approve a column-schema CR
- **Level:** integration
- **Preconditions / fixtures:** a CR for `updateColumn(col:status)` with `resolved_approver=C`; B is editor on `col:status`.
- **Given** B calls `approveChange(change_request=cr)`
  **When** authority is checked (approver OR column editor)
  **Then** B is authorized → handler replays as approver; `COLUMN_CONFIG_UPDATED` + `CHANGE_APPROVED`; `decided_by=B`.
- **Covers:** `approveChange` editor-approves path.

### ADMIN-044
- **Title:** Reject a proposed column CR (no mutation)
- **Level:** integration
- **Preconditions / fixtures:** CR from ADMIN-013 (`resolved_approver=A`).
- **Given** A calls `rejectChange(change_request=cr, comment="dup")`
  **When** processed
  **Then** `cr.status=rejected`, `decided_by=A`, `decided_at` set; one `CHANGE_REJECTED`; no column created; no `COLUMN_CONFIG_UPDATED`.
- **Covers:** `rejectChange`; `CHANGE_REJECTED`.

### ADMIN-045
- **Title:** Requester withdraws their own proposal
- **Level:** integration
- **Preconditions / fixtures:** CR from ADMIN-013 (requester E, approver A).
- **Given** E calls `withdrawChange(change_request=cr)`
  **When** authority is checked (actor == `requester`)
  **Then** `cr.status=withdrawn`; emits `CHANGE_REJECTED` (status=withdrawn per registry); no mutation.
- **Covers:** `withdrawChange`; `CHANGE_REJECTED (withdrawn)`.

### ADMIN-046
- **Title:** Non-requester cannot withdraw
- **Level:** integration
- **Preconditions / fixtures:** CR from ADMIN-013 (requester E); F unrelated.
- **Given** F calls `withdrawChange(change_request=cr)`
  **When** authority is checked
  **Then** rejected (only requester may withdraw); CR stays `proposed`.
- **Covers:** `withdrawChange` requester-only invariant.

### ADMIN-047
- **Title:** Decisions on a terminal CR are rejected (idempotency / state machine)
- **Level:** integration
- **Preconditions / fixtures:** CR already `approved` (from ADMIN-014).
- **Given** A calls `approveChange` (or `rejectChange`/`withdrawChange`) on the same CR again
  **When** the state machine validates the transition
  **Then** the call is rejected (terminal state); no second mutation; no duplicate `COLUMN_CONFIG_UPDATED`; CR unchanged.
- **Covers:** CR state-machine terminality; idempotency (ARCHITECTURE §5).

### ADMIN-048
- **Title:** suggestChange creates a column-schema CR directly (always allowed)
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; E suggest-only.
- **Given** E calls `suggestChange(sheet=S, target_kind="column-schema", operation="add", payload={field:"tags",...})`
  **When** processed (always allowed)
  **Then** a CR is created with `resolved_approver=A` (sheet owner for column-add), `requester=E`; one `CHANGE_PROPOSED`.
- **Covers:** `suggestChange`; `CHANGE_PROPOSED`.

### ADMIN-049
- **Title:** Owner-self policy forces a CR for an authorized schema change
- **Level:** integration
- **Preconditions / fixtures:** sheet `S` with `settings.owners_must_use_change_requests=true`; A is sheet owner.
- **Given** A calls `addColumn(sheet=S, field="phase", label="Phase", type="text")`
  **When** `execute_action` resolves authority (A authorized) under owner-self policy
  **Then** instead of direct mutation, a CR is created with `resolved_approver=A` (self); one `CHANGE_PROPOSED`;
  **And** on A's `approveChange` the column is created with `COLUMN_CONFIG_UPDATED` + `CHANGE_APPROVED`.
- **Covers:** Owner-self policy (PERMISSIONS §1.2, §4.8); `addColumn`.

---

## H. Subscriptions

### ADMIN-050
- **Title:** Self-subscribe to a branch with required acknowledgement (persona G)
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; G self-subscribing.
- **Given** G calls `subscribe(scope="branch", target=P2, event_types=["CHANGE_APPROVED","NODE_DELETED"], delivery="in-app", requires_ack=true)`
  **When** processed (self-subscribe allowed)
  **Then** a `Subscription` row exists (`subscriber=G`, `subscriber_kind=user`, `requires_ack=1`); one `SUBSCRIPTION_CHANGED`.
- **Covers:** `subscribe`; `SUBSCRIPTION_CHANGED`.

### ADMIN-051
- **Title:** Subscribing another user requires admin
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; E attempts to subscribe F; ADMIN available.
- **Given** E calls `subscribe(subscriber=F, scope="sheet", target=S, ...)`
  **When** authority is checked (self-subscribe, or admin for others)
  **Then** E is denied; ADMIN performing the same call succeeds with a `SUBSCRIPTION_CHANGED`.
- **Covers:** `subscribe` others-require-admin; admin gating.

### ADMIN-052
- **Title:** subscribe validates scope and delivery enums
- **Level:** unit
- **Preconditions / fixtures:** G; sheet `S`.
- **Given** `subscribe(scope="row", target=S, event_types=["X"], delivery="sms")`
  **When** validated
  **Then** validation fails (`scope` ∈ sheet|branch|column; `delivery` ∈ in-app|email|webhook); no Subscription; no event.
- **Covers:** `subscribe` params_schema boundary.

### ADMIN-053
- **Title:** Unsubscribe by the subscription owner
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; G's subscription from ADMIN-050.
- **Given** G calls `unsubscribe(subscription=sub_G)`
  **When** authority is checked (owner of the subscription)
  **Then** the Subscription is removed; one `SUBSCRIPTION_CHANGED`.
- **Covers:** `unsubscribe`; `SUBSCRIPTION_CHANGED`.

### ADMIN-054
- **Title:** Unsubscribe by a non-owner is denied
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; G's subscription; E unrelated.
- **Given** E calls `unsubscribe(subscription=sub_G)`
  **When** authority checked
  **Then** denied (not the subscription owner); subscription intact.
- **Covers:** `unsubscribe` owner-only.

### ADMIN-055
- **Title:** Branch-scope subscription matches only descendants (NestedSet range)
- **Level:** integration
- **Preconditions / fixtures:** G subscribed `scope=branch, target=P2`; events on Z (in P2) and on X (in P1).
- **Given** a `NODE_DELETED` on Z and a `NODE_DELETED` on X occur
  **When** the notification dispatcher matches subscriptions
  **Then** a Notification for G is created for the Z event only; the X event does **not** match G's branch sub.
- **Covers:** Subscription branch-scope matching (NestedSet descendant range); dispatcher derivation.

### ADMIN-056
- **Title:** event_types filter — only subscribed types notify
- **Level:** integration
- **Preconditions / fixtures:** G subscribed to `["CHANGE_APPROVED","NODE_DELETED"]` on P2.
- **Given** a `NODE_VALUE_UPDATED` event occurs inside P2
  **When** the dispatcher evaluates G's subscription
  **Then** no Notification is created for G (type not subscribed).
- **Covers:** Subscription `event_types` filter.

---

## I. Notifications & accountability reports

### ADMIN-057
- **Title:** Approved deletion in a watched branch creates an ack-required notification for G
- **Level:** integration
- **Preconditions / fixtures:** G subscribed (ADMIN-050); D approves a CR deleting Z (in P2).
- **Given** D's `approveChange` produces `CHANGE_APPROVED` + `NODE_DELETED` for Z
  **When** the dispatcher runs over both events
  **Then** Notification row(s) for G with `requires_ack=1` and a `channel=in-app`; `delivered_at` set.
- **Covers:** Notification dispatcher fan-out; `CHANGE_APPROVED`/`NODE_DELETED` matching.

### ADMIN-058
- **Title:** Acknowledge a notification
- **Level:** integration
- **Preconditions / fixtures:** the ack-required Notification from ADMIN-057.
- **Given** G calls `acknowledge(notification=notif_G)`
  **When** authority is checked (recipient of the notification)
  **Then** an `Acknowledgement` row (`user=G`, `acked_at` set); **no** Tree Event emitted (per registry).
- **Covers:** `acknowledge`; no-event boundary.

### ADMIN-059
- **Title:** Non-recipient cannot acknowledge
- **Level:** integration
- **Preconditions / fixtures:** Notification for G; E unrelated.
- **Given** E calls `acknowledge(notification=notif_G)`
  **When** authority checked
  **Then** denied (not the recipient); no Acknowledgement row.
- **Covers:** `acknowledge` recipient-only.

### ADMIN-060
- **Title:** Acknowledge is idempotent ((notification, user) unique)
- **Level:** integration
- **Preconditions / fixtures:** G already acknowledged `notif_G` (ADMIN-058).
- **Given** G calls `acknowledge(notification=notif_G)` again
  **When** the unique `(notification, user)` constraint applies
  **Then** no duplicate Acknowledgement row; `acked_at` unchanged (idempotent).
- **Covers:** Acknowledgement `(notification, user)` uniqueness; idempotency.

### ADMIN-061
- **Title:** Accountability report "N notified / M acked"
- **Level:** integration
- **Preconditions / fixtures:** the `NODE_DELETED` event from ADMIN-057; G notified (requires_ack), then G acked (ADMIN-058).
- **Given** the report is computed for that Tree Event (or its CR)
  **When** aggregated
  **Then** `N notified = count(Notification where requires_ack=1)` = 1 and `M acked = count(Acknowledgement)` = 1 → "1 notified / 1 acked";
  **And** before G acks, the same report reads "1 notified / 0 acked".
- **Covers:** Accountability report aggregate (ARCHITECTURE §6; PERMISSIONS persona G).

### ADMIN-062
- **Title:** Viewing the event stream is admin/reader-gated and append-only
- **Level:** integration
- **Preconditions / fixtures:** sheet `S` with prior events; A (reader) and OUTSIDER.
- **Given** A queries `GET /api/resource/Tree Event?filters={sheet:S}` and attempts a write/delete on a Tree Event
  **When** processed
  **Then** A reads the ordered event list; any write/delete to Tree Event is denied (append-only; no exposed mutation);
  **And** OUTSIDER (no role on S) is denied the read.
- **Covers:** Tree Event read access; append-only integrity; role gating.

---

## J. Webhook endpoints & delivery

### ADMIN-063
- **Title:** Register a webhook endpoint for an external system (admin)
- **Level:** integration
- **Preconditions / fixtures:** ADMIN; sheet `S`; EXT external system.
- **Given** ADMIN registers a `Webhook Endpoint(url=…, secret=…, event_types=["NODE_VALUE_UPDATED","CHANGE_APPROVED"], scope=sheet, target=S, active=1)`
  **When** persisted
  **Then** the endpoint row exists with a stored signing `secret` and active flag.
- **Covers:** Webhook Endpoint registration; admin gating.

### ADMIN-064
- **Title:** Matching event triggers a signed delivery (HMAC)
- **Level:** integration
- **Preconditions / fixtures:** endpoint from ADMIN-063; webhook receiver returns 200.
- **Given** a `NODE_VALUE_UPDATED` event is emitted on `S`
  **When** the webhook dispatcher runs
  **Then** a `Webhook Delivery` POSTs the serialized event body with header `X-Arbor-Signature: sha256=<hmac(secret, raw_body)>`
  and `X-Arbor-Event-Id=<tree_event>`; receiver-side HMAC verification passes; delivery `status=delivered`, `attempts=1`.
- **Covers:** Webhook dispatcher; HMAC signing; idempotency header.

### ADMIN-065
- **Title:** Event type not subscribed is not delivered
- **Level:** integration
- **Preconditions / fixtures:** endpoint subscribed to `["NODE_VALUE_UPDATED"]` only.
- **Given** a `NODE_CREATED` event is emitted on `S`
  **When** the dispatcher evaluates the endpoint
  **Then** no Webhook Delivery row is created for that endpoint.
- **Covers:** Webhook event_types filter.

### ADMIN-066
- **Title:** Failed delivery retries with backoff until exhausted
- **Level:** integration
- **Preconditions / fixtures:** endpoint from ADMIN-063; receiver returns 500; clock controllable.
- **Given** a matching event and a receiver that always 500s
  **When** the dispatcher attempts delivery and the scheduler advances through the backoff schedule (0s,30s,5m,30m,2h,12h)
  **Then** `attempts` increments per try, `status` is `failed`/`pending` between tries with `next_retry_at` set, and becomes `exhausted` after 6 attempts; each attempt is appended to the log.
- **Covers:** Webhook retry/backoff; `Webhook Delivery` status lifecycle.

### ADMIN-067
- **Title:** Delivery marked delivered on first 2xx; no further retries
- **Level:** integration
- **Preconditions / fixtures:** endpoint; receiver returns 202.
- **Given** a matching event
  **When** the dispatcher delivers and receives 202
  **Then** `status=delivered`, `attempts=1`, `next_retry_at` cleared; no additional attempts scheduled.
- **Covers:** Webhook 2xx success path.

### ADMIN-068
- **Title:** Inactive endpoint receives no deliveries
- **Level:** integration
- **Preconditions / fixtures:** endpoint with `active=0`.
- **Given** a matching event is emitted
  **When** the dispatcher evaluates endpoints
  **Then** no Webhook Delivery is created for the inactive endpoint.
- **Covers:** Webhook Endpoint `active` flag boundary.

### ADMIN-069
- **Title:** Branch-scoped webhook matches only descendant-node events
- **Level:** integration
- **Preconditions / fixtures:** endpoint `scope=branch, target=P2, event_types=[NODE_DELETED]`.
- **Given** `NODE_DELETED` on Z (in P2) and on X (in P1)
  **When** the dispatcher matches
  **Then** delivery is created for the Z event only (NestedSet descendant range), not the X event.
- **Covers:** Webhook branch-scope matching (DRY with notification matching).

### ADMIN-070
- **Title:** Delivery log is queryable per endpoint for audit
- **Level:** integration
- **Preconditions / fixtures:** endpoint from ADMIN-063 with several deliveries (mixed delivered/exhausted).
- **Given** an audit query for the endpoint's deliveries
  **When** filtered by endpoint
  **Then** all attempt rows are returned with `status`, `attempts`, `last_response`, `signature`, `tree_event`.
- **Covers:** Webhook Delivery audit log queryability.

---

## K. EXT — external system as API consumer (surface parity)

### ADMIN-071
- **Title:** EXT API write funnels through the same two-axis ACL
- **Level:** e2e
- **Preconditions / fixtures:** EXT has an API key but owns no columns on `S`.
- **Given** EXT calls `POST /api/method/arbor.update_cell {sheet:S, node:X, column:col:budget, value:5}`
  **When** authority resolves (Axis 2, approvers={C})
  **Then** EXT ∉ → a Change Request to C; one `CHANGE_PROPOSED`; cell unchanged — identical to a human non-owner.
- **Covers:** Surface parity; `updateCell` DENIED via REST; `CHANGE_PROPOSED`.

### ADMIN-072
- **Title:** Surface parity — same capability/actor yields identical decision across web, REST, agent
- **Level:** e2e
- **Preconditions / fixtures:** sheet `S`; B is `col:notes` owner; agent runs as its own User (no authority).
- **Given** the same `updateCell(node=X, column=col:notes, value="v")` is invoked (1) via web `execute_action` as B, (2) via `POST /api/method/arbor.update_cell` as B, (3) via the agent tool as the agent user
  **When** each runs
  **Then** (1) and (2) both execute → `NODE_VALUE_UPDATED` (identical authority + event shape); (3) the agent (no authority) produces a Change Request → `CHANGE_PROPOSED`;
  **And** the web/REST executed events are byte-for-byte equivalent in `type` and payload shape.
- **Covers:** Surface parity invariant (ARCHITECTURE §11); agent-as-human-under-ACL (PERMISSIONS §4.5).

---

## L. Cross-cutting integrity & event invariants

### ADMIN-073
- **Title:** Exactly one Tree Event per successful capability
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; A authorized for `addColumn`.
- **Given** A calls a single authorized `addColumn`
  **When** it executes
  **Then** exactly **one** `COLUMN_CONFIG_UPDATED` event is appended (count delta = 1); no extra events.
- **Covers:** One-event-per-capability invariant (ARCHITECTURE §4.2).

### ADMIN-074
- **Title:** Denied admin capability emits no mutation event (only CHANGE_PROPOSED when applicable)
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; E unauthorized for `delegateBranch`.
- **Given** E's `delegateBranch` resolves to a CR
  **When** processed
  **Then** zero `DELEGATION_CHANGED`; exactly one `CHANGE_PROPOSED`; Branch Grant table unchanged.
- **Covers:** DENIED-path event discipline.

### ADMIN-075
- **Title:** All admin Tree Events carry correct actor_type
- **Level:** integration
- **Preconditions / fixtures:** A (human) adds a column; agent user proposes a column.
- **Given** A's authorized `addColumn` and the agent's unauthorized `addColumn`
  **When** events are emitted
  **Then** A's `COLUMN_CONFIG_UPDATED` has `actor_type=human, actor=A`; the agent's `CHANGE_PROPOSED` has `actor_type=agent, actor=<agent user>`.
- **Covers:** Tree Event `actor_type` correctness; agent identity.

### ADMIN-076
- **Title:** Schema/ownership writes are blocked on raw REST DocType write
- **Level:** integration
- **Preconditions / fixtures:** A with API key; sheet `S`.
- **Given** A attempts `PUT /api/resource/Tree Column/<name>` (raw Frappe write bypassing `execute_action`)
  **When** Frappe permissions evaluate
  **Then** the direct write to the governed DocType is denied; the column is unchanged; no event;
  **And** the same change via `arbor.update_column` (authorized) succeeds with one `COLUMN_CONFIG_UPDATED`.
- **Covers:** "governed DocTypes: write only via execute_action" (DATA-MODEL §13); one-mutation-site invariant.
