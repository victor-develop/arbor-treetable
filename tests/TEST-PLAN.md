# Arbor — Master Test Plan

> The test architect's synthesis of the eight per-surface catalogs into one coherent
> strategy. This document is the **entry point** for the whole test suite: it fixes the
> layering and tooling, defines the **one** canonical fixture every catalog references,
> indexes every surface catalog with its case-ID ranges, and proves capability coverage
> with a traceability matrix.
>
> Companion specs (the locked source of truth the tests are built against):
> [`ARCHITECTURE.md`](../docs/ARCHITECTURE.md) ·
> [`DATA-MODEL.md`](../docs/DATA-MODEL.md) ·
> [`CAPABILITIES.md`](../docs/CAPABILITIES.md) ·
> [`PERMISSIONS.md`](../docs/PERMISSIONS.md)
>
> **Totals:** 8 surface catalogs · **506 cases** · 19 capabilities (18 with coverage, see §5).

---

## 1. Overview & the test pyramid

Arbor's defining property is that **every surface funnels through one path**
(`execute_action` → ACL resolver → handler → `emit_event`; ARCHITECTURE §4.2, §11).
The test strategy is shaped by that fact: the bulk of behavioral truth lives in the
**backend core**, so the pyramid is deliberately backend-heavy. Surfaces (Web, REST,
agent, webhooks) are tested mostly for **parity and wiring**, not for re-deriving the
governance logic — that would duplicate the very thing the architecture refuses to
duplicate.

```
                      ┌──────────────────────────┐
                      │           e2e            │   ~5%   Playwright (browser),
                      │  (real surfaces, slow)   │         live HTTP webhook receiver
                      ├──────────────────────────┤
                      │       integration        │   ~30%  execute_action end-to-end,
                      │ (executor + ACL + events │         dispatchers, CR lifecycle,
                      │  + dispatchers, in-proc)  │         REST methods, agent loop
                      ├──────────────────────────┤
                      │           unit           │   ~65%  resolver, registry schema,
                      │ (pure functions, fast)   │         HMAC, backoff, serializer,
                      │                          │         NestedSet walks
                      └──────────────────────────┘
```

### 1.1 Layer definitions

| Layer | What it covers | Example cases |
|---|---|---|
| **Unit** | Pure, isolated logic with no surface: `resolve_structural_approver` ancestor walk, `resolve_column_approvers`, nearest-grant-wins, registry/`getLLMTools` filtering, params-schema validation, HMAC signing, retry backoff schedule, snapshot serializer shape, NestedSet range queries. | PERMISSIONS_AND_DELEGATION ACL walks; WEBHOOKS HMAC/backoff; AGENT-001..004 tool exposure |
| **Integration** | The **one path** wired together in-process: `execute_action` → ACL → handler → event → dispatcher fan-out, plus CR lifecycle replay, notification/ack ledger, REST whitelisted methods, the Re-Act loop against a mocked provider. This is where the **governance keystone** (authorized-vs-suggested) and **surface parity** live. | CHANGE_REQUEST_LIFECYCLE-*; NOTIFICATIONS_AND_ACK-*; API parity (API-010..013); AGENT loop/mutation cases |
| **e2e** | Real React app driving the real (or mock-boundary) backend through a browser; real HTTP for webhook delivery against a live local receiver. | WEB_UI render/edit/agent-sidebar flows; WEBHOOKS live-receiver delivery/retry |

### 1.2 Tooling per layer (and why)

