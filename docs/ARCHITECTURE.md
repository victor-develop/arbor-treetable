# Arbor — Architecture (Canonical Specification)

> **Status:** Single source of truth. Test authors and implementers build against
> this document. The decisions encoded here are **locked**; do not re-open them.
>
> **Companion specs:**
> [`DATA-MODEL.md`](./DATA-MODEL.md) ·
> [`CAPABILITIES.md`](./CAPABILITIES.md) ·
> [`PERMISSIONS.md`](./PERMISSIONS.md)

---

## 1. Product framing

**Arbor** is a collaborative, governed, API-first, agent-native **Tree Table** SaaS.
It replaces flat Google-Sheet-style collaboration with **fine-grained governance over
hierarchical data**.

A *tree table* is a table whose rows form a tree (each row may have child rows) and
whose columns are defined as data (not as fixed code-level fields). The product's
defining problem is **governance**: who is allowed to change *what* part of the tree,
and what happens to everyone else's edits — they become **suggestions** that route to
the right owner for approval.

The system is built on the **Frappe Framework** (Python backend, DocType ORM,
NestedSet, auto-REST, Webhook) with a **standalone React frontend**. Engineering taste
follows the reference repo
[`github.com/victor-develop/React-TreeTable-Demo`](https://github.com/victor-develop/React-TreeTable-Demo):

- an **event-sourced capability registry** as the single source of truth for actions,
- a **centralized `executeAction`** through which every mutation flows,
- a **ColumnConfiguration-driven dynamic schema** (schema is data).

Arbor lifts those three ideas from the browser into a governed, multi-surface server.

### 1.1 Design philosophy — few orthogonal primitives

We deliberately compose a **small set of orthogonal primitives** rather than many
special cases:

| Primitive | Governs | Composes with |
|---|---|---|
| **Tree Node** (NestedSet) | structure / hierarchy | Tree Column → a cell |
| **Tree Column** (meta-model) | schema / fields | Tree Node → a cell |
| **Branch Grant** | Axis 1 authority (vertical) | resolution walk |
| **Tree Column.column_owner + editors** | Axis 2 authority (horizontal) | direct lookup |
| **Capability** | the only way to mutate | executeAction |
| **Change Request** | a deferred capability call | event stream |
| **Tree Event** | the only record of truth of *what happened* | webhooks + notifications |

Everything else (notifications, webhooks, the agent, the API) is **derived** from these
primitives. There is exactly one way to change state, exactly one log of changes, and
exactly one ACL resolver.

---

## 2. The two-axis ownership model (the heart of Arbor)

Arbor has **two orthogonal ownership axes**. They never merge into a single ACL bit;
they are resolved independently and composed at the *cell* level.

```
                    AXIS 2 — COLUMN ownership (horizontal / field-scoped)
                    governs the VALUE of cells in a column
        ┌───────────────────────────────────────────────────────────┐
        │   col:name     col:status    col:owner_email   col:budget   │
        │   (owner B)    (owner C)      (owner B)         (owner C)    │
  ┌─────┼───────────────────────────────────────────────────────────┐
A │ root│   ███           ███             ███              ███        │  AXIS 1 —
X │  ├─ │   ███           ███             ███              ███        │  STRUCTURAL
I │  │  │   ███           ███             ███              ███        │  ownership
S │  └─ │   ███           ███             ███              ███        │  (vertical /
  │ sub │   ███           ███             ███              ███        │  subtree-scoped)
1 │ (D) │   ███           ███             ███              ███        │  governs add /
  └─────┴───────────────────────────────────────────────────────────┘  move / delete
                                                                          of NODES
```

- **A cell = (node, column).**
- The cell's **existence and position** (was the node added here? moved? deleted?) is
  governed by **Axis 1 — structural ownership** of the node's branch.
- The cell's **value** is governed by **Axis 2 — column ownership** of its column.
- **The two axes are independent.** A column owner (Axis 2) can edit that column's value
  on *any* node, including nodes inside a branch they do **not** structurally own.
  A branch owner (Axis 1) can add/move/delete nodes in their subtree but may **not**
  set the value of a column they don't own.

### 2.1 Axis 1 — Structural ownership (vertical, subtree-scoped, **delegable**)

- Governs: `addNode`, `moveNode`, `deleteNode` (the *shape* of the tree).
- Scope: a **branch** (a subtree rooted at some node).
- Root authority: `Tree Sheet.structural_owner` (the root owner, persona **A**).
- **Delegation:** a branch owner may grant a sub-branch to another user via a
  **Branch Grant** (`scope = structure`). The grantee (persona **D**) becomes the
  approver for structural changes anywhere within that sub-branch. Delegation is
  itself a recorded event (`DELEGATION_CHANGED`) and is revocable.
- Non-owners may only **suggest** → a Change Request routed to the resolved approver.

#### Resolution algorithm (structural approver for node X)

```python
# arbor.acl.resolver.resolve_structural_approver
#
# Returns the User who is the authoritative approver for a STRUCTURAL change
# (add/move/delete) affecting node X. Walks ancestors self -> root; nearest
# active Branch Grant wins; falls back to the sheet's root structural_owner.
#
# Uses NestedSet (lft/rgt) so "ancestors of X" is a single indexed query.

def resolve_structural_approver(sheet, node):
    # node may be None for "add a new root-level node" — then authority is the
    # sheet root owner directly.
    if node is None:
        return sheet.structural_owner

    # ancestors_self = [node, parent, grandparent, ..., root], nearest first.
    # In NestedSet terms: all N where N.lft <= node.lft AND N.rgt >= node.rgt,
    # ordered by lft DESC (deepest/nearest ancestor first).
    for ancestor in ancestors_self(node, order="nearest_first"):
        grant = find_active_branch_grant(
            sheet=sheet,
            branch_root=ancestor,
            scope="structure",
            active=True,
        )
        if grant is not None:
            return grant.grantee          # nearest delegated sub-branch owner

    return sheet.structural_owner          # fallback: root owner "A"
```

For a **move**, both the *source parent* branch and the *destination parent* branch are
resolved; the actor must have authority over **both** (else the change is suggested to
the destination approver, with the source approver added as a required co-approver in
`payload.co_approvers`). For an **add**, the approver is resolved against the **intended
parent**. For a **delete**, against the node being deleted.

### 2.2 Axis 2 — Column ownership (horizontal, field-scoped)

- Governs: `updateCell` (the *value* of a cell), and schema ops on that column
  (`updateColumn`, `deleteColumn`).
- Authority is read directly off the **Tree Column** row — no tree walk:
  - `Tree Column.column_owner` — the owner (personas **B**, **C**, …), **and**
  - `Tree Column.editors` (child table of Users) — additional approvers/editors.
- Non-owners may only **suggest** → a Change Request routed to the column owner/editors.

```python
# arbor.acl.resolver.resolve_column_approvers
def resolve_column_approvers(column):
    approvers = {column.column_owner}
    approvers |= {row.user for row in column.editors}
    return approvers   # any one of these may approve / may edit directly
```

> **Why `column_owner + editors` (child table) and NOT a separate Column Grant DocType:**
> column authority is intrinsically *single-target* (it always attaches to exactly one
> Tree Column) and has no hierarchy to walk — unlike branch authority, which is
> subtree-scoped and delegable down a tree. Modeling it as fields on the Tree Column row
> keeps schema and its ownership co-located, makes the snapshot serializer trivial
> (ownership ships with the column config), and avoids a join on every cell edit. The
> `grantColumn` capability simply mutates `column_owner` / `editors`. A standalone grant
> DocType would add a special case for no expressive gain. **(Locked.)**

### 2.3 Cell-level composition

```python
# Authority to MUTATE a given cell value:
def can_edit_cell(actor, sheet, node, column):
    return actor in resolve_column_approvers(column)   # Axis 2 only

# Authority to ADD/MOVE/DELETE the node that the cell lives on:
def can_change_structure(actor, sheet, node):
    return actor == resolve_structural_approver(sheet, node)  # Axis 1 only
```

A node's **label** is *not* a hardcoded field — it is the value of a designated Tree
Column (e.g. `field = "name"`). Therefore editing a node's display label is an `updateCell`
on the label column and is governed by **Axis 2** (that column's owner), even though
creating the node was **Axis 1**. This is the model working as intended.

---

## 3. Meta-model data model

Schema is **data**, not Frappe fields. See [`DATA-MODEL.md`](./DATA-MODEL.md) for the
full field-by-field definitions. This section gives the ER-level picture and the key
modeling decisions.

### 3.1 Core DocTypes

| DocType | Role | Key fields |
|---|---|---|
| **Tree Sheet** | a table instance | `title`, `description`, `structural_owner`, `status`, `settings` (JSON policy flags) |
| **Tree Column** | schema definition (one row per column) | `sheet`, `field`, `label`, `type`, `options`, `width`, `editable`, `column_owner`, `editors[]` |
| **Tree Node** | tree structure (NestedSet) | `sheet`, `parent_tree_node`, `lft`, `rgt`, `is_group`, `idx` (ordering) |
| **Tree Node Value** | a single cell, keyed by (node, column) | `sheet`, `node`, `column`, `value`, `version` |
| **Branch Grant** | delegable structural ownership | `sheet`, `branch_root`, `grantee`, `scope=structure`, `granted_by`, `active` |
| **Change Request** | the suggest/approve unit | `sheet`, `target_kind`, `operation`, `payload`, `requester`, `resolved_approver`, `status`, `decided_by`, `decided_at`, `resulting_event` |
| **Subscription** | who watches what | `subscriber`, `subscriber_kind`, `scope`, `target`, `event_types`, `delivery`, `requires_ack` |
| **Notification** | one per (change, recipient) | `change_request`, `tree_event`, `recipient`, `delivered_at`, `channel` |
| **Acknowledgement** | one per (notification, user) | `notification`, `user`, `acked_at` |
| **Webhook Endpoint** | external subscriber | `url`, `secret`, `event_types`, `scope`, `target`, `active` |
| **Webhook Delivery** | one delivery attempt log | `endpoint`, `tree_event`, `status`, `attempts`, `last_response`, `next_retry_at`, `signature` |
| **Tree Event** | event-sourced log (append-only) | `sheet`, `type`, `payload`, `actor_type`, `actor`, `change_request` |

### 3.2 ER-style relationships

```
Tree Sheet 1───*  Tree Column        (sheet defines its columns)
Tree Sheet 1───*  Tree Node          (NestedSet: parent_tree_node, lft, rgt)
Tree Node  1───*  Tree Node          (parent_tree_node self-link)
(Tree Node × Tree Column) 1───1 Tree Node Value   (one cell per pair)
Tree Sheet 1───*  Branch Grant       (branch_root → Tree Node)
Tree Sheet 1───*  Change Request     (resulting_event → Tree Event)
Tree Sheet 1───*  Tree Event         (append-only)
Tree Event 1───*  Notification       (fan-out to recipients)
Notification 1──0..1 Acknowledgement (requires_ack subscriptions only)
Tree Event 1───*  Webhook Delivery   (fan-out to endpoints)
Tree Column 1───* (editors child rows of User)
```

### 3.3 Key modeling decisions (locked)

- **Tree Node Value = its own DocType, keyed by `(node, column)` — NOT a JSON blob on the
  node.** Rationale: *every cell gets its own audit trail, version counter, and
  field-level permission*. A JSON blob would force the whole row to share one permission
  and one version, defeating Axis 2 (per-column ownership) and the per-cell history the
  product promises. The `(node, column)` pair is unique. Cell value updates increment
  `version` and emit a `NODE_VALUE_UPDATED` event carrying `{node, column, old, new}`.
- **Tree Node uses Frappe NestedSet** (`lft`/`rgt`/`is_group`, `parent_tree_node`). This
  makes ancestor/descendant queries (the Axis-1 resolution walk and branch-scoped
  subscription matching) single indexed range queries rather than recursive CTEs.
- **The node label is a column value, not a field** (see §2.3).
- **Schema is mutable at runtime** by adding/removing Tree Column rows; each schema change
  emits `COLUMN_CONFIG_UPDATED` and reuses the same snapshot serializer.

---

## 4. The Capability Layer — the DRY hub

The **capability registry** (`arbor.capabilities.registry`) is the single Python source
of truth for everything Arbor can do. It mirrors the reference repo's
`capabilities/registry.ts`. See [`CAPABILITIES.md`](./CAPABILITIES.md) for the full table.

A **Capability** is a declarative record:

```python
Capability(
    id="updateCell",
    name="Update cell value",
    params_schema={...},          # JSON-schema; validated before execution
    axis="column",                # "structure" | "column" | "meta" | "none"
    is_exposed_to_llm=True,        # filters getLLMTools()
    acl_rule="resolve_column_approvers(column)",   # which resolver decides
    emits=["NODE_VALUE_UPDATED"], # Tree Event type(s)
    handler=update_cell_handler,  # the ONLY place the mutation lives
)
```

### 4.1 Four consumers, one registry

```
                    ┌──────────────────────────────────────┐
                    │   arbor.capabilities.registry         │
                    │   (single source of truth)            │
                    └───────────────┬──────────────────────┘
                                    │
            ┌───────────────┬───────┴────────┬───────────────────┐
            ▼               ▼                 ▼                   ▼
   (a) Web UI         (b) REST API     (c) Tree Event       (d) LLM agent
   executeAction      auto-exposed     stream → Webhooks    tools via
   (React shell)      endpoints +      + Notifications      getLLMTools()
                      whitelisted                           filtered by
                      methods                               is_exposed_to_llm
```

- **(a) Web UI `executeAction`** — the React frontend never calls a mutation directly; it
  calls one `executeAction(actionId, params)` endpoint. The UI's available buttons/menus
  are *generated* from the registry (and from `getSheetSnapshot`'s column config).
- **(b) REST API** — DocTypes get Frappe auto-REST; capabilities get whitelisted methods.
  See §8 endpoint map. The API and the Web UI share the **identical** capability + ACL
  path.
- **(c) Tree Event stream** — every successful capability emits exactly one Tree Event.
  The **notification dispatcher** and **webhook dispatcher** subscribe to this stream;
  they contain no mutation logic.
- **(d) LLM agent tools** — `getLLMTools()` returns the registry filtered to
  `is_exposed_to_llm == True`, rendered as LiteLLM/Claude tool definitions from each
  capability's `params_schema`. Destructive/global ops (`internalReset`) are
  `is_exposed_to_llm = False`.

### 4.2 The centralized `executeAction` flow

`arbor.capabilities.execute.execute_action(action_id, params, actor)` is the **one path**
all four surfaces funnel through. Humans, the agent, and API callers are indistinguishable
here except by `actor` / `actor_type`.

```python
def execute_action(action_id, params, actor):
    cap = registry.get(action_id)                       # 1. validate exists
    validate_schema(params, cap.params_schema)          # 2. validate params

    authority = resolve_authority(cap, params, actor)   # 3. resolve ACL on the
                                                        #    relevant axis/axes
    if authority.is_authorized:
        # 4a. AUTHORIZED → mutate + emit exactly one Tree Event
        result = cap.handler(params, actor)             # the only mutation site
        event = emit_event(                             # one event emitter
            sheet=params["sheet"], type=cap.emits_primary,
            payload=result.event_payload,
            actor=actor, actor_type=actor.actor_type,
            change_request=None,
        )
        return Outcome(kind="executed", event=event, result=result)
    else:
        # 4b. NOT AUTHORIZED → create a Change Request (suggest), do NOT mutate
        cr = create_change_request(
            sheet=params["sheet"],
            target_kind=cap.target_kind,                # node-structure|cell-value|column-schema
            operation=cap.operation,                    # add|update|move|delete
            payload=params,
            requester=actor,
            resolved_approver=authority.resolved_approver,
        )
        event = emit_event(                             # same emitter
            sheet=params["sheet"], type="CHANGE_PROPOSED",
            payload={"change_request": cr.name, "action": action_id},
            actor=actor, actor_type=actor.actor_type,
            change_request=cr.name,
        )
        return Outcome(kind="suggested", change_request=cr, event=event)
```

**This is the governance keystone:** *the same call either mutates or becomes a
suggestion, decided purely by ACL.* No surface has a privileged path. The agent (§7),
lacking authority, produces Change Requests exactly as a human non-owner would.

### 4.3 Named shared modules (DRY mandate)

Implementers **must reuse** these; no surface re-implements them:

| Module | Path | Responsibility |
|---|---|---|
| Capability registry | `arbor.capabilities.registry` | declarative capability records + `getLLMTools()` |
| Centralized executor | `arbor.capabilities.execute` | `execute_action` (the only mutation entrypoint) |
| ACL resolver | `arbor.acl.resolver` | `resolve_structural_approver`, `resolve_column_approvers`, `resolve_authority` |
| Event emitter | `arbor.events.emitter` | `emit_event` (the only Tree Event writer) |
| Snapshot serializer | `arbor.snapshot.serializer` | `get_sheet_snapshot` (one shape for web/api/agent) |
| Notification dispatcher | `arbor.notify.dispatcher` | fan-out from Tree Event → Notification/Acknowledgement |
| Webhook dispatcher | `arbor.webhooks.dispatcher` | fan-out from Tree Event → Webhook Delivery (HMAC + retries) |

---

## 5. Change Request / approval lifecycle

A **Change Request (CR)** is a *deferred capability call*: it stores the exact
`{action_id == operation/target_kind, payload, actor}` that failed the authority check,
plus the `resolved_approver`. Approving it re-runs the capability handler **as the
approver**, guaranteeing the same mutation path and the same emitted event.

### 5.1 State machine

```
                 suggestChange / executeAction(no authority)
                              │
                              ▼
                       ┌────────────┐
        withdrawChange  │  PROPOSED  │  (resolved_approver assigned;
        (by requester)  └─────┬──────┘   CHANGE_PROPOSED event emitted;
              │               │           notifications + webhooks fan out)
              │      ┌────────┴────────┐
              ▼      ▼                 ▼
        ┌───────────┐  approveChange   rejectChange
        │ WITHDRAWN │  (by approver)   (by approver)
        └───────────┘     │                 │
                          ▼                 ▼
                   ┌────────────┐    ┌────────────┐
                   │  APPROVED  │    │  REJECTED  │
                   └─────┬──────┘    └────────────┘
                         │
                         │ on approve: re-run capability handler AS approver,
                         │ emit the REAL mutation event (e.g. NODE_VALUE_UPDATED),
                         │ then emit CHANGE_APPROVED; set resulting_event link.
                         ▼
                  (state is terminal; resulting_event populated)
```

- **States:** `proposed → approved | rejected | withdrawn`. `approved`, `rejected`,
  `withdrawn` are **terminal**.
- **`approveChange`** is the only transition that produces a real data mutation. It calls
  `cap.handler(cr.payload, actor=cr.resolved_approver)` and links the resulting Tree Event
  to `cr.resulting_event`, then emits `CHANGE_APPROVED`.
- **`rejectChange`** emits `CHANGE_REJECTED`; no data mutation.
- **`withdrawChange`** (requester-only) is silent to approvers except as a status change.
- Only the `resolved_approver` (or an editor in the column case) may approve/reject.
  Re-resolution happens at decision time if the tree/grants changed since proposal
  (`resolved_approver` is recomputed and the CR is re-routed if stale).
- **Owner self-suggest policy:** if `Tree Sheet.settings.owners_must_use_change_requests`
  is true, even an authorized owner's `executeAction` produces a CR (the owner becomes
  their own `resolved_approver`), forcing an audit/peer-review trail.

---

## 6. Notification + acknowledgement ledger

Notifications are **derived from the Tree Event stream**, not emitted by capabilities
directly. The **notification dispatcher** (`arbor.notify.dispatcher`) runs on each new
Tree Event:

```python
def on_tree_event(event):
    for sub in matching_subscriptions(event):    # scope=sheet|branch|column, event_types
        notif = create_notification(
            tree_event=event.name,
            change_request=event.change_request,
            recipient=sub.subscriber,
            channel=sub.delivery,                 # in-app | email | webhook
        )
        deliver(notif, sub.delivery)              # sets delivered_at
        if sub.requires_ack:
            mark_ack_required(notif)              # awaits Acknowledgement row
```

- **Subscription matching** uses NestedSet ranges for `scope = branch` (event's node is a
  descendant of `target`), direct equality for `scope = column`, and sheet-level match for
  `scope = sheet`.
- **Notification** = one row per `(tree_event, recipient)` with `delivered_at` + `channel`.
- **Acknowledgement** = one row per `(notification, user, acked_at)`, created by the
  `acknowledge` capability.
- **Accountability report** ("N notified / M acked") is a straight aggregate:
  `count(Notification where requires_ack) ` vs `count(Acknowledgement)` for a given
  Tree Event or Change Request. This powers the "sensitive subscriber" persona (**G**),
  whose subscription has `requires_ack = true`.

---

## 7. Webhook event model + delivery semantics

External systems subscribe to **event types** via **Webhook Endpoint** rows. The
**webhook dispatcher** (`arbor.webhooks.dispatcher`) also subscribes to the Tree Event
stream (same fan-out as notifications — DRY).

- **Payload:** the serialized Tree Event (`type`, `sheet`, `payload`, `actor`,
  `actor_type`, `change_request`, `timestamp`, `event_id`).
- **Signature (HMAC):** each delivery includes header
  `X-Arbor-Signature: sha256=<hmac>` where `hmac = HMAC-SHA256(endpoint.secret, raw_body)`.
  An `X-Arbor-Event-Id` header enables idempotent consumption.
- **Retries:** exponential backoff with jitter; `Webhook Delivery` tracks `attempts`,
  `status` (`pending | delivered | failed | exhausted`), `last_response`, `next_retry_at`.
  Default schedule: 6 attempts over ~24h (e.g. 0s, 30s, 5m, 30m, 2h, 12h). A delivery is
  `delivered` on 2xx; non-2xx/timeout reschedules until `exhausted`.
- **Delivery log:** every attempt is appended; the log is queryable per endpoint for
  audit. (Build on Frappe's `Webhook` where convenient, but the subscription/delivery
  state is modeled explicitly as above so retries and HMAC are first-class.)

Webhooks are *just another consumer of the Tree Event stream* — the dispatcher contains
no mutation logic and never bypasses `executeAction`.

---

## 8. Server-side Re-Act agent

The agent runs **in Frappe (Python)**, never in the browser.

- **Provider adapter:** **LiteLLM** — provider-agnostic. Default **Claude**; swappable to
  Gemini/OpenAI via config. Each org **brings its own key** (stored per-site, never in
  core source).
- **Agent identity:** the agent acts with its **own Frappe User identity**. It is
  therefore subject to the **same two-axis ACL** as any human. **Key governance property:**
  when the agent lacks authority, its `executeAction` calls become **Change Requests
  routed for human approval** — exactly the §4.2 (4b) branch. The agent cannot escalate
  privilege by being an agent.
- **Tools:** `getLLMTools()` from the registry, filtered by `is_exposed_to_llm`. The agent
  literally calls the same capabilities humans do.
- **Re-Act loop:**

  ```
  Thought      → reason about the user's request
  Action       → getSheetSnapshot(...)            (read; shared serializer)
  Observation  → snapshot
  Thought      → decide a mutation
  Action       → updateCell(...) / addNode(...) / suggestChange(...)
                 → execute_action(...) → mutates OR becomes a Change Request
  Observation  → Outcome(kind="executed" | "suggested")
  ...          → loop
  Final        → natural-language summary (e.g. "I updated 3 cells and filed
                 2 change requests for B's approval")
  ```

- **Web surface:** exposed via an `agent.chat` endpoint; the React sidebar is a **thin
  shell** that streams the loop. Because the agent is server-side, it is **equally
  available to API/headless consumers** (same endpoint).
- **Module:** `arbor.agent.react` (loop) + `arbor.agent.provider` (LiteLLM adapter) +
  `arbor.agent.tools` (`getLLMTools` binding). The agent owns **zero** mutation logic.

---

## 9. API as a first-class peer to Web

Everything doable in the UI is a documented REST call. The API and Web share the
**identical capability + ACL path** (§4.2). Two layers:

1. **Frappe auto-REST for DocTypes** — read/list/query for `Tree Sheet`, `Tree Column`,
   `Tree Node`, `Tree Node Value`, `Change Request`, `Tree Event`, etc.
   (writes to governed DocTypes go through capabilities, not raw REST).
2. **Whitelisted capability methods** — one method per capability, all funneling into
   `execute_action`.

### 8.1 Endpoint map

| Surface | Method / Path | Maps to |
|---|---|---|
| Execute any capability | `POST /api/method/arbor.execute_action` `{action_id, params}` | `execute_action` |
| Snapshot | `GET /api/method/arbor.get_sheet_snapshot?sheet=…` | `getSheetSnapshot` (shared serializer) |
| Add node | `POST /api/method/arbor.add_node` | `addNode` (Axis 1) |
| Update cell | `POST /api/method/arbor.update_cell` | `updateCell` (Axis 2) |
| Move / delete node | `POST /api/method/arbor.move_node` · `…delete_node` | `moveNode` · `deleteNode` (Axis 1) |
| Add / update / delete column | `POST /api/method/arbor.add_column` · `…update_column` · `…delete_column` | meta-schema |
| Suggest / approve / reject / withdraw | `POST /api/method/arbor.suggest_change` · `…approve_change` · `…reject_change` · `…withdraw_change` | CR lifecycle |
| Subscribe / unsubscribe / acknowledge | `POST /api/method/arbor.subscribe` · `…unsubscribe` · `…acknowledge` | notification ledger |
| Delegate / revoke branch / grant column | `POST /api/method/arbor.delegate_branch` · `…revoke_delegation` · `…grant_column` | ownership admin |
| Agent chat | `POST /api/method/arbor.agent.chat` `{sheet, message}` | server-side Re-Act agent |
| DocType read/list | `GET /api/resource/Tree Event?filters=…` | Frappe auto-REST |
| Webhook (inbound to consumer) | consumer's `url`, `X-Arbor-Signature`, `X-Arbor-Event-Id` | webhook dispatcher |

Auth: standard Frappe API keys / OIDC bearer tokens (see §10). An **external system**
persona is exactly an API consumer + a Webhook Endpoint subscriber.

---

## 10. SSO isolation seam

Authentication is a **pluggable provider interface**. The open-source **core** depends
only on the interface; the employee SSO integration lives in a **separate,
isolated app** injected only in a private deployment.

```
┌────────────────────────────────────────────┐
│  arbor (open-source CORE)                    │
│   depends on → AuthProvider (interface)      │
│   ships → LocalAuthProvider (Frappe email)   │
│           OIDCAuthProvider (generic OAuth2)  │
└───────────────────────┬─────────────────────┘
                        │  (interface seam)
                        ▼
┌────────────────────────────────────────────┐
│  arbor_sso_overlay   (SEPARATE app,          │
│   NOT in open-source core)                   │
│   implements → AuthProvider using an         │
│   employee SSO SDK                            │
│   injected only in a private deployment       │
└────────────────────────────────────────────┘
```

```python
# arbor.auth.provider  (CORE — open-source)
class AuthProvider(Protocol):
    def authenticate(self, request) -> AuthResult: ...      # → Frappe User / session
    def get_login_url(self, redirect) -> str: ...
    def handle_callback(self, request) -> AuthResult: ...
    def map_identity(self, claims) -> UserIdentity: ...      # external → Arbor User

# Core ships: LocalAuthProvider, OIDCAuthProvider (generic).
# arbor_sso_overlay ships: EmployeeSSOProvider(AuthProvider) using an
#   employee SSO SDK (an employee-SSO SDK integration pattern — NOT vendored here).
```

- The active provider is selected by site config (`arbor.auth.provider_class`).
- **Everything except `arbor_sso_overlay` is open-source-ready.** No SSO-overlay SDK
  import appears anywhere in core. Test suites for core mock `AuthProvider`.

---

## 11. Surface parity guarantee (test anchor)

For any capability `C` and actor `actor`:

```
execute_action(C, params, actor)   ≡   POST /api/method/arbor.<C>(params)   [as actor]
                                   ≡   agent calling tool C (params)         [as agent user]
```

All three resolve the same ACL, run the same handler (or create the same Change Request),
and emit the same Tree Event. This identity is the primary invariant test authors should
assert against.
