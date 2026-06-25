# Arbor — Demo Journeys (the `ACME` showcase)

> A hands-on tour of **every** Arbor capability area, driven from a single
> admin login. Each journey is click-by-click and labeled with the capability
> it demonstrates. The state behind these journeys is built by
> [`demo/showcase/seed.py`](../demo/showcase/seed.py) — an idempotent seed that
> creates the `ACME` sheet **through the real executor**, so the Change
> Requests actually route, the events actually emit, and the ACL actually
> resolves exactly as it would in production.

- **Sheet:** `ACME` — *ACME Platform Roadmap* (a product-org roadmap)
- **Open it:** <http://localhost:5173/?sheet=ACME>
- **Log in as:** your **System Manager** account (the admin / demo driver — referred to below simply as *admin*)
- **Re-seed any time** (`$BENCH` = your frappe-bench directory):
  ```
  cd $BENCH/sites && ../env/bin/python \
      ../apps/arbor/demo/showcase/seed.py
  ```

The seed leaves the demo in a deliberately "mid-flight" governance state: an
**inbox of 6 open Change Requests** (one of them a multi-owner batch, one a
dual-end move), **3 open role applications**, a **branch delegation**, **two-axis
column authority** (including the headline **`role:pm` column editor**), **read-ACL
tiers**, and a **watcher subscription with acknowledgement**. You approve/reject,
watch, and ask the agent — and everything behaves like the real product.

---

## What ACME contains (so the journeys make sense)

**Tree (18 nodes, depth 3):**

```
ACME Platform
├── Core Platform                 (structure owned by PM = pm@arbor.example)
│   ├── Identity & Access
│   │   ├── SSO Federation
│   │   ├── Passkeys
│   │   └── Device Trust          ← created live by an APPROVED Change Request
│   └── Billing Engine
│       ├── Usage Metering
│       └── Invoicing
├── Growth   ───────────────────  Branch Grant → dana.demo (Dana owns this subtree)
│   ├── Onboarding
│   │   ├── Guided Setup
│   │   └── Sample Data
│   └── Lifecycle Messaging
│       └── Win-back Campaign
└── Data & AI
    ├── Insights Dashboard
    └── Copilot
```

**Columns (9 — every type + every ACL feature):**

| field | label | type | column owner (Axis 2) | read level | note |
|---|---|---|---|---|---|
| `initiative` | Initiative | text *(label)* | PM | public | the node label |
| `description` | Description | multiline-text | PM | public | |
| `stage` | Stage | single-select-split | PM | public | **editors include `role:pm`** (ACL addressing) |
| `tags` | Tags | multi-select-split | DEV | public | **editor `bob.demo`** (granted via `grantColumn`) |
| `effort_weeks` | Effort (weeks) | number | DEV | public | |
| `target_release` | Target Release | text | PM | public | |
| `marketing_copy` | Marketing Copy | multiline-text | MKT | public | |
| `revenue_forecast` | Revenue Forecast ($) | number | PM | **owner-only** | only owner + admin can *see* values |
| `security_notes` | Security Review | multiline-text | DEV | **explicit-readers** | readers = `dana.demo` + `role:pm` |

**Cast:** `pm@arbor.example` (sheet owner + holds `pm`), `dev@arbor.example`,
`marketing@arbor.example`; demo users `alice.demo` (now holds `pm`), `bob.demo`,
`carol.demo` (watcher), `dana.demo` (Growth delegate), `erin.demo`. The **admin**
(your System Manager login) can see and do everything.

---

## Capability → Journey coverage

All 32 registry capabilities are reachable from these journeys. The table maps
each capability area to where you experience it.