| Concern | Tool | Justification |
|---|---|---|
| **Backend unit + integration** | **Frappe test framework / pytest** | The core is Frappe DocTypes + Python modules. Frappe's `FrappeTestCase` gives transactional rollback per test, fixture seeding, and the site/DocType context the resolver and NestedSet queries require. pytest fixtures express the shared seed (§2) cleanly and run the pure-logic units fast. One framework for the whole backend keeps the parity assertions (web/REST/agent compared in-process) trivial. |
| **Frontend unit/component** | **Vitest + React Testing Library** | The React app is a *thin shell* over `executeAction` / `getSheetSnapshot` / `agent.chat`. Component tests assert the shell renders snapshot-driven affordances (per-column/per-node edit flags) and routes clicks to the mocked client API — no governance logic to re-test. Vitest matches the Vite/React toolchain; RTL asserts user-visible behavior over implementation. |
| **Frontend e2e** | **Playwright** | Real browser flows (tree expand/collapse, inline edit → direct-vs-suggest toast, agent sidebar streaming, export/import round-trip) need a real DOM and async streaming. Playwright is reliable for the multi-step, network-driven journeys in `web-ui.md`. |
| **Webhooks** | **Local receiver harness** (programmable HTTP server) | Delivery, HMAC verification, and retry/backoff are only meaningful against a real socket that can return 2xx/4xx/5xx/timeout/malformed and capture raw body + headers. The harness pairs with a freezable/advanceable clock + on-demand retry runner so backoff (0s,30s,5m,30m,2h,12h) is deterministic. |
| **Agent** | **Mocked LLM adapter** (scripted LiteLLM `MockProvider` / `RecordingProvider`) | The Re-Act loop must be deterministic and offline. A scripted adapter returns canned tool-call frames so the test asserts the loop's *control flow* and the resulting `execute_action` outcomes — never a live model. A `RecordingProvider` variant captures the rendered tool defs to assert `getLLMTools()` wiring. |

