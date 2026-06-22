# Arbor — Permissions (Two-Axis ACL Resolver)

> The complete authority model with worked persona examples. Companion to
> [`ARCHITECTURE.md`](./ARCHITECTURE.md) §2 and [`CAPABILITIES.md`](./CAPABILITIES.md).
> This is the canonical reference for ACL test cases.

## 1. The resolver (`arbor.acl.resolver`)

Two **orthogonal** axes, resolved independently, composed at the cell level. There is
**one** resolver module, reused by web, API, and agent.

```python
# ---------- Axis 1: STRUCTURAL (vertical, subtree, delegable) ----------
def resolve_structural_approver(sheet, node):
    """Approver for add/move/delete affecting `node`. Nearest active Branch Grant
    on the ancestor chain wins; else the sheet's root structural_owner."""
    if node is None:                                  # add at root level
        return sheet.structural_owner
    for ancestor in ancestors_self(node, order="nearest_first"):
        grant = find_active_branch_grant(sheet, branch_root=ancestor,
                                         scope="structure", active=True)
        if grant:
            return grant.grantee
    return sheet.structural_owner

# ---------- Axis 2: COLUMN (horizontal, field-scoped, not delegable down a tree) ----------
def resolve_column_approvers(column):
    """Set of users who may edit/approve this column's values."""
    return {column.column_owner} | {e.user for e in column.editors}

# ---------- Composition in execute_action ----------
def resolve_authority(cap, params, actor):
    if cap.axis == "structure":
        if cap.id == "moveNode":
            src  = resolve_structural_approver(sheet, node)
            dest = resolve_structural_approver(sheet, new_parent)
            authorized = actor in {src, dest} and actor == src and actor == dest
            approver   = dest                      # route to dest; src as co-approver
        else:
            approver   = resolve_structural_approver(sheet, target_node)
            authorized = (actor == approver)
    elif cap.axis == "column":
        approvers  = resolve_column_approvers(column)
        authorized = (actor in approvers)
        approver   = column.column_owner           # CR routes to owner
    elif cap.axis == "meta":
        ...                                        # addColumn → sheet owner; up/del → column approvers
    return Authority(is_authorized=authorized, resolved_approver=approver)
```

### 1.1 Axis independence (the core invariant)

- A **column owner** (Axis 2) edits that column's value on **any** node, even inside a
  branch they do not structurally own.
- A **branch owner** (Axis 1) adds/moves/deletes nodes in their subtree but **cannot** set
  the value of a column they do not own.
- A **cell = (node, column)**: existence/position governed by Axis 1; value by Axis 2.
- The node **label** is the value of the `is_label` column → editing it is **Axis 2**.

### 1.2 Owner-self policy

If `Tree Sheet.settings.owners_must_use_change_requests` is true, an authorized owner's
`execute_action` still creates a Change Request with the owner as their own
`resolved_approver` (forced audit trail). Otherwise authorized actions mutate directly.

---

## 2. Personas & sheet setup

Sheet `S`, root structural owner **A**. Tree (NestedSet):

```
root R               (struct authority: A)
├── P1               (struct authority: A)
│   └── X            (struct authority: A)
└── P2  ────────────  Branch Grant: grantee = D, active   (struct authority: D)
    ├── Y            (struct authority: D, inherited)
    └── Z            (struct authority: D, inherited)
```

Columns: `col:name` (is_label, owner **B**), `col:status` (owner **C**, editors: [B]),
`col:budget` (owner **C**), `col:notes` (owner **B**).

Personas: **A** root structural owner; **B**, **C** column owners; **D** delegated
sub-branch (P2) owner; **E**, **F** suggest-only users (no grants); **G** sensitive
subscriber with `requires_ack`; **EXT** external system (API consumer + webhook endpoint).

---

## 3. Worked examples

### A — root structural owner

| Action | Resolution | Outcome |
|---|---|---|
| `addNode(parent=P1)` | `resolve_structural_approver(P1)` walk → no grant on P1/root → **A** | **A == approver → executes**, `NODE_CREATED` |
| `deleteNode(X)` | walk X→P1→root, no grant → **A** | executes, `NODE_DELETED` |
| `moveNode(X → P2)` | src approver = A; dest approver = **D** (P2 grant). A ≠ D | **not authorized at dest → Change Request to D** (A listed co-approver) |
| `updateCell(X, col:budget)` | Axis 2: approvers = {C} | A ∉ {C} → **Change Request to C** |

> A owns *structure* globally (minus delegations) but owns **no columns** → A must suggest
> value edits. Demonstrates axis independence.

### B — column owner (col:name, col:notes; editor on col:status)