| # | Capability area | Capabilities exercised | Journey |
|---|---|---|---|
| 1 | Tree structure (groups, leaves, levels) | `addNode`, `getSheetSnapshot`, `getSheetOverview` | **J1** |
| 2 | Column types + cell values | `addColumn`*, `updateCell` | **J1**, **J3** |
| 3a | Axis 1 — branch delegation | `delegateBranch`, `revokeDelegation` | **J6** |
| 3b | Axis 2 — column authority | `grantColumn` | **J3**, **J7** |
| 4 | Change Requests (single + batch) | `suggestChange`, `suggestChanges`, `approveChange`, `rejectChange`, `withdrawChange` | **J2** |
| 4b | moveNode dual-approval | `moveNode` (+ `approveChange`) | **J2.5** |
| 5 | Read-ACL (public / owner-only / explicit-readers) | `getSheetSnapshot` (filtered) | **J4** |
| 6 | Role management (catalog, grants, applications) | `applyForRole`, `approveRoleApplication`, `rejectRoleApplication`, `assignRole`, `revokeRole` | **J5** |
| 7 | **Role-as-ACL addressing (`role:pm`)** | `updateCell` via `role:pm` editor; read via `role:pm` reader | **J5**, **J7** |
| 8 | Subscriptions + notifications + ack | `subscribe`, `acknowledge`, `unsubscribe` | **J8** |
| 9 | Optimistic concurrency | `updateCell` w/ `base_version` (VERSION_CONFLICT) | **J9** |
| 10 | Server-side agent | `chat` over the explore reads (`listChildren`, `getNode`, `searchNodes`, `getCells`, `getSubtree`) | **J10** |
| 11 | Presentation (views, density) | hide/reorder/resize columns, row density | **J11** |
| 12 | Import / Export | snapshot export + import | **J12** |
| 13 | Activity / change-history (audit timeline) | `list_activity` over the append-only Tree Event stream (read-ACL redacted) | **J13** |
| — | CR/role withdrawal & rejection | `withdrawChange`, `rejectChange`, `withdrawRoleApplication`, `rejectRoleApplication` | **J2**, **J5** |

\* `addColumn` is demonstrated live in **J3** (the seed creates the initial 9
columns as catalog scaffolding; you add a 10th through the UI).

> **Single-login note.** Everything is experienceable as admin. A few effects
> (a *non-owner's* edit becoming a CR; a watcher's notification) normally
> involve a second user — the seed has **already created those rows as the
> other users through the executor**, so as admin you simply open the inbox and
> see/act on them. Where a step says *"observe"*, the cross-user effect is
> already present in the data; where it says *"do"*, you perform it yourself.

---

## J1 — Tree structure & cell editing  *(addNode, updateCell, snapshot)*

1. Open <http://localhost:5173/?sheet=ACME>. The **TreeTable** renders the
   18-node roadmap. Expand/collapse **Core Platform**, **Growth**, **Data & AI**
   with the disclosure triangles — three levels deep.
2. Click the **Initiative** cell of *Passkeys* and watch the breadcrumb/label;
   click any **Description** (multiline) cell to see paragraph text.
3. **Add a node:** hover a group row (e.g. *Billing Engine*) and use its row
   action **+ / Add child** (or the add control in the toolbar). Give it an
   Initiative like *"Dunning"*. Because you are admin, the add executes
   immediately and a `NODE_CREATED` event is emitted (vs. a non-owner, whose add
   would route to a CR — see J2).
4. **Edit a cell:** change *Invoicing → Stage* from `Beta` to `GA` (single-select
   dropdown). As admin this saves directly (`NODE_VALUE_UPDATED`).

*You just used `addNode`, `updateCell`, and the `getSheetSnapshot` read path.*

---

## J2 — The Change Request inbox  *(suggestChange, approveChange, rejectChange, withdrawChange)*

The seed filed several edits **as non-owner demo users**, so each became a
Change Request routed to the right approver. Open the **Governance** panel
(right rail / the panel with tabs **Change Requests · Notifications ·
Delegations · Roles**) → **Change Requests**.

You will see **6 open CRs**:

| Requester | Change | Routes to | Why |
|---|---|---|---|
| `alice.demo` | set *Passkeys* Stage → Build | **PM** | `stage` is PM-owned |
| `carol.demo` | set *Copilot* Effort → 14 | **DEV** | `effort_weeks` is DEV-owned |
| `bob.demo` | add *Checklist Widget* under *Onboarding* | **Dana** | *Onboarding* is in Dana's delegated **Growth** branch |
| `bob.demo` | rename `effort_weeks` label | **DEV** | column-schema → column owner |
| `erin.demo` | **batch** (2 changes) on *Insights Dashboard* | PM **and** DEV | see **J2.5** |
| `bob.demo` | move *Win-back Campaign* → *Core Platform* | PM **+** Dana | dual-end, see **J2.5** |