> **Principle.** Governance correctness is proven once, at the backend integration layer,
> against the shared fixture. Every other surface asserts **parity** ("same ACL decision,
> same handler, same Tree Event"; ARCHITECTURE §11) rather than re-proving the rule.

---

## 2. The canonical SHARED FIXTURE (DRY — the one place fixtures live)

This is the single seed every catalog references by name. No catalog redefines it; a test
needing a variation references this seed **plus** a named delta (e.g. "seed + `S'` with
`owners_must_use_change_requests=true`"). Expressed as a Frappe/pytest factory
(`seed_canonical_sheet()`), mirrored by the Vitest `loginAs()` mock boundary and the
agent `run_agent(...)` harness so all surfaces share identical data.

### 2.1 Personas (Frappe Users)

| Persona | Role in the model | Authority |
|---|---|---|
| **A** | Root structural owner | `Tree Sheet.structural_owner`; Axis-1 fallback approver for the whole tree (minus delegations). Owns **no columns**. |
| **B** | Column owner | Owner of `col:name` (the `is_label` column) and `col:notes`; **editor** on `col:status`. |
| **C** | Column owner | Owner of `col:status` and `col:budget`. |
| **D** | Delegated sub-branch owner | Grantee of an active Branch Grant on **P2** (`scope=structure`) → structural approver anywhere in the P2 subtree. No column authority. |
| **E** | Suggest-only collaborator | No grants, no columns. Every mutation becomes a Change Request. |
| **F** | Suggest-only collaborator | Same as E (a second body so two independent requesters/withdrawals can be exercised). |
| **G** | Sensitive subscriber | No edit authority; a watcher. Holds a `requires_ack=true` subscription (powers the accountability ledger). |
| **EXT** | External system | A Frappe User **+ API key** (REST consumer) **and** a Webhook Endpoint subscriber. No special privileges — bound by the same two-axis ACL. |
| **AGENT** | Server-side agent | Acts under **its own Frappe User** (`actor_type=agent`); suggest-only by default, reconfigurable to hold a given persona's authority for the authorized-agent cases. |

> Two optional admin-only identities used by the Admin catalog extend the seed without
> changing it: **ADMIN** (System Manager) and **OUTSIDER** (no role on `S`). They are part
> of the seed's *optional* slice, referenced only where admin-gating is under test.

### 2.2 Sample Tree Sheet `S`

```python
def seed_canonical_sheet():
    # ----- Tree Sheet -----
    S = TreeSheet(title="S", structural_owner=A, status="active",
                  settings={})                      # owners_must_use_change_requests = false (default)

    # ----- Columns (Tree Column rows; ownership co-located on the row) -----
    col_name   = TreeColumn(sheet=S, field="name",   type="text",
                            is_label=True,  column_owner=B)            # the label column
    col_status = TreeColumn(sheet=S, field="status", type="single-select-split",
                            column_owner=C, editors=[B])               # B is an editor here
    col_budget = TreeColumn(sheet=S, field="budget", type="number",
                            column_owner=C)
    col_notes  = TreeColumn(sheet=S, field="notes",  type="multiline-text",
                            column_owner=B)
    # (col:tags multi-select-split, owner C — optional slice for Web-UI split-multi cases)

    # ----- Tree (NestedSet; nearest-first walk = self -> root) -----
    #   R                         struct authority: A
    #   ├── P1                    struct authority: A
    #   │   └── X                 struct authority: A
    #   └── P2  ◄── Branch Grant (grantee=D, scope=structure, active)  struct authority: D
    #       ├── Y                 struct authority: D (inherited)
    #       └── Z                 struct authority: D (inherited)
    R  = node(S, parent=None)
    P1 = node(S, parent=R);  X = node(S, parent=P1)
    P2 = node(S, parent=R);  Y = node(S, parent=P2);  Z = node(S, parent=P2)

    BranchGrant(sheet=S, branch_root=P2, grantee=D,
                scope="structure", granted_by=A, active=True)

    # ----- Initial cell values (Tree Node Value rows; one per (node, column)) -----
    set_cell(R,  col_name, "Root")
    set_cell(P1, col_name, "Phase 1");  set_cell(X, col_name, "Task X")
    set_cell(P2, col_name, "Phase 2");  set_cell(Y, col_name, "Task Y")
    set_cell(Z,  col_name, "Task Z")
    set_cell(X,  col_status, "todo");   set_cell(X, col_budget, 1000)
    set_cell(Y,  col_budget, 5000);     set_cell(Z, col_budget, 12000)

    return Fixture(S=S, nodes=..., columns=..., grant_P2=...)
```

### 2.3 Named deltas (referenced, not redefined)

The seed is mutated by **named** add-ons so variation never forks the base:

| Delta | Purpose | Used by |
|---|---|---|
| `S'` — `settings.owners_must_use_change_requests=true` | owner-self policy / forced audit trail | CRL, API, Admin, Web-UI, Permissions |
| `BG_Z` — nested active Branch Grant on **Z**, grantee **D2** | nearest-grant-wins | Permissions, API, Agent |
| `col:tags` (multi-select-split, owner C) | split-multi value editing | Web-UI |
| `S2` / second org sheet | scope-isolation (webhooks/notifications/agent read-deny) | Webhooks, Agent, Notifications |
| large-tree variant of `S` (≥500 nodes) | pagination / filtering / range-query perf | API |
| pre-seeded CRs (`cr1` value→C, `cr2` requester E, `cr3` structural P2→D, `crv` versioned cell) | lifecycle/decision-time tests without re-creating CRs | API, CRL, Agent, Web-UI |
| `SUB_G` (branch P2, `[CHANGE_PROPOSED, CHANGE_APPROVED, NODE_DELETED]`, in-app, `requires_ack=true`); `G2` second watcher | sensitive-subscriber + accountability | Notifications, Permissions |
| `EXT_ENDPOINT` (sheet scope, known secret, active) + branch/column-scoped variants | webhook scope + HMAC + retry | Webhooks |
| MockProvider / RecordingProvider; freezable clock; HTTP receiver harness | offline determinism | Agent, Webhooks, Notifications |

---

## 3. Surface catalog index

| # | Surface | Catalog | Cases | Case-ID range(s) |
|---|---|---|---|---|
| 1 | Permissions & Delegation (two-axis ACL, delegation, suggest-routing) | [`permissions-and-delegation.md`](./permissions-and-delegation.md) | 76 | `PERMISSIONS_AND_DELEGATION-001 … -076` |
| 2 | Change Request & Approval lifecycle | [`change-request-lifecycle.md`](./change-request-lifecycle.md) | 51 | `CHANGE_REQUEST_LIFECYCLE-001..005, -010..015, -020..023, -030..033, -040..043, -050..055, -060..065, -070..072, -080..084, -090..092, -100..102, -110..111` |
| 3 | Notifications & Ack Ledger | [`notifications-and-ack.md`](./notifications-and-ack.md) | 45 | `NOTIFICATIONS_AND_ACK-001..043` (+ `-010b`, `-014b`) |
| 4 | Webhook Events | [`webhooks.md`](./webhooks.md) | 50 | `WEBHOOKS-001 … -050` |
| 5 | REST API (first-class peer) | [`api.md`](./api.md) | 70 | `API-001..004, -010..013, -020..028, -040..048, -060..068, -080..087, -100..103, -120..125, -140..149, -160..163, -180..182` |
| 6 | Web UI (React thin shell) | [`web-ui.md`](./web-ui.md) | 90 | `WEB_UI-001 … -090` |
| 7 | Admin & Schema Co-Design | [`admin.md`](./admin.md) | 76 | `ADMIN-001 … -076` |
| 8 | AI Re-Act Agent | [`agent.md`](./agent.md) | 48 | `AGENT-001 … -048` |
| | **Total** | | **506** | |

> Case-ID prefixes are **non-overlapping by surface**, so any ID is globally unambiguous.
> Sparse numbering (gaps like `-005 → -010`) is intentional per-catalog sectioning, not
> missing cases — the counts above are exact unique-ID counts.

---

## 4. Traceability matrix — capability × surface

Marks which surfaces exercise each capability (`✓`). Capabilities are the rows of the
registry (CAPABILITIES.md); surfaces are the eight catalogs. Derived from each catalog's
declared capability set, cross-checked against grep of the catalog bodies.

Legend: **Perm**=Permissions · **CRL**=Change-Request Lifecycle · **Notif**=Notifications
· **Hook**=Webhooks · **API**=REST · **Web**=Web-UI · **Adm**=Admin · **Agt**=Agent.

| Capability | Perm | CRL | Notif | Hook | API | Web | Adm | Agt | Coverage |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|---|
| `getSheetSnapshot` |   | ✓ | ✓ |   | ✓ | ✓ | ✓ | ✓ | strong |
| `addNode` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | strong |
| `updateCell` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | strong |
| `moveNode` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |   | ✓ | strong |
| `deleteNode` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |   | ✓ | strong |
| `addColumn` | ✓ | ✓ |   | ✓ | ✓ | ✓ | ✓ | ✓ | strong |
| `updateColumn` | ✓ | ✓ |   | ✓ | ✓ | ✓ | ✓ | ✓ | strong |
| `deleteColumn` | ✓ | ✓ |   | ✓ | ✓ | ✓ | ✓ | ✓ | strong |
| `suggestChange` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | strong |
| `approveChange` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | strong |
| `rejectChange` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | strong |
| `withdrawChange` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | strong |
| `subscribe` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | strong |
| `unsubscribe` |   |   | ✓ | ✓ |   |   | ✓ | ✓ | adequate |
| `acknowledge` |   | ✓ | ✓ |   |   | ✓ | ✓ | ✓ | strong |
| `delegateBranch` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | strong |
| `revokeDelegation` | ✓ | ✓ |   | ✓ | ✓ |   | ✓ | ✓ | strong |
| `grantColumn` | ✓ | ✓ |   | ✓ | ✓ | ✓ | ✓ | ✓ | strong |
| `internalReset` |   |   |   |   | ✓ |   | ✓ | ✓ | **thin** (see §5) |

**Per-capability findings**

- **Zero-coverage gaps: none.** Every one of the 19 registry capabilities is exercised by
  at least one surface.
- **`internalReset` — thin.** Exercised only by API, Admin, and Agent, and only for the
  guardrails that matter: it is `is_exposed_to_llm=False` (Agent asserts it is **never**
  offered as a tool), admin/system-only (Admin asserts non-admins are denied), and not on
  the Tree Event stream. That is the correct scope, but it is the **least-covered** mutator
  and the only one with no Web-UI or parity path — acceptable, flagged for awareness.
- **`unsubscribe` — adequate, asymmetric.** Covered by Notifications/Webhooks/Admin/Agent
  but **not** by API or Web-UI, whereas its sibling `subscribe` is covered everywhere. Add
  a REST and a Web-UI `unsubscribe` case so the subscribe/unsubscribe pair has identical
  surface parity (see §5).
- **Event-type coverage.** All 11 Tree Event types are reachable through the capabilities
  above **except** there is no capability that emits `IMPORT_COMPLETED`; it is produced by
  the export/import round-trip path tested in Web-UI / Admin / Webhooks (grep-confirmed in
  those three catalogs) rather than by a registry capability. Ensure at least one
  **webhook** and one **notification** case subscribes to `IMPORT_COMPLETED` so the derived
  consumers of that event are proven, not just the producer (see §5).

---

## 5. Coverage gaps & risks to address during implementation

1. **`unsubscribe` surface-parity gap.** `subscribe` is tested on all 8 surfaces;
   `unsubscribe` is missing from **REST API** and **Web UI**. Add `API-*` and `WEB_UI-*`
   `unsubscribe` cases so the pair is symmetric and the §11 parity guarantee holds for both
   halves of the subscription lifecycle.

2. **`internalReset` is the thinnest-covered mutator.** Coverage is correct-but-minimal
   (Agent: never an LLM tool; Admin: admin-only deny; API: whitelisted method exists). Risk:
   a destructive global op with little testing. Add an explicit case asserting it emits **no**
   Tree Event (append-only invariant, DATA-MODEL §12) and that it is rejected for `EXT` and
   all non-admin personas over REST.

3. **`IMPORT_COMPLETED` consumers under-asserted.** The event type exists in the closed set
   but is produced by import (not a registry capability). Confirm a webhook endpoint and a
   `requires_ack` subscription can both fire on `IMPORT_COMPLETED`, otherwise an entire event
   type reaches the stream with no proven downstream fan-out.

4. **Surface-parity is asserted per-surface, not centrally.** API (API-010..013) and Agent
   (AGENT-015 etc.) each assert "same ACL/handler/event as `execute_action`," but no single
   test compares **all three** surfaces for the *same* capability+actor in one assertion.
   Add a small cross-surface parity harness (in-process `execute_action` vs REST method vs
   agent tool-call, identical params/actor) so ARCHITECTURE §11 is proven as the *primary
   invariant* rather than implied by three separate suites.

5. **`moveNode` dual-end authority is the highest-logic-density edge.** Authorized only when
   the actor approves **both** src and dest; otherwise CR to dest with src as co-approver
   (PERMISSIONS §4.4). It is well covered in Permissions/CRL/Agent but **absent from Admin**
   and only lightly in Web-UI drag-drop. Keep the integration-layer move cases as the source
   of truth; ensure the Web-UI drag-and-drop e2e maps cleanly onto the same outcomes rather
   than re-deriving them.

6. **Re-resolution at decision time.** CRL-053..055 cover an approver becoming stale when the
   tree/grants change between proposal and decision. This is subtle and easy to regress;
   gate it as a required integration case and mirror one instance through the Agent
   (an agent approving a CR whose `resolved_approver` was recomputed).

7. **Owner-self policy (`owners_must_use_change_requests=true`).** The `S'` delta is
   referenced by 5 catalogs; risk is divergent expectations across surfaces (does an owner's
   direct edit on `S'` show as "suggested" in Web-UI toast, as `Outcome(kind="suggested")`
   over REST, and as a self-approver CR in the ledger?). Pin one shared assertion of the
   self-approver CR shape and reference it from each surface rather than re-specifying.

8. **Determinism risks in time/network-bound layers.** Webhook backoff and notification
   delivery depend on the freezable clock + on-demand retry runner, and the agent on the
   scripted provider. If any catalog case quietly uses wall-clock or a live socket/model it
   becomes flaky. Enforce (lint/fixture-level) that webhook and agent cases only ever use the
   harness clock/receiver and the Mock/Recording provider.