| Action | Resolution | Outcome |
|---|---|---|
| `updateCell(Z, col:name)` | Axis 2: {B} | executes (even though Z is in D's branch) → `NODE_VALUE_UPDATED` |
| `updateCell(X, col:status)` | Axis 2: {C, B(editor)} | B ∈ set → executes |
| `updateCell(X, col:budget)` | Axis 2: {C} | B ∉ → **Change Request to C** |
| `addNode(parent=P1)` | Axis 1: approver A | B ≠ A → **Change Request to A** |

> B edits owned columns on **any** node (including D's subtree) — Axis 2 ignores branch
> structure. B has no structural authority → structural ops suggest to A.

### C — column owner (col:status, col:budget)

| Action | Resolution | Outcome |
|---|---|---|
| `updateCell(Y, col:budget)` | {C} | executes (Y in D's branch; irrelevant to Axis 2) |
| `deleteColumn(col:budget)` | meta → column approvers {C} | executes → `COLUMN_CONFIG_UPDATED` |
| `moveNode(Y → P1)` | Axis 1: src D, dest A; C is neither | **Change Request** (routed to dest A) |

### D — delegated sub-branch owner (Branch Grant on P2)

| Action | Resolution | Outcome |
|---|---|---|
| `addNode(parent=Y)` | walk Y→P2(grant D)→… → nearest active grant = **D** | executes, `NODE_CREATED` |
| `deleteNode(Z)` | walk Z→P2(D) → **D** | executes, `NODE_DELETED` |
| `addNode(parent=P1)` | walk P1→root, no grant → **A** | D ≠ A → **Change Request to A** (delegation is scoped to P2 subtree) |
| `updateCell(Y, col:status)` | Axis 2: {C, B} | D ∉ → **Change Request to C** |
| `delegateBranch(Z → someone)` | `resolve_structural_approver(Z)` = D | D may sub-delegate within own branch → `DELEGATION_CHANGED` |

> D's authority is **exactly** the P2 subtree (NestedSet range), and **only structural**.
> Outside P2, or for column values, D must suggest. Sub-delegation is allowed.

### E / F — suggest-only users (no grants, no columns)

| Action | Resolution | Outcome |
|---|---|---|
| `updateCell(X, col:status)` (E) | {C, B} | E ∉ → **Change Request to C** |
| `addNode(parent=P2)` (F) | Axis 1: D | F ≠ D → **Change Request to D** |
| `suggestChange(...)` (E) | always allowed | creates Change Request directly → `CHANGE_PROPOSED` |
| `approveChange(cr)` (E, where cr.resolved_approver=C) | actor ≠ approver | **rejected by ACL** (not allowed) |

> Every mutating attempt by E/F becomes a Change Request routed to the resolved approver.
> They may withdraw their own CRs but cannot approve others'.

### G — sensitive subscriber with acknowledgement

Setup: `subscribe(scope=branch, target=P2, event_types=[CHANGE_APPROVED,NODE_DELETED],
delivery=in-app, requires_ack=true)`.

| Event | Dispatcher behavior |
|---|---|
| D approves a CR deleting Z (in P2) | `CHANGE_APPROVED` + `NODE_DELETED` match G's branch sub → **Notification** rows for G, `requires_ack=1` |
| G calls `acknowledge(notification)` | **Acknowledgement** row, `acked_at` set |
| Accountability report for that event | `N notified` = count(Notification requires_ack) ; `M acked` = count(Acknowledgement) → "1 notified / 1 acked" |

> G has **no edit authority**; G is a watcher. `requires_ack` makes the
> notified-vs-acked ledger auditable (ARCHITECTURE §6).

### EXT — external system (API consumer + webhook)

| Channel | Behavior |
|---|---|
| **API write** `POST /api/method/arbor.update_cell` (as EXT's User) | identical path to web/agent: resolves Axis 2; if EXT owns/edits the column → executes, else → Change Request. **Surface parity** (ARCHITECTURE §11). |
| **API read** `GET /api/resource/Tree Event?filters=...` | Frappe auto-REST read |
| **Webhook in** Webhook Endpoint subscribed to `[NODE_VALUE_UPDATED, CHANGE_APPROVED]` | on each matching Tree Event, Webhook Delivery POSTs payload with `X-Arbor-Signature: sha256=<hmac(secret, body)>`, `X-Arbor-Event-Id`; retries w/ backoff until delivered/exhausted (ARCHITECTURE §7) |

> An external system has no special privileges: as an API writer it is bound by the same
> two-axis ACL; as a webhook subscriber it is a derived consumer of the event stream.

---

## 4. ACL test invariants (for test authors)

1. **Axis independence:** column owner edits owned column on a node in a branch they
   don't own → executes. Branch owner edits a non-owned column → Change Request.
2. **Nearest-grant wins:** with grants on both P2 (D) and Z (D2), a structural change on a
   child of Z resolves to **D2**, not D.
3. **Root fallback:** no grant on the ancestor chain → `structural_owner` (A).
4. **Move requires both ends:** `moveNode` authorized only if actor is approver of both
   src and dest; otherwise Change Request to dest with src co-approver.
5. **Agent = human under ACL:** agent (own User) lacking authority produces a Change
   Request identical to a human non-owner's.
6. **Surface parity:** web `executeAction`, REST method, and agent tool for the same
   capability + actor produce identical authority decision, mutation, and Tree Event.
7. **Approver-only decisions:** only `resolved_approver` (or a column editor) may approve;
   only the requester may withdraw.
8. **Owner-self policy:** with `owners_must_use_change_requests=true`, an owner's direct
   action still yields a CR (self-approver).