1. As **admin**, you are an approver of every CR. Click **Approve** on Alice's
   *Passkeys → Build* CR. The CR replays the original `updateCell` **as PM** (the
   resolved approver), the cell updates in the table, and a `CHANGE_APPROVED`
   + `NODE_VALUE_UPDATED` pair is emitted.
2. Click **Reject** on Bob's *rename label* CR. It moves to `rejected`; no
   mutation happens.
3. **Bulk triage:** use **Select all I can approve** then **Approve** to clear
   several at once (each becomes one independent `approveChange`).
4. *(withdraw)* A requester may withdraw their own open CR. To see this without
   a second login, note that the **Already-applied** proof is in the data: the
   seed pre-approved one structural CR — *Device Trust* now exists under
   *Identity & Access* (that node was created by an approved `addNode`, not by
   the seed directly).

*Demonstrates the full CR lifecycle: proposed → approved | rejected, and the
"replay the handler as the approver" applied path.*

---

## J2.5 — Multi-owner batch & dual-approval move  *(suggestChanges, moveNode)*

1. In **Change Requests**, open Erin's **batch** CR. It bundles **two** edits in
   one review unit: a `target_release` change (PM-owned) **and** an `effort_weeks`
   change (DEV-owned). Each item lists its **own approver**; **nothing applies
   until every item is approved**. As admin, approve it — both items apply
   atomically and one `CHANGE_APPROVED` (with `changes: 2`) is emitted.
2. Open Bob's **move** CR (*Win-back Campaign* → *Core Platform*). Because the
   source branch (Growth) is owned by **Dana** and the destination (Core
   Platform) by **PM**, this is a **dual-end** CR with both as required
   approvers. The panel shows it needs both ends. As admin you stand in for both
   — approve it and the node re-parents (`NODE_MOVED`). (With real separate
   users, it would stay `proposed` until the *second* end also approves.)

*Demonstrates atomic multi-change CRs spanning owners, and moveNode's
two-sided approval (ADR-001).*

---

## J3 — Column authority (Axis 2) & adding a column  *(grantColumn, addColumn, updateCell)*

1. Open a column's config (the header menu / **Column** settings, e.g. on
   **Tags**). Note `tags` is **DEV-owned**, and the seed granted **`bob.demo`**
   as an explicit editor via `grantColumn`. So Bob may edit *Tags* on **any**
   node — even nodes inside Dana's Growth branch he does NOT structurally own.
   This is **axis independence**: column authority ignores tree structure.
2. **Add a column live:** use the schema/add-column affordance to add a new
   column, e.g. *"Owner Team"* (type **text**), owned by you. This emits
   `COLUMN_CONFIG_UPDATED`. Set a value on a couple of rows.
3. Change the column owner / editors from the column config to re-route who can
   edit it — another `grantColumn`.

*Demonstrates Axis 2 ownership, editor delegation, and live schema change.*

---

## J4 — Read-ACL: visibility differs by viewer  *(getSheetSnapshot, filtered)*

Two columns have restricted **read** levels:

- **Revenue Forecast ($)** — `owner-only`: only the column owner (PM) **and
  admin** may even *see* the numbers.
- **Security Review** — `explicit-readers`: readable only by an allowlist
  (`dana.demo` + everyone holding **`role:pm`**), plus owner + admin.

1. As **admin**, you see **all 9 columns** including both restricted ones with
   their values — admin bypasses read-ACL.
