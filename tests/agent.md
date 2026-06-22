# Arbor — Test-Case Catalog: AI Re-Act Agent surface

> **Test-first catalog** for the server-side Re-Act agent
> (`arbor.agent.react` + `arbor.agent.provider` + `arbor.agent.tools`).
> Written against the locked spec: [`ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §8 (agent),
> §4 (capability hub), §11 (surface parity); [`CAPABILITIES.md`](../docs/CAPABILITIES.md)
> (`getLLMTools`, `is_exposed_to_llm`); [`PERMISSIONS.md`](../docs/PERMISSIONS.md)
> (two-axis ACL, invariant #5 "agent = human under ACL").
>
> **Scope of THIS surface:** the Re-Act loop (Thought/Action/Observation transcript),
> the LiteLLM provider adapter (mocked — no real network), `getLLMTools()` tool exposure
> and filtering, multi-step orchestration, and the governance property that the agent acts
> under its OWN Frappe User identity so unauthorized agent actions become Change Requests.
> Out of scope (covered by sibling catalogs): the ACL resolver internals, the executor,
> the notification/webhook dispatchers — referenced here only where the agent must produce
> the identical outcome (surface parity).

---

## Shared fixtures (canonical — DO NOT redefine per test)

All cases reuse the canonical world from [`PERMISSIONS.md`](../docs/PERMISSIONS.md) §2:

- **Sheet `S`** (`structural_owner = A`, `status = active`,
  `settings.owners_must_use_change_requests = false` unless a case states otherwise).
- **Tree (NestedSet):** `R → {P1 → X, P2 → {Y, Z}}`. Active **Branch Grant** on `P2`,
  `grantee = D`, `scope = structure`.
- **Columns:** `col:name` (`is_label`, owner **B**), `col:status` (owner **C**, editors `[B]`),
  `col:budget` (owner **C**), `col:notes` (owner **B**).
- **Personas (each a Frappe User):** **A** root structural owner; **B**, **C** column
  owners; **D** delegated P2 owner; **E**, **F** suggest-only; **G** sensitive subscriber
  (`requires_ack`); **EXT** external system.
- **`AGENT`** — a dedicated Frappe User that the Re-Act agent acts as. By default `AGENT`
  holds **no** grants and owns **no** columns (a "suggest-only" identity), so its
  unauthorized actions become Change Requests. Cases that need agent authority explicitly
  state which persona's authority `AGENT` is configured to hold (e.g. "run agent as
  identity = D" = the agent's User is the grantee of the P2 Branch Grant).
- **`MockProvider`** — an in-process `arbor.agent.provider` adapter implementing the
  LiteLLM provider interface. It is **scripted**: the test supplies an ordered list of
  provider responses (each either a `tool_call` with `{name, arguments}` or a final
  assistant text). No real network. A `RecordingProvider` variant additionally captures the
  exact tool-schema list and message history it was handed.
- Helper `run_agent(message, identity=AGENT, provider=MockProvider(script))` invokes
  `arbor.agent.react` and returns the full transcript (ordered Thought/Action/Observation/
  Final steps), the list of `Outcome`s, and all emitted Tree Events.

> Per-test "Preconditions" list only **deltas** from this baseline.

---

## A. Tool exposure & `getLLMTools()` filtering

### AGENT-001 — getLLMTools returns exactly the LLM-exposed capabilities
- **Level:** unit
- **Preconditions:** canonical registry loaded.
- **Given** the capability registry from `CAPABILITIES.md`.
- **When** `arbor.agent.tools.get_llm_tools()` is called.
- **Then** the returned tool names equal the set of registry capabilities with
  `is_exposed_to_llm == True`: `getSheetSnapshot, addNode, updateCell, moveNode,
  deleteNode, addColumn, updateColumn, deleteColumn, suggestChange, approveChange,
  rejectChange, withdrawChange, subscribe, unsubscribe, acknowledge, delegateBranch,
  revokeDelegation, grantColumn` — **and** `internalReset` is **absent**.
- **Covers:** `getLLMTools` contract; `is_exposed_to_llm` filter.

### AGENT-002 — internalReset is never exposed as a tool
- **Level:** unit
- **Given** the registry where `internalReset.is_exposed_to_llm == False`.
- **When** `get_llm_tools()` is called.
- **Then** no tool definition has `name == "internalReset"`; asserting on the raw
  registry confirms `internalReset` exists but is filtered out (i.e. the filter, not a
  missing record, hides it).
- **Covers:** `internalReset` (hidden); `getLLMTools` filter.

### AGENT-003 — tool definitions are rendered from each capability's params_schema
- **Level:** unit
- **When** `get_llm_tools()` runs.
- **Then** for each exposed capability the emitted LiteLLM tool def carries `name == cap.id`,
  a non-empty `description` derived from `cap.name`, and a `parameters` JSON-schema deep-equal
  to `cap.params_schema` (verified for `updateCell`: required `[sheet,node,column,value]`;
  and `addNode`: required `[sheet,parent]`).
- **Covers:** `getLLMTools` contract (schema fidelity).

### AGENT-004 — agent attempting a hidden/unknown tool is rejected, not executed
- **Level:** integration
- **Preconditions:** `MockProvider` scripted to emit a `tool_call` for `internalReset`
  (simulating a hallucinated/forbidden call) with `{sheet:S, confirm:true}`.
- **When** `run_agent("reset the sheet")` runs.
- **Then** the loop does **not** dispatch `internalReset` to `execute_action`; the step is
  recorded as an Observation of kind `tool_error` ("unknown/unavailable tool"); **no**
  Tree Event is emitted; the agent either re-plans or produces a Final explaining it cannot.
- **Covers:** `internalReset` (hidden); Re-Act loop guard. Emits: **none**.

---

## B. Re-Act loop transcript & read-only flow

### AGENT-005 — single read: snapshot then natural-language answer
- **Level:** integration
- **Preconditions:** `MockProvider` script = [tool_call `getSheetSnapshot{sheet:S}`,
  final text "There are 5 nodes."]. `AGENT` may read `S`.
- **When** `run_agent("how many nodes are in S?")`.
- **Then** transcript is ordered exactly: Thought → Action(`getSheetSnapshot`) →
  Observation(snapshot via the **shared** `get_sheet_snapshot` serializer) → Final(text).
  The Action invoked `execute_action("getSheetSnapshot", {sheet:S}, actor=AGENT)`. **No**
  Tree Event is emitted (read-only). The Observation payload shape equals what web/API get.
- **Covers:** `getSheetSnapshot`; shared serializer. Emits: **none** (read).

### AGENT-006 — transcript records thought/action/observation tuples in order
- **Level:** unit
- **Preconditions:** `MockProvider` script with 2 tool_calls then a final.
- **When** the loop runs.
- **Then** the returned transcript is an ordered list where every Action is immediately
  followed by its Observation; each Action records `{tool, arguments}` and each Observation
  records the `Outcome` (or read result); the final entry is `kind == "final"` carrying the
  assistant text. Re-Act ordering invariant: count(Action) == count(Observation).
- **Covers:** Re-Act loop transcript structure.

### AGENT-007 — observation is fed back to the provider on the next turn
- **Level:** integration
- **Preconditions:** `RecordingProvider` script = [tool_call `getSheetSnapshot`, final].
- **When** the loop runs.
- **Then** the message history handed to the provider on the **second** call contains the
  tool result (the snapshot) as a tool/observation message keyed to the first tool_call id;
  the agent's reasoning is thus conditioned on real observations, not invented ones.
- **Covers:** Re-Act loop (observation feedback).

### AGENT-008 — loop terminates on final assistant message (no tool_call)
- **Level:** unit
- **Preconditions:** `MockProvider` script = [final text only].
- **When** the loop runs.
- **Then** the loop makes exactly one provider call, emits a Final step, performs zero
  Actions, and returns.
- **Covers:** Re-Act loop termination.

### AGENT-009 — max-iteration guard halts a non-terminating provider
- **Level:** integration
- **Preconditions:** `MockProvider` scripted to return a `getSheetSnapshot` tool_call on
  **every** turn (never finalizes). Loop `max_steps` configured (e.g. 8).
- **When** the loop runs.
- **Then** the loop stops after `max_steps` Actions, emits a Final step flagged
  `terminated_by="max_steps"`, and never exceeds the cap. No partial mutation is left
  uncommitted beyond what each authorized Action already did.
- **Covers:** Re-Act loop boundary (runaway guard).

---

## C. Happy-path mutations (agent HAS authority)

### AGENT-010 — agent with column authority updates a cell directly
- **Level:** integration
- **Preconditions:** run agent as **identity = C** (agent User owns `col:budget`).
  `MockProvider` script = [tool_call `updateCell{sheet:S,node:Y,column:col:budget,value:5000}`,
  final].
- **When** `run_agent("set Y's budget to 5000")`.
- **Then** Axis-2 resolves approvers `{C}`, actor==C → `execute_action` **executes** the
  handler; Observation `Outcome.kind == "executed"`; exactly one `NODE_VALUE_UPDATED` event
  with `actor=C-as-agent`, `actor_type == "agent"`, `change_request == null`,
  payload `{node:Y, column:col:budget, old_value, new_value:5000, version++}`.
- **Covers:** `updateCell`. Emits: **NODE_VALUE_UPDATED**.

### AGENT-011 — agent with structural authority adds a node directly
- **Level:** integration
- **Preconditions:** run agent as **identity = D** (grantee on P2). Script = [tool_call
  `addNode{sheet:S,parent:Y}`, final].
- **When** `run_agent("add a child under Y")`.
- **Then** Axis-1 walk `Y→P2(grant D)` resolves approver **D** == actor → executes; one
  `NODE_CREATED` event, `actor_type == "agent"`, `change_request == null`.
- **Covers:** `addNode`. Emits: **NODE_CREATED**.

### AGENT-012 — agent actor_type is "agent" on every emitted event
- **Level:** integration
- **Preconditions:** identity = C; script performs one authorized `updateCell`.
- **Then** the emitted Tree Event has `actor_type == "agent"` and `actor == <agent User>`
  (the agent's own identity, not a service/system account, not the human who chatted).
- **Covers:** agent identity stamping. Emits: **NODE_VALUE_UPDATED**.

---

## D. Permission-DENIED → Change Request (the governance keystone)

### AGENT-013 — suggest-only agent's cell edit becomes a Change Request
- **Level:** integration
- **Preconditions:** identity = `AGENT` (no columns). Script = [tool_call
  `updateCell{sheet:S,node:X,column:col:budget,value:9000}`, final summarizing].
- **When** `run_agent("set X budget to 9000")`.
- **Then** Axis-2 approvers `{C}`, AGENT ∉ → `execute_action` creates a **Change Request**
  (`target_kind=cell-value, operation=update, payload=params, requester=AGENT,
  resolved_approver=C`), emits **CHANGE_PROPOSED** (not NODE_VALUE_UPDATED); Observation
  `Outcome.kind == "suggested"`; the cell value is **unchanged**. Final text mentions a CR
  was filed for C.
- **Covers:** `updateCell` (unauthorized path). Emits: **CHANGE_PROPOSED**.

### AGENT-014 — suggest-only agent's structural add becomes a Change Request to A
- **Level:** integration
- **Preconditions:** identity = `AGENT`. Script = [tool_call `addNode{sheet:S,parent:P1}`,
  final].
- **Then** Axis-1 walk `P1→root` no grant → approver **A**; AGENT ≠ A → Change Request
  (`node-structure/add`, `resolved_approver=A`), **CHANGE_PROPOSED** emitted, no
  `NODE_CREATED`; no node row created.
- **Covers:** `addNode` (unauthorized). Emits: **CHANGE_PROPOSED**.

### AGENT-015 — agent cannot escalate privilege by being an agent
- **Level:** integration
- **Preconditions:** identity = `AGENT`. Script = [tool_call `deleteNode{sheet:S,node:Z}`].
- **Then** the outcome is byte-for-byte the same authority decision a human suggest-only
  user (E/F) would get: Axis-1 walk `Z→P2(D)` → approver **D**; AGENT ≠ D → Change Request
  to D, **CHANGE_PROPOSED**, `Z` not deleted. Assert there is **no** code path where
  `actor_type=="agent"` bypasses `resolve_authority`.
- **Covers:** `deleteNode` (unauthorized); PERMISSIONS invariant #5. Emits: **CHANGE_PROPOSED**.

### AGENT-016 — agent explicit suggestChange always creates a CR
- **Level:** integration
- **Preconditions:** identity = C (would otherwise be authorized for `col:budget`).
  Script = [tool_call `suggestChange{sheet:S, target_kind:cell-value, operation:update,
  payload:{node:Y,column:col:budget,value:1}}`, final].
- **When** the agent deliberately suggests rather than executes.
- **Then** a Change Request is created regardless of authority (`suggestChange` is always
  allowed), **CHANGE_PROPOSED** emitted, no `NODE_VALUE_UPDATED`. Confirms the agent can
  intentionally route for review even when it could act.
- **Covers:** `suggestChange`. Emits: **CHANGE_PROPOSED**.

---

## E. Axis independence via the agent

### AGENT-017 — column-owner agent edits a cell inside a branch it does NOT structurally own
- **Level:** integration
- **Preconditions:** identity = B (owns `col:name`). Script = [tool_call
  `updateCell{sheet:S,node:Z,column:col:name,value:"renamed"}`, final]. `Z` is in **D's**
  P2 subtree.
- **Then** Axis-2 only: approvers `{B}`, B∈ → **executes** despite Z being in D's branch;
  `NODE_VALUE_UPDATED`. Demonstrates Axis 2 ignores structure.
- **Covers:** `updateCell`; axis independence (PERMISSIONS inv #1). Emits: **NODE_VALUE_UPDATED**.

### AGENT-018 — structural-owner agent editing a non-owned column is suggested
- **Level:** integration
- **Preconditions:** identity = D (P2 structural owner). Script = [tool_call
  `updateCell{sheet:S,node:Y,column:col:status,value:"done"}`, final].
- **Then** Axis-2 approvers `{C, B(editor)}`; D ∉ → Change Request to C (column owner),
  **CHANGE_PROPOSED**; no value change. D's structural authority over P2 grants no value
  authority.
- **Covers:** `updateCell` (unauthorized); axis independence (PERMISSIONS inv #1).
  Emits: **CHANGE_PROPOSED**.

### AGENT-019 — editing the label column is governed by Axis 2, not Axis 1
- **Level:** integration
- **Preconditions:** identity = D (owns P2 structurally, owns no column). Script =
  [tool_call `updateCell{sheet:S,node:Z,column:col:name,value:"Z2"}`, final]. `col:name`
  is `is_label`, owner B.
- **Then** even though D created/owns the structure of Z, the label edit resolves Axis-2
  `{B}`; D ∉ → Change Request to B, **CHANGE_PROPOSED**. Confirms label-as-cell rule.
- **Covers:** `updateCell` on `is_label` column. Emits: **CHANGE_PROPOSED**.

---

## F. Multi-step orchestration

### AGENT-020 — "find all budget>10k and move under a new High Cost folder" (mixed outcomes)
- **Level:** e2e
- **Preconditions:** identity = `AGENT` (suggest-only). Cell values set so that `X` and `Z`
  have `col:budget > 10000`, others not. Script (realistic Re-Act):
  1. tool_call `getSheetSnapshot{sheet:S}` → Observation snapshot
  2. tool_call `addNode{sheet:S,parent:R, values:{name:"High Cost"}}` → creates folder `H`
  3. tool_call `moveNode{sheet:S,node:X,new_parent:H}`
  4. tool_call `moveNode{sheet:S,node:Z,new_parent:H}`
  5. final summary
- **When** `run_agent("find all nodes with budget>10k and move them under a new High Cost folder")`.
- **Then** because `AGENT` lacks authority everywhere here: step 2 (addNode under R, approver
  A) → CR to A; step 3 (move X: src A, dest H/new — its parent R's approver A) → CR to A;
  step 4 (move Z: src D for P2, dest A) → since actor is neither src nor dest approver and
  src≠dest, Change Request routed to **dest (A)** with **src (D) as co-approver** in
  `payload.co_approvers`. Each Action's Observation is `kind=="suggested"`; three+
  **CHANGE_PROPOSED** events; **no** structural mutation occurs. Final summary states the
  count of CRs filed and to whom (A, and A+D co-approver).
- **Covers:** `getSheetSnapshot`, `addNode`, `moveNode` (unauthorized, multi-step).
  Emits: **CHANGE_PROPOSED** ×N.

### AGENT-021 — same orchestration with an authorized agent executes end-to-end
- **Level:** e2e
- **Preconditions:** identity = A (root structural owner). Same script as AGENT-020.
- **Then** addNode under R (approver A) executes → `NODE_CREATED`; move X (src A, dest A)
  executes → `NODE_MOVED`; move Z (src **D**, dest A) — A is not src approver → this single
  step is **suggested** to A with co-approver D (A is dest but lacks src authority), so a CR
  is filed for Z even though A is otherwise authorized. Transcript shows 2 executed + 1
  suggested. Demonstrates per-step authority within one orchestration.
- **Covers:** `addNode`, `moveNode`. Emits: **NODE_CREATED**, **NODE_MOVED**, **CHANGE_PROPOSED**.

### AGENT-022 — orchestration plans off the observed snapshot, not stale assumptions
- **Level:** e2e
- **Preconditions:** identity = C (owns `col:budget`). Snapshot shows only `Y` has
  budget>10k. Script: getSheetSnapshot → then a single `updateCell` only on `Y`.
- **Then** the agent issues exactly one mutating Action (on Y), proving it conditioned the
  plan on the Observation. Asserts the loop did not act on nodes absent from the snapshot.
- **Covers:** `getSheetSnapshot`, `updateCell`; Re-Act observation grounding.
  Emits: **NODE_VALUE_UPDATED**.

### AGENT-023 — partial failure mid-orchestration does not roll back prior authorized steps
- **Level:** e2e
- **Preconditions:** identity = C. Script: updateCell(Y,col:budget) [authorized] →
  updateCell(Y,col:name) [C not owner → suggested] → final.
- **Then** step 1 emits `NODE_VALUE_UPDATED` and persists; step 2 yields a Change Request
  (CHANGE_PROPOSED) and persists nothing; the first mutation is **not** rolled back. Each
  capability call is independently committed (no implicit transaction across Actions).
- **Covers:** `updateCell` (mixed). Emits: **NODE_VALUE_UPDATED**, **CHANGE_PROPOSED**.

---

## G. Delegation edge cases through the agent

### AGENT-024 — nearest-grant-wins resolves agent action to sub-delegate
- **Level:** integration
- **Preconditions:** in addition to the P2→D grant, an active Branch Grant on `Z`
  (`grantee = D2`). Identity = `AGENT`. Script = [tool_call `addNode{sheet:S,parent:Z}`].
- **Then** Axis-1 walk from a child-of-Z is `Z(grant D2) → P2(grant D) → root`; nearest
  active grant = **D2** → Change Request routed to **D2**, not D. **CHANGE_PROPOSED**.
- **Covers:** `addNode` (unauthorized); PERMISSIONS inv #2 (nearest grant). Emits: **CHANGE_PROPOSED**.

### AGENT-025 — agent acting as delegate D adds within P2 and executes
- **Level:** integration
- **Preconditions:** identity = D. Script = [tool_call `addNode{sheet:S,parent:Z}`, final].
- **Then** walk `Z→P2(grant D)` → approver D == actor → executes; `NODE_CREATED`,
  `actor_type=="agent"`.
- **Covers:** `addNode`. Emits: **NODE_CREATED**.

### AGENT-026 — agent as delegate D acting OUTSIDE its branch is suggested
- **Level:** integration
- **Preconditions:** identity = D. Script = [tool_call `addNode{sheet:S,parent:P1}`].
- **Then** walk `P1→root`, no grant → approver A; D ≠ A → Change Request to A,
  **CHANGE_PROPOSED**. Delegation is strictly the P2 NestedSet range.
- **Covers:** `addNode` (unauthorized); delegation scoping. Emits: **CHANGE_PROPOSED**.

### AGENT-027 — agent sub-delegating its own branch executes DELEGATION_CHANGED
- **Level:** integration
- **Preconditions:** identity = D. Script = [tool_call
  `delegateBranch{sheet:S,branch_root:Z,grantee:F}`, final].
- **Then** `resolve_structural_approver(Z)` = D == actor → executes; one **DELEGATION_CHANGED**
  event; a new active Branch Grant row on Z with grantee F exists.
- **Covers:** `delegateBranch`. Emits: **DELEGATION_CHANGED**.

### AGENT-028 — agent revoking a delegation it does not own is suggested
- **Level:** integration
- **Preconditions:** identity = `AGENT`. An existing Branch Grant `BG_P2` (granted_by A,
  grantee D). Script = [tool_call `revokeDelegation{branch_grant:BG_P2}`].
- **Then** revoke authority = `granted_by` (A) or ancestor structural owner (A); AGENT is
  neither → Change Request to A, **CHANGE_PROPOSED**; grant stays active.
- **Covers:** `revokeDelegation` (unauthorized). Emits: **CHANGE_PROPOSED**.

### AGENT-029 — agent move where src≠dest authority routes to dest with co-approver
- **Level:** integration
- **Preconditions:** identity = D (owns P2 structurally). Script = [tool_call
  `moveNode{sheet:S,node:Y,new_parent:P1}`]. src parent = P2 (approver D), dest parent =
  P1 (approver A).
- **Then** move requires authority over **both** ends; D is src approver but not dest → not
  authorized → Change Request routed to **dest A** with **src D in `payload.co_approvers`**.
  **CHANGE_PROPOSED**; Y not moved.
- **Covers:** `moveNode` (unauthorized, dual-end); PERMISSIONS inv #4. Emits: **CHANGE_PROPOSED**.

---

## H. Approval / lifecycle capabilities driven by the agent

### AGENT-030 — agent approving a CR it is the resolved_approver of replays the mutation
- **Level:** integration
- **Preconditions:** identity = C. A pending CR `CR1` (cell-value update on `col:budget`,
  `resolved_approver = C`). Script = [tool_call `approveChange{change_request:CR1}`, final].
- **Then** C == resolved_approver → `approveChange` replays `cap.handler(payload, actor=C)`,
  emits the real **NODE_VALUE_UPDATED** then **CHANGE_APPROVED**, sets `CR1.status=approved`,
  links `CR1.resulting_event`. Two events in order.
- **Covers:** `approveChange`. Emits: **NODE_VALUE_UPDATED**, **CHANGE_APPROVED**.

### AGENT-031 — agent approving a CR it is NOT approver of is denied
- **Level:** integration
- **Preconditions:** identity = `AGENT`. Pending CR `CR2` with `resolved_approver = C`.
  Script = [tool_call `approveChange{change_request:CR2}`].
- **Then** actor AGENT ≠ resolved_approver C and AGENT ∉ column editors → ACL denies;
  Observation records an authorization failure; CR2 stays `proposed`; **no**
  CHANGE_APPROVED and **no** replayed mutation event. (Per CAPABILITIES, `approveChange`
  authority is `actor == resolved_approver`, so a denied approve does not itself spawn a CR.)
- **Covers:** `approveChange` (denied); PERMISSIONS inv #7. Emits: **none**.

### AGENT-032 — agent withdrawing another user's CR is denied
- **Level:** integration
- **Preconditions:** identity = `AGENT`. CR `CR3` with `requester = E`. Script =
  [tool_call `withdrawChange{change_request:CR3}`].
- **Then** withdraw authority = requester only; AGENT ≠ E → denied; CR3 stays `proposed`;
  no event.
- **Covers:** `withdrawChange` (denied); PERMISSIONS inv #7. Emits: **none**.

### AGENT-033 — agent withdrawing its OWN CR succeeds
- **Level:** integration
- **Preconditions:** identity = `AGENT`. CR `CR4` with `requester = AGENT` (filed by an
  earlier agent suggestion). Script = [tool_call `withdrawChange{change_request:CR4}`, final].
- **Then** AGENT == requester → withdraw succeeds; `CR4.status = withdrawn`; emits
  **CHANGE_REJECTED** (status=withdrawn per CAPABILITIES).
- **Covers:** `withdrawChange`. Emits: **CHANGE_REJECTED**.

### AGENT-034 — agent acknowledges a notification addressed to it
- **Level:** integration
- **Preconditions:** identity = `AGENT`, which is the `recipient` of notification `N1`.
  Script = [tool_call `acknowledge{notification:N1}`, final].
- **Then** AGENT == recipient → an Acknowledgement row is created with `acked_at` set; **no**
  Tree Event (acknowledge emits none per CAPABILITIES).
- **Covers:** `acknowledge`. Emits: **none**.

---

## I. Provider adapter / provider-swap (mock LLM)

### AGENT-035 — provider-swap: identical loop behavior across mock adapters
- **Level:** integration
- **Preconditions:** two adapters `MockClaude` and `MockGemini`, both scripted with the
  **same logical plan** (snapshot → updateCell → final) using each provider's native
  tool-call wire shape. Identity = C.
- **When** `run_agent(...)` is run once per adapter, selected via
  `arbor.agent.provider` config (`provider_class`).
- **Then** both produce the identical sequence of `execute_action` calls, the identical
  Outcome kinds, and the identical Tree Event (`NODE_VALUE_UPDATED`). The Re-Act core is
  provider-agnostic; only the adapter's (de)serialization differs.
- **Covers:** `updateCell`; LiteLLM provider abstraction (swap). Emits: **NODE_VALUE_UPDATED**.

### AGENT-036 — provider config selects the default (Claude) when unspecified
- **Level:** unit
- **Preconditions:** no `provider_class` override in site config.
- **When** the agent provider is instantiated.
- **Then** the default adapter resolves to the Claude LiteLLM model id; assertion is on the
  configured model string, not a live call.
- **Covers:** provider default selection.

### AGENT-037 — adapter translates registry tools into provider-native tool definitions
- **Level:** unit
- **Preconditions:** `RecordingProvider`.
- **When** the loop starts with `get_llm_tools()` output.
- **Then** the adapter passes each tool to the provider in its native schema (e.g. Claude
  `input_schema` vs OpenAI `function.parameters`) while preserving `name` and the JSON-schema;
  round-tripping a recorded tool def back yields the original `params_schema`.
- **Covers:** `getLLMTools` ↔ provider adapter mapping.

### AGENT-038 — no real network: provider key absent does not block tests
- **Level:** unit
- **Preconditions:** no per-site LLM API key configured; `MockProvider` injected.
- **Then** the loop runs entirely against the mock; assert zero outbound HTTP (e.g. a
  network guard/spy records no calls). Confirms keys are per-site and never required for the
  mocked suite.
- **Covers:** provider isolation (no network).

### AGENT-039 — malformed tool arguments from the provider are surfaced, not executed blindly
- **Level:** integration
- **Preconditions:** identity = C. `MockProvider` emits `updateCell` with arguments missing
  the required `value` (schema violation).
- **Then** `execute_action` step-2 schema validation rejects the params; Observation is a
  `validation_error` fed back to the provider; **no** mutation, **no** Tree Event. The agent
  may retry with corrected args on the next scripted turn.
- **Covers:** `updateCell` (schema validation in loop). Emits: **none**.

### AGENT-040 — provider error (simulated) ends loop gracefully with a Final
- **Level:** integration
- **Preconditions:** `MockProvider` configured to raise a transport-style error on the 2nd
  call.
- **Then** the loop catches it, emits a Final step flagged `terminated_by="provider_error"`,
  and leaves any already-committed Action results intact (no rollback of prior authorized
  mutations). No unhandled exception escapes `arbor.agent.react`.
- **Covers:** Re-Act loop error handling.

---

## J. Surface parity (agent ≡ web ≡ REST)

### AGENT-041 — agent updateCell ≡ REST update_cell ≡ web executeAction (authorized)
- **Level:** e2e
- **Preconditions:** identity = C for all three surfaces; same params
  `{sheet:S,node:Y,column:col:budget,value:42}`.
- **When** the action is performed (1) via the agent tool, (2) via
  `POST /api/method/arbor.update_cell` as C, (3) via web `executeAction`.
- **Then** all three resolve identical authority (authorized), run the same handler, and
  emit a structurally identical `NODE_VALUE_UPDATED` (same payload shape, same `version`
  increment semantics). The only difference is `actor_type` (`agent` vs `human`).
- **Covers:** `updateCell`; ARCHITECTURE §11 parity (inv #6). Emits: **NODE_VALUE_UPDATED** (×3).

### AGENT-042 — agent suggest ≡ human suggest for the same unauthorized action
- **Level:** e2e
- **Preconditions:** the agent runs as a suggest-only identity; human **E** is also
  suggest-only; both attempt `updateCell{X,col:status}`.
- **Then** both produce a Change Request with `resolved_approver=C`, identical
  `target_kind/operation/payload`, and a **CHANGE_PROPOSED** event differing only in
  `actor`/`actor_type`. Confirms the agent is a peer, not a privileged path.
- **Covers:** `updateCell` (unauthorized); PERMISSIONS inv #5 & #6. Emits: **CHANGE_PROPOSED** (×2).

---

## K. Owner-self policy & idempotency boundaries

### AGENT-043 — owners_must_use_change_requests forces a CR even for an authorized agent
- **Level:** integration
- **Preconditions:** `S.settings.owners_must_use_change_requests = true`. Identity = C
  (owns `col:budget`). Script = [tool_call `updateCell{sheet:S,node:Y,column:col:budget,
  value:7}`, final].
- **Then** despite C being authorized, `execute_action` creates a Change Request with C as
  its **own** `resolved_approver`; emits **CHANGE_PROPOSED** (not NODE_VALUE_UPDATED); value
  not yet changed until C later approves. Same policy as humans (PERMISSIONS inv #8).
- **Covers:** `updateCell` (owner-self policy). Emits: **CHANGE_PROPOSED**.

### AGENT-044 — duplicate identical tool_calls in one loop are each governed independently
- **Level:** integration
- **Preconditions:** identity = C. Script emits the **same** `updateCell{Y,col:budget,
  value:5}` tool_call twice, then final.
- **Then** the first executes (`NODE_VALUE_UPDATED`, version n→n+1); the second also runs
  the handler and emits a second `NODE_VALUE_UPDATED` (version n+1→n+2) with
  `old_value==new_value==5` (no special idempotency suppression unless the handler defines
  one). Asserts the loop does not silently dedupe Actions, and the event log records both —
  matching non-agent behavior.
- **Covers:** `updateCell` (idempotency boundary). Emits: **NODE_VALUE_UPDATED** (×2).

### AGENT-045 — stale resolved_approver re-resolution applies when agent-filed CR is approved later
- **Level:** e2e
- **Preconditions:** AGENT files a CR to add a node under P2 → `resolved_approver=D`
  (CHANGE_PROPOSED). Then the P2 Branch Grant is revoked (`revokeDelegation`) so P2's
  structural approver reverts to **A**. Later someone calls `approveChange` on the agent's CR.
- **Then** at decision time the approver is **re-resolved** to A (grants changed since
  proposal); the CR is re-routed, and only A (the current approver) may approve. Confirms the
  agent's deferred call respects re-resolution (ARCHITECTURE §5).
- **Covers:** `addNode` (CR), `revokeDelegation`, `approveChange` (re-resolution).
  Emits: **CHANGE_PROPOSED**, **DELEGATION_CHANGED**, then on approve **NODE_CREATED** + **CHANGE_APPROVED**.

---

## L. Read-scope & boundary conditions

### AGENT-046 — agent cannot read a sheet it has no view access to
- **Level:** integration
- **Preconditions:** a second sheet `S2` the `AGENT` User cannot view. Script = [tool_call
  `getSheetSnapshot{sheet:S2}`].
- **Then** `getSheetSnapshot` ACL ("reader can view sheet") denies; Observation is an
  authorization error; no snapshot data leaks into the transcript/provider history; no event.
- **Covers:** `getSheetSnapshot` (read denied). Emits: **none**.

### AGENT-047 — agent on an archived sheet cannot mutate
- **Level:** integration
- **Preconditions:** `S.status = archived`. Identity = C. Script = [tool_call
  `updateCell{Y,col:budget,value:1}`].
- **Then** the mutation is rejected per sheet-status policy (no Change Request for a
  structurally-frozen archived sheet; or per implementation, surfaced as an error
  Observation); assert no `NODE_VALUE_UPDATED` is emitted on an archived sheet.
- **Covers:** `updateCell` (archived-sheet boundary). Emits: **none**.

### AGENT-048 — empty / unknown target node yields a clean tool error, not a crash
- **Level:** integration
- **Preconditions:** identity = C. Script = [tool_call
  `updateCell{sheet:S,node:"DOES_NOT_EXIST",column:col:budget,value:1}`].
- **Then** the handler/validation reports a not-found Observation back to the loop; no Tree
  Event; loop continues to a graceful Final. No uncaught exception.
- **Covers:** `updateCell` (bad reference boundary). Emits: **none**.

---

## Coverage matrix (capability → cases)

| Capability | Cases |
|---|---|
| `getSheetSnapshot` | 005, 020, 022, 046 |
| `addNode` | 011, 014, 020, 021, 024, 025, 026, 045 |
| `updateCell` | 010, 013, 016*, 017, 018, 019, 022, 023, 039, 041, 042, 043, 044, 047, 048 |
| `moveNode` | 020, 021, 029 |
| `deleteNode` | 015 |
| `delegateBranch` | 027 |
| `revokeDelegation` | 028, 045 |
| `suggestChange` | 016 |
| `approveChange` | 030, 031, 045 |
| `rejectChange` | (via lifecycle parity; see 030/045 chain) |
| `withdrawChange` | 032, 033 |
| `acknowledge` | 034 |
| `internalReset` (hidden) | 001, 002, 004 |
| `getLLMTools` / exposure | 001, 002, 003, 037 |
| Provider adapter / swap | 035, 036, 037, 038, 039, 040 |
| Re-Act loop mechanics | 005, 006, 007, 008, 009, 040 |
| Surface parity | 041, 042 |

\*016 uses `suggestChange` wrapping an `updateCell` payload.

| Tree Event | Cases asserting it |
|---|---|
| `NODE_CREATED` | 011, 021, 025, 045 |
| `NODE_VALUE_UPDATED` | 010, 012, 017, 022, 023, 030, 041, 044 |
| `NODE_MOVED` | 021 |
| `NODE_DELETED` | (suggested in 015; executed path via lifecycle) |
| `CHANGE_PROPOSED` | 013, 014, 015, 016, 018, 019, 020, 024, 026, 028, 029, 042, 043, 045 |
| `CHANGE_APPROVED` | 030, 045 |
| `CHANGE_REJECTED` | 033 (withdraw) |
| `DELEGATION_CHANGED` | 027, 045 |
| (no event) | 004, 008, 031, 032, 034, 038, 039, 046, 047, 048 |