2. To experience the *difference*, open the sheet as a non-privileged user. If
   you can switch users, log in as `bob.demo` — **Revenue Forecast** and
   **Security Review** columns are **absent from his snapshot entirely** (not
   blanked — omitted, so their very existence isn't leaked). Without a second
   login, verify the rule from the server:
   ```
   cd $BENCH/sites && ../env/bin/python -c "
   import frappe; frappe.init(site='arbor.test', sites_path='.'); frappe.connect()
   import sys; sys.path.insert(0,'../apps/arbor')
   from arbor.arbor.adapter.repository import FrappeRepository
   from arbor.core.acl import can_read_column
   from arbor.core.types import Actor
   r=FrappeRepository(); sec=r.get_column('ACME','acme:security_notes'); rev=r.get_column('ACME','acme:revenue_forecast')
   for u in ['bob.demo@arbor.example','dana.demo@arbor.example','alice.demo@arbor.example']:
       a=Actor(u); print(u, 'revenue:', can_read_column(r,'ACME',rev,a), 'security:', can_read_column(r,'ACME',sec,a))"
   ```
   You'll see Bob = both False; Dana = security True (explicit reader); Alice =
   security True (she holds `role:pm`).

*Demonstrates the 3-level read-ACL and that the snapshot + explore reads apply
the same filter.*

---

## J5 — Role management & the `role:pm` headline  *(applyForRole, approveRoleApplication, assignRole, role-as-ACL)*

Open **Governance → Roles** (the admin Roles inbox).

1. **Open applications (3):** `bob.demo → developer`, `erin.demo → marketing`,
   `dana.demo → design`. These were filed via `applyForRole`. **Approve** one
   (`approveRoleApplication`) — it materializes an **Arbor Role Grant**; **Reject**
   another (`rejectRoleApplication`). Keep one pending so the inbox stays alive.
2. **Direct grant/revoke:** in the same panel, **assign** a role to a user
   (`assignRole`, e.g. grant `developer` to `carol.demo`) and **revoke** it
   (`revokeRole`). Admin-only, immediate, no approval.
3. **The headline — `role:pm` as a column ACL principal.** The seed already
   approved `alice.demo → pm`, so **Alice now holds the `pm` role**. The `stage`
   column lists **`role:pm`** among its editors. That means *every holder of
   `pm`* — including Alice — is **automatically** an editor of `stage`, with no
   per-user grant. Confirm it "just works":
   ```
   cd $BENCH/sites && ../env/bin/python -c "
   import frappe; frappe.init(site='arbor.test', sites_path='.'); frappe.connect()
   import sys; sys.path.insert(0,'../apps/arbor')
   from arbor.arbor.adapter.repository import FrappeRepository
   from arbor.core.acl import resolve_column_approvers
   print('stage editors:', sorted(resolve_column_approvers(FrappeRepository(),'ACME','acme:stage')))"
   ```
   → `['alice.demo@arbor.example', 'pm@arbor.example']`. Approve another `pm`
   application and that new user instantly joins the `stage` editor set too.
   The same `role:pm` principal is also a **reader** of the `security_notes`
   column (J4) — one role, both axes, zero per-user wiring.

*Demonstrates the role lifecycle AND role-as-ACL addressing — the new capability.*

---

## J6 — Branch delegation (Axis 1)  *(delegateBranch, revokeDelegation)*

Open **Governance → Delegations**.

1. See the active grant: **Growth → `dana.demo`**. Dana **structurally owns the
   entire Growth subtree** (Onboarding, Guided Setup, Sample Data, Lifecycle
   Messaging, Win-back Campaign) — nearest-grant-wins on the ancestor chain.
   This is exactly why Bob's *"add under Onboarding"* CR (J2) routed to **Dana**,
   not the sheet owner PM.
2. **Delegate a branch yourself:** use the **Branch / Grantee** control to grant,
   say, *Data & AI* to `bob.demo` (`delegateBranch`). Now a structural add under
   *Copilot* would route to Bob.
3. **Revoke** the grant you just made (`revokeDelegation`) — authority falls back
   to the ancestor owner (PM). Both emit `DELEGATION_CHANGED`.

*Demonstrates subtree-scoped, delegable structural ownership and re-routing.*

---

## J7 — Axis independence in one screen  *(updateCell across both axes)*

A quick conceptual payoff combining J3/J5/J6:

- **Dana** owns the **Growth structure** (Axis 1) but owns **no columns** — she
  can add/move/delete Growth nodes, yet editing *Stage* on a Growth node would
  route to a CR (it's PM/`role:pm`-owned).
- **Alice** (via `role:pm`) can edit *Stage* on **any** node, including inside
  Dana's Growth branch she has no structural authority over (Axis 2).
- A **cell = (node, column)**: its *position* is governed by Axis 1, its *value*
  by Axis 2. Pick *Win-back Campaign* (in Growth) and note: structure → Dana,
  `stage` value → PM/`role:pm`.

*Demonstrates the core invariant: the two axes are orthogonal and compose at the
cell.*

---

## J8 — Subscriptions, notifications & acknowledgement  *(subscribe, acknowledge)*

The seed subscribed **`carol.demo`** to the **Growth branch** for
`CHANGE_PROPOSED / CHANGE_APPROVED / NODE_CREATED`, **with `requires_ack`**.
Her inbox is empty at seed time (she subscribed after the existing CRs were
filed) — so you generate a fresh matching event and watch it light up.

1. Go to **Governance → Change Requests** and **approve Bob's *"add Checklist
   Widget under Onboarding"*** CR (it's in Growth). This emits `CHANGE_APPROVED`
   **and** `NODE_CREATED`, both inside Growth.
2. Open **Governance → Notifications** (Carol's, as admin you can inspect the
   inbox). Two notifications appear for Carol, each flagged **requires
   acknowledgement**.
3. Click **Acknowledge** — an Acknowledgement row is written and the
   notified-vs-acked ledger updates (the auditable "N notified / M acked").
4. *(unsubscribe)* The **Subscription** control lets the owner unsubscribe
   (`SUBSCRIPTION_CHANGED`).

> Verified working in-process: approving that Growth CR produces exactly 2
> ack-required notifications for Carol.

*Demonstrates branch-scoped subscriptions, notification fan-out, and the
acknowledgement ledger.*

---

## J9 — Optimistic concurrency  *(updateCell base_version → VERSION_CONFLICT)*

Arbor supports opt-in lost-update protection: pass `base_version` with a cell
edit and the write is rejected if the stored version has moved.

1. In the UI, the editor sends the cell's known version on save; if another
   writer bumped it first, you get a **VERSION_CONFLICT** (the UI surfaces a
   "reload / your value vs. theirs" prompt) instead of silently overwriting.
2. Demonstrate the server contract directly:
   ```
   cd $BENCH/sites && ../env/bin/python -c "
   import frappe; frappe.init(site='arbor.test', sites_path='.'); frappe.connect(); frappe.set_user('Administrator')
   import sys; sys.path.insert(0,'../apps/arbor')
   from arbor.arbor.adapter.repository import FrappeRepository, FrappeEventSink
   from arbor.core.executor import execute_action
   from arbor.core.types import Actor
   r,s=FrappeRepository(),FrappeEventSink(); pm=Actor('pm@arbor.example')
   # current version of Invoicing / target_release:
   v=r.get_value_version('Invoicing','acme:target_release'); print('stored version:', v)
   try:
       execute_action('updateCell', {'sheet':'ACME','node':'Invoicing','column':'acme:target_release','value':'v9.9','base_version': (v or 0)+5}, pm, r, s)
   except Exception as e:
       print('rejected as expected:', type(e).__name__)
   frappe.db.rollback()"
   ```
   A stale `base_version` raises a version conflict; the correct version writes.

*Demonstrates the opt-in optimistic-concurrency guard.*

---

## J10 — Ask the server-side agent  *(chat over explore reads)*

Open the **Agent** rail (tabs **Agent · Capabilities · Try**; input *"Ask the
agent…"*).

1. Ask: **"Summarize the ACME roadmap structure and what's in the Growth
   branch."** The agent navigates with the bounded explore tools
   (`getSheetOverview`, `listChildren`, `getSubtree`, `getNode`) — it never pulls
   the whole sheet — and answers.
2. Ask: **"What can I edit on this sheet?"** The agent reasons over the same
   two-axis ACL you do (it runs as its own actor under identical rules).
3. Ask: **"Which initiatives are in Beta stage?"** — drives `searchNodes` /
   `getCells`.
4. The agent only sees columns its actor may **read** — ask it about *Revenue
   Forecast* and it will note it can't access that column (read-ACL applies to
   the agent for free).

*Demonstrates the agent as a first-class, ACL-bound surface over the bounded
read API. (The **Capabilities** tab lists every LLM-exposed capability;
`internalReset` is the one hidden capability.)*

---

## J11 — Presentation: Views & density  *(UI-only)*

1. Open the **View** menu (top bar). **Hide** a couple of columns (e.g.
   *Marketing Copy*, *Security Review*), **reorder** columns by dragging, and
   **resize** a column.
2. The view encodes into the URL as a query param — copy the address bar; it's a
   **shareable custom view** (`?sheet=ACME&view=…`). Open it in a new tab to
   confirm the layout restores.
3. Use the **Row density** control (compact / comfortable / expand) to change how
   multiline cells (*Description*, *Marketing Copy*) line-clamp.

*Demonstrates presentation state that is per-viewer and shareable, with zero
effect on the governed data.*

---

## J12 — Import / Export  *(the Data menu)*

1. Open the **Data** menu (top bar). **Export** the current snapshot — you get a
   JSON document of the sheet (structure + readable cells).
2. **Import:** use **Choose file** or paste JSON into the *"…or paste exported
   snapshot JSON here to import"* box. A round-tripped export re-imports cleanly;
   an import emits `IMPORT_COMPLETED`.
3. Because export respects **read-ACL**, a non-admin's export omits columns they
   can't read — import/export inherit the same governance as every other surface.

*Demonstrates governed import/export round-trip.*

---

## J13 — Activity / change history  *(the audit timeline over the Tree Event stream)*

1. In the Governance panel, open the **Activity** tab (the 5th tab; its badge
   shows the recent-event count, e.g. **50**). You get a newest-first timeline of
   everything that has happened on this sheet — built from the append-only **Tree
   Event** log, the same record webhooks and notifications fire from.
2. Each row carries a **type chip** (`NODE CREATED`, `APPROVED`, `PROPOSED`,
   `SUBSCRIPTION CHANGED`, `DELEGATION CHANGED`, …), a human one-line **summary**
   (e.g. *"pm@arbor.example added Device Trust"*, *"alice.demo proposed a
   change"*), the **actor**, a **timestamp**, and — on change-related events — the
   **Change Request id** (so you can trace an approval back to its request).
3. **Read-ACL is enforced on the feed**, just like the grid: an event that
   touched a column you can't read shows a redacted summary (*"updated a cell"*),
   never the column name and **never the raw value** — the timeline tells you
   *what happened*, not the protected data. As admin you see every event in full;
   a non-owner viewing the same feed sees the redacted form for `revenue_forecast`.
4. Note the tab **never steals default focus** — the panel still opens on the
   actionable Change Requests queue; Activity is history you pull up on demand.

*Demonstrates the change-history surface (`list_activity`) over the append-only
event stream, with the same read-ACL governance as every other surface.*

> **Where the data comes from.** Every governed mutation in J1–J12 emits exactly
> one Tree Event; that append-only stream IS the version/audit history. There is
> no separate "undo log" — the event stream is the single source of truth that
> webhooks, notifications, and this Activity feed all read from.

---

## Appendix — verified live counts (after seeding)

| Thing | Count |
|---|---|
| Sheets present | `ACME`, `WIDE`, `ECOM` (ECOM/WIDE untouched) |
| ACME nodes | **18** (17 seeded + 1 from an approved `addNode` CR) |
| ACME columns | **9** (text, multiline-text, number, single-select-split, multi-select-split) |
| ACME cell values | **117** (written via the executor as the column owners) |
| Active branch grants | **1** (Growth → `dana.demo`) |
| Change Requests | **7** total — **6 proposed**, 1 approved (incl. 1 batch, 1 dual-end move) |
| Open role applications | **3** (developer / marketing / design) |
| `pm` role grantees | `alice.demo@arbor.example`, `pm@arbor.example` |
| `role:pm` resolves on `acme:stage` editors | `{alice.demo, pm}` ✔ |
| Watcher subscription | **1** (`carol.demo` on Growth, `requires_ack`) |

Re-running the seed reproduces this state exactly (idempotent) and never touches
`ECOM`, `WIDE`, the role catalog, or the admin account.
