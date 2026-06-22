# Test-Case Catalog — Permissions & Delegation

> **Surface:** The two orthogonal ownership axes end to end — structural (Axis 1,
> delegable via Branch Grant) and column (Axis 2, `column_owner` + `editors`), their
> independence, suggest-routing to the resolved approver, and the delegation lifecycle.
>
> **Canonical refs:** [`ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §2, §4.2, §5, §11 ·
> [`PERMISSIONS.md`](../docs/PERMISSIONS.md) (all) · [`CAPABILITIES.md`](../docs/CAPABILITIES.md).
>
> **Test-first:** written before implementation. IDs are stable contract anchors.

---

## Shared canonical fixtures (DRY — referenced by every case, never redefined per-test)

Assume a fixtures module provides these. Tests **reference** them; they do **not** invent
bespoke worlds.

### Personas (Frappe Users)
- **A** — root structural owner of sheet `S` (`Tree Sheet.structural_owner = A`). Owns no columns.
- **B** — column owner of `col:name` (is_label) and `col:notes`; **editor** on `col:status`.
- **C** — column owner of `col:status` and `col:budget`.
- **D** — delegated sub-branch owner: active Branch Grant `BG_P2` (`branch_root = P2`, `scope=structure`, `granted_by = A`, `active = 1`). No column ownership.
- **E**, **F** — suggest-only users: no Branch Grant, no column ownership/editorship.
- **G** — sensitive subscriber: subscription `scope=branch, target=P2, event_types=[CHANGE_APPROVED, NODE_DELETED], delivery=in-app, requires_ack=true`. No edit authority.
- **EXT** — external system: a Frappe User (API key) + a Webhook Endpoint subscribed to `[NODE_VALUE_UPDATED, CHANGE_APPROVED]`.

### Sample sheet `S` — tree (NestedSet, nearest-first walk = self→root)
```
root R               (struct authority: A)
├── P1               (struct authority: A)
│   └── X            (struct authority: A)
└── P2  ── BG_P2: grantee=D, active   (struct authority: D)
    ├── Y            (struct authority: D, inherited)
    └── Z            (struct authority: D, inherited)
```

### Columns of `S`
| column | is_label | column_owner | editors |
|---|---|---|---|
| `col:name`   | yes | B | — |
| `col:status` | no  | C | [B] |
| `col:budget` | no  | C | — |
| `col:notes`  | no  | B | — |

### Sheet settings
- Default: `settings.owners_must_use_change_requests = false` (unless a case states otherwise).

### Capability/event vocabulary referenced
Capabilities: `addNode`, `updateCell`, `moveNode`, `deleteNode`, `addColumn`,
`updateColumn`, `deleteColumn`, `suggestChange`, `approveChange`, `rejectChange`,
`withdrawChange`, `delegateBranch`, `revokeDelegation`, `grantColumn`.
Events: `NODE_CREATED`, `NODE_VALUE_UPDATED`, `NODE_MOVED`, `NODE_DELETED`,
`COLUMN_CONFIG_UPDATED`, `CHANGE_PROPOSED`, `CHANGE_APPROVED`, `CHANGE_REJECTED`,
`DELEGATION_CHANGED`.

**Global invariant under test for every mutating case:** `execute_action` emits
**exactly one** Tree Event — either the capability's `emits` event (authorized) or
`CHANGE_PROPOSED` (unauthorized). Never both, never zero.

---

## Section 1 — Axis 1: structural authority & ancestor-walk resolution

### PERMISSIONS_AND_DELEGATION-001
- **Title:** Root owner A adds a node under a branch with no grant → executes
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **A**.
- **Given** node `P1` has no Branch Grant on its ancestor chain (P1→R).
- **When** A calls `addNode(sheet=S, parent=P1)`.
- **Then** `resolve_structural_approver(S, P1)` returns **A**; `actor == approver` → authorized; a new Tree Node is created under P1; outcome `kind="executed"`.
- **Covers:** `addNode` · `NODE_CREATED`.

### PERMISSIONS_AND_DELEGATION-002
- **Title:** Root owner A deletes deep node X → executes (walk finds no grant)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **A**.
- **Given** the walk X→P1→R contains no active Branch Grant.
- **When** A calls `deleteNode(sheet=S, node=X)`.
- **Then** approver resolves to **A**; authorized; node X removed; outcome `executed`.
- **Covers:** `deleteNode` · `NODE_DELETED`.

### PERMISSIONS_AND_DELEGATION-003
- **Title:** addNode at root level (parent=null) resolves directly to sheet structural_owner
- **Level:** unit
- **Preconditions / fixtures:** Shared `S`; resolver under test.
- **Given** `node is None` branch of `resolve_structural_approver`.
- **When** `resolve_structural_approver(S, None)` is called.
- **Then** it returns `S.structural_owner` (**A**) without any ancestor walk.
- **Covers:** `addNode` (resolver) · *(no event; resolver-level)*.

### PERMISSIONS_AND_DELEGATION-004
- **Title:** Delegated owner D adds node under Y (inside P2) → executes
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **D**, grant `BG_P2`.
- **Given** the walk Y→P2→R; nearest active grant is `BG_P2` (grantee D).
- **When** D calls `addNode(sheet=S, parent=Y)`.
- **Then** approver = **D**; authorized; node created; outcome `executed`.
- **Covers:** `addNode` · `NODE_CREATED`.

### PERMISSIONS_AND_DELEGATION-005
- **Title:** Delegated owner D deletes Z (inside P2) → executes
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **D**.
- **Given** walk Z→P2→R; nearest grant `BG_P2`.
- **When** D calls `deleteNode(sheet=S, node=Z)`.
- **Then** approver = **D**; authorized; Z deleted; outcome `executed`.
- **Covers:** `deleteNode` · `NODE_DELETED`.

### PERMISSIONS_AND_DELEGATION-006
- **Title:** Delegation is subtree-scoped — D acting OUTSIDE P2 must suggest to A
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **D**.
- **Given** `addNode(parent=P1)`: walk P1→R has no grant (BG_P2 is not an ancestor of P1).
- **When** D calls `addNode(sheet=S, parent=P1)`.
- **Then** approver = **A**; `D != A` → unauthorized → a Change Request is created with `resolved_approver=A`, `target_kind=node-structure`, `operation=add`, `payload` = original params; outcome `kind="suggested"`. No node is created.
- **Covers:** `addNode` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-007
- **Title:** Non-owner F adds under P2 → Change Request routed to delegated owner D (not A)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **F**.
- **Given** walk P2→R; nearest grant `BG_P2` → approver D.
- **When** F calls `addNode(sheet=S, parent=P2)`.
- **Then** unauthorized; CR created with `resolved_approver=D` (delegation routing wins over root A); outcome `suggested`.
- **Covers:** `addNode` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-008
- **Title:** Non-owner E deletes deep node X (under A's branch) → CR routed to A
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **E**.
- **Given** walk X→P1→R, no grant → approver A.
- **When** E calls `deleteNode(sheet=S, node=X)`.
- **Then** unauthorized; CR `resolved_approver=A`, `operation=delete`; outcome `suggested`; X still present.
- **Covers:** `deleteNode` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-009
- **Title:** Nearest-grant-wins — nested delegation D2 on Z shadows D on P2
- **Level:** integration
- **Preconditions / fixtures:** Shared `S` + an additional active grant `BG_Z` (`branch_root=Z`, grantee **D2**, granted_by D). (Created via D's `delegateBranch`; see -040.)
- **Given** a structural change on a **child of Z** (node `Z1` under Z).
- **When** any actor resolves `resolve_structural_approver(S, Z1)`; walk Z1→Z(BG_Z:D2)→P2(BG_P2:D)→R.
- **Then** the **nearest** active grant `BG_Z` wins → approver = **D2**, not D, not A.
- **Covers:** `delegateBranch`/`addNode` (resolver) · *(resolver-level)*.

### PERMISSIONS_AND_DELEGATION-010
- **Title:** Root fallback — no grant anywhere on chain resolves to structural_owner A
- **Level:** unit
- **Preconditions / fixtures:** Shared `S` with `BG_P2` revoked/inactive for this case.
- **Given** all Branch Grants inactive.
- **When** `resolve_structural_approver(S, Z)` (walk Z→P2→R, all grants inactive).
- **Then** returns **A** (sheet root fallback).
- **Covers:** `addNode`/`deleteNode` (resolver).

### PERMISSIONS_AND_DELEGATION-011
- **Title:** Inactive Branch Grant is ignored by resolution
- **Level:** unit
- **Preconditions / fixtures:** Shared `S` but `BG_P2.active = 0`.
- **Given** `find_active_branch_grant` filters on `active=True`.
- **When** `resolve_structural_approver(S, Y)`.
- **Then** the inactive `BG_P2` is skipped; walk continues to R → returns **A**.
- **Covers:** `delegateBranch`/`revokeDelegation` (resolver).

---

## Section 2 — moveNode: dual-end (src + dest) authority

### PERMISSIONS_AND_DELEGATION-012
- **Title:** A moves X within A's own region (src=P1, dest=R) → executes (both ends A)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **A**.
- **Given** src parent P1 → approver A; dest parent R → approver A.
- **When** A calls `moveNode(sheet=S, node=X, new_parent=R)`.
- **Then** `actor == src_approver AND actor == dest_approver` → authorized; X reparented; outcome `executed`.
- **Covers:** `moveNode` · `NODE_MOVED`.

### PERMISSIONS_AND_DELEGATION-013
- **Title:** A moves X into D's branch (dest=P2) → CR to D with A as co-approver
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **A**.
- **Given** src parent P1 → A; dest parent P2 → D; `A != D`.
- **When** A calls `moveNode(sheet=S, node=X, new_parent=P2)`.
- **Then** not authorized (must equal **both** ends); CR created routed to **dest approver D** as `resolved_approver`; `payload.co_approvers` includes the **src approver A**; outcome `suggested`. X not moved.
- **Covers:** `moveNode` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-014
- **Title:** D moves Y out of P2 into P1 → CR to dest A (D owns src only)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **D**.
- **Given** src parent P2 → D; dest parent P1 → A; D owns src but not dest.
- **When** D calls `moveNode(sheet=S, node=Y, new_parent=P1)`.
- **Then** unauthorized (dest fails); CR `resolved_approver=A` (dest); `co_approvers` includes **D** (src); outcome `suggested`.
- **Covers:** `moveNode` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-015
- **Title:** D moves Z within own P2 subtree (src=P2, dest=Y) → executes (both ends D)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **D**.
- **Given** both src parent P2 and dest parent Y resolve to D (both inside delegated branch).
- **When** D calls `moveNode(sheet=S, node=Z, new_parent=Y)`.
- **Then** authorized at both ends; Z moved under Y; outcome `executed`.
- **Covers:** `moveNode` · `NODE_MOVED`.

### PERMISSIONS_AND_DELEGATION-016
- **Title:** Column owner C attempts moveNode (neither src nor dest owner) → CR to dest
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **C** (column owner only).
- **Given** `moveNode(node=Y, new_parent=P1)`: src D, dest A; C is neither.
- **When** C calls `moveNode`.
- **Then** unauthorized; CR routed to dest **A**, `co_approvers=[D]`; outcome `suggested`. Demonstrates column authority confers **no** structural authority.
- **Covers:** `moveNode` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-017
- **Title:** moveNode to root level (new_parent=null) resolves dest to sheet owner A
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **D**.
- **Given** `new_parent=null` → dest approver = `structural_owner` A; src parent P2 → D.
- **When** D calls `moveNode(sheet=S, node=Z, new_parent=null)`.
- **Then** dest A ≠ D → unauthorized; CR to A, co_approver D; outcome `suggested`.
- **Covers:** `moveNode` · `CHANGE_PROPOSED`.

---

## Section 3 — Axis 2: column authority (owner + editors)

### PERMISSIONS_AND_DELEGATION-018
- **Title:** Column owner B edits col:name on Z (inside D's branch) → executes (axis independence)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **B**.
- **Given** `resolve_column_approvers(col:name) = {B}`; node Z lives in D's structural branch but Axis 2 ignores structure.
- **When** B calls `updateCell(sheet=S, node=Z, column=col:name, value="new")`.
- **Then** B ∈ approvers → authorized; cell `(Z, col:name)` value updated; `version` incremented; outcome `executed`.
- **Covers:** `updateCell` · `NODE_VALUE_UPDATED`.

### PERMISSIONS_AND_DELEGATION-019
- **Title:** Editor B (not owner) edits col:status → executes (editors are approvers)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **B** (editor on col:status).
- **Given** `resolve_column_approvers(col:status) = {C, B}`.
- **When** B calls `updateCell(sheet=S, node=X, column=col:status, value="done")`.
- **Then** B ∈ approvers via editors child table → authorized; outcome `executed`.
- **Covers:** `updateCell` · `NODE_VALUE_UPDATED`.

### PERMISSIONS_AND_DELEGATION-020
- **Title:** Column owner C edits col:budget on Y (inside D's branch) → executes
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **C**.
- **Given** `{C}` owns col:budget; Y in D's branch is irrelevant to Axis 2.
- **When** C calls `updateCell(sheet=S, node=Y, column=col:budget, value=100)`.
- **Then** authorized; outcome `executed`.
- **Covers:** `updateCell` · `NODE_VALUE_UPDATED`.

### PERMISSIONS_AND_DELEGATION-021
- **Title:** Column owner B edits a column they do NOT own (col:budget) → CR to C
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **B**.
- **Given** `resolve_column_approvers(col:budget) = {C}`; B ∉.
- **When** B calls `updateCell(sheet=S, node=X, column=col:budget, value=50)`.
- **Then** unauthorized; CR created `target_kind=cell-value`, `operation=update`, `resolved_approver=C` (the column_owner); outcome `suggested`. Cell unchanged.
- **Covers:** `updateCell` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-022
- **Title:** Root structural owner A edits a column value (col:budget) → CR to C
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **A**.
- **Given** A owns structure globally but **no columns**; `{C}` owns col:budget.
- **When** A calls `updateCell(sheet=S, node=X, column=col:budget, value=7)`.
- **Then** unauthorized on Axis 2; CR `resolved_approver=C`; outcome `suggested`. Proves structural authority ≠ column authority.
- **Covers:** `updateCell` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-023
- **Title:** Delegated owner D edits a column value in own branch (col:status on Y) → CR to C
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **D**.
- **Given** D owns structure of P2 subtree but Axis 2 for col:status = {C, B}; D ∉.
- **When** D calls `updateCell(sheet=S, node=Y, column=col:status, value="x")`.
- **Then** unauthorized; CR `resolved_approver=C` (column_owner); outcome `suggested`. D's structural delegation gives **no** value-edit authority.
- **Covers:** `updateCell` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-024
- **Title:** Suggest-only E edits col:status → CR routed to column owner C
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **E**.
- **Given** approvers {C, B}; E ∉.
- **When** E calls `updateCell(sheet=S, node=X, column=col:status, value="z")`.
- **Then** unauthorized; CR `resolved_approver=C`; outcome `suggested`.
- **Covers:** `updateCell` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-025
- **Title:** Editing the node label is Axis 2 (col:name owner B), not Axis 1
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **D** (structural owner of P2 containing Y).
- **Given** `col:name` is the `is_label` column owned by B; editing Y's label = `updateCell(Y, col:name)`.
- **When** D (who structurally owns Y) calls `updateCell(sheet=S, node=Y, column=col:name, value="renamed")`.
- **Then** Axis 2 resolves {B}; D ∉ → CR to **B**; outcome `suggested`. Confirms label edits route to the label column owner, not the branch owner.
- **Covers:** `updateCell` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-026
- **Title:** `resolve_column_approvers` set composition (owner ∪ editors)
- **Level:** unit
- **Preconditions / fixtures:** Shared `S` columns.
- **Given** col:status owner C, editors [B].
- **When** `resolve_column_approvers(col:status)`.
- **Then** returns exactly `{C, B}` (set; no duplicates if owner also appears as editor).
- **Covers:** `updateCell` (resolver).

---

## Section 4 — Meta / schema ops (addColumn, updateColumn, deleteColumn)

### PERMISSIONS_AND_DELEGATION-027
- **Title:** Sheet owner A adds a column → executes (addColumn → sheet structural_owner)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **A**.
- **Given** `addColumn` ACL = sheet `structural_owner`.
- **When** A calls `addColumn(sheet=S, field="risk", label="Risk", type="text", column_owner=C)`.
- **Then** A == structural_owner → authorized; new Tree Column row created; outcome `executed`.
- **Covers:** `addColumn` · `COLUMN_CONFIG_UPDATED`.

### PERMISSIONS_AND_DELEGATION-028
- **Title:** Non-sheet-owner C attempts addColumn → CR to sheet owner A
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **C**.
- **Given** addColumn ACL = sheet owner A; C ≠ A.
- **When** C calls `addColumn(sheet=S, field="x", label="X", type="number")`.
- **Then** unauthorized; CR `target_kind=column-schema`, `operation=add`, `resolved_approver=A`; outcome `suggested`.
- **Covers:** `addColumn` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-029
- **Title:** Column owner C updates own column schema (col:budget width) → executes
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **C**.
- **Given** `updateColumn` ACL = `resolve_column_approvers(col:budget) = {C}`.
- **When** C calls `updateColumn(sheet=S, column=col:budget, patch={"width": 200})`.
- **Then** authorized; column row patched; outcome `executed`.
- **Covers:** `updateColumn` · `COLUMN_CONFIG_UPDATED`.

### PERMISSIONS_AND_DELEGATION-030
- **Title:** Column owner C deletes own column (col:budget) → executes
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **C**.
- **Given** `deleteColumn` ACL = column approvers {C}.
- **When** C calls `deleteColumn(sheet=S, column=col:budget)`.
- **Then** authorized; column removed; outcome `executed`.
- **Covers:** `deleteColumn` · `COLUMN_CONFIG_UPDATED`.

### PERMISSIONS_AND_DELEGATION-031
- **Title:** Editor B updates col:status schema → executes (editor is an approver for meta too)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **B** (editor on col:status).
- **Given** `updateColumn` ACL = column approvers {C, B}.
- **When** B calls `updateColumn(sheet=S, column=col:status, patch={"label":"State"})`.
- **Then** B ∈ approvers → authorized; outcome `executed`.
- **Covers:** `updateColumn` · `COLUMN_CONFIG_UPDATED`.

### PERMISSIONS_AND_DELEGATION-032
- **Title:** Non-approver A deletes a column they don't own (col:status) → CR to owner C
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **A** (sheet owner, but not col:status approver).
- **Given** `deleteColumn` ACL = {C, B}; A ∉.
- **When** A calls `deleteColumn(sheet=S, column=col:status)`.
- **Then** unauthorized; CR `target_kind=column-schema`, `operation=delete`, `resolved_approver=C` (column_owner); outcome `suggested`. (Sheet ownership does not grant column-schema authority for update/delete.)
- **Covers:** `deleteColumn` · `CHANGE_PROPOSED`.

---

## Section 5 — Delegation lifecycle (delegateBranch, revokeDelegation) & effect on routing

### PERMISSIONS_AND_DELEGATION-033
- **Title:** A delegates P2 to D → DELEGATION_CHANGED, subsequent P2 structural authority moves to D
- **Level:** integration
- **Preconditions / fixtures:** Shared `S` **without** `BG_P2` initially (pre-delegation variant), persona **A**.
- **Given** `delegateBranch` ACL = `resolve_structural_approver(branch_root=P2)` = A (no grant yet).
- **When** A calls `delegateBranch(sheet=S, branch_root=P2, grantee=D)`.
- **Then** authorized; an active Branch Grant row is created (`grantee=D`, `granted_by=A`, `scope=structure`); outcome `executed`. A follow-up `resolve_structural_approver(S, Y)` now returns **D**.
- **Covers:** `delegateBranch` · `DELEGATION_CHANGED`.

### PERMISSIONS_AND_DELEGATION-034
- **Title:** Non-owner E attempts to delegate P2 → CR to current approver
- **Level:** integration
- **Preconditions / fixtures:** Shared `S` (BG_P2 active, approver D), persona **E**.
- **Given** `delegateBranch(branch_root=P2)` ACL = `resolve_structural_approver(P2)` = D; E ≠ D.
- **When** E calls `delegateBranch(sheet=S, branch_root=P2, grantee=F)`.
- **Then** unauthorized; CR `resolved_approver=D`; outcome `suggested`. No grant created.
- **Covers:** `delegateBranch` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-035
- **Title:** Revoke BG_P2 by granted_by A → DELEGATION_CHANGED; P2 authority falls back to A
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **A**, grant `BG_P2`.
- **Given** `revokeDelegation` ACL = `granted_by` (A) or ancestor structural owner.
- **When** A calls `revokeDelegation(branch_grant=BG_P2)`.
- **Then** authorized; grant set inactive; outcome `executed`. Follow-up `resolve_structural_approver(S, Y)` now returns **A** (fallback). Pending CRs against D get re-routed at decision time (see -054).
- **Covers:** `revokeDelegation` · `DELEGATION_CHANGED`.

### PERMISSIONS_AND_DELEGATION-036
- **Title:** Revoke by ancestor structural owner (not granted_by) → allowed
- **Level:** integration
- **Preconditions / fixtures:** Shared `S` + nested grant `BG_Z` (granted_by=D, grantee=D2). Persona **A** (ancestor structural owner of Z's chain root).
- **Given** `revokeDelegation` ACL = `granted_by` **or** ancestor structural owner. A is an ancestor structural owner (root) even though A did not grant BG_Z.
- **When** A calls `revokeDelegation(branch_grant=BG_Z)`.
- **Then** authorized; BG_Z inactive; outcome `executed`.
- **Covers:** `revokeDelegation` · `DELEGATION_CHANGED`.

### PERMISSIONS_AND_DELEGATION-037
- **Title:** Revoke by unrelated user E → CR (not authorized)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **E**, grant `BG_P2`.
- **Given** E is neither `granted_by` (A) nor an ancestor structural owner.
- **When** E calls `revokeDelegation(branch_grant=BG_P2)`.
- **Then** unauthorized; CR routed to the resolving structural approver (the grant's ancestor owner / granted_by A); outcome `suggested`. Grant stays active.
- **Covers:** `revokeDelegation` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-038
- **Title:** Delegated owner D sub-delegates within own branch (delegateBranch Z → D2) → executes
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **D**, grant `BG_P2`.
- **Given** `delegateBranch(branch_root=Z)` ACL = `resolve_structural_approver(Z)`; walk Z→P2(BG_P2:D) → **D**.
- **When** D calls `delegateBranch(sheet=S, branch_root=Z, grantee=D2)`.
- **Then** D == approver → authorized; new active grant `BG_Z` (granted_by=D); outcome `executed`. Sub-delegation allowed within own subtree.
- **Covers:** `delegateBranch` · `DELEGATION_CHANGED`.

### PERMISSIONS_AND_DELEGATION-039
- **Title:** D cannot delegate a branch OUTSIDE its subtree (delegateBranch P1) → CR to A
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **D**.
- **Given** `delegateBranch(branch_root=P1)` ACL = `resolve_structural_approver(P1)` = A; D ≠ A.
- **When** D calls `delegateBranch(sheet=S, branch_root=P1, grantee=F)`.
- **Then** unauthorized; CR `resolved_approver=A`; outcome `suggested`.
- **Covers:** `delegateBranch` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-040
- **Title:** Nested-grant routing after sub-delegation — child of Z routes to D2, sibling of Z under P2 routes to D
- **Level:** integration
- **Preconditions / fixtures:** Shared `S` + active `BG_Z` (grantee D2) from -038.
- **Given** node `Z1` (child of Z) and node `Y` (child of P2, outside Z).
- **When** F calls `addNode(parent=Z1)` and separately `addNode(parent=Y)`.
- **Then** `addNode(parent=Z1)` → nearest grant BG_Z → CR `resolved_approver=D2`; `addNode(parent=Y)` → nearest grant BG_P2 → CR `resolved_approver=D`. Confirms nearest-grant boundary precision.
- **Covers:** `addNode` · `CHANGE_PROPOSED` (×2).

### PERMISSIONS_AND_DELEGATION-041
- **Title:** Revoking inner grant BG_Z re-collapses Z's subtree to outer owner D
- **Level:** integration
- **Preconditions / fixtures:** Shared `S` + `BG_Z` active, persona **D** (granted_by of BG_Z).
- **Given** BG_Z grantee D2 active.
- **When** D calls `revokeDelegation(branch_grant=BG_Z)`, then a structural change on Z1 is resolved.
- **Then** revoke executes (`DELEGATION_CHANGED`); subsequent `resolve_structural_approver(S, Z1)` walks Z1→Z(BG_Z inactive)→P2(BG_P2:D) → **D**. Authority collapses outward, not to A.
- **Covers:** `revokeDelegation` · `DELEGATION_CHANGED`; `addNode` (resolver).

### PERMISSIONS_AND_DELEGATION-042
- **Title:** Delegation only affects Axis 1 — column authority unchanged after delegateBranch
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, after A delegates P2 to D.
- **Given** delegation changed structural routing for P2 subtree.
- **When** D calls `updateCell(sheet=S, node=Y, column=col:budget, value=9)`.
- **Then** Axis 2 unaffected by Axis 1 delegation; approvers {C}; D ∉ → CR to **C**; outcome `suggested`.
- **Covers:** `updateCell` · `CHANGE_PROPOSED`.

---

## Section 6 — Column ownership admin (grantColumn)

### PERMISSIONS_AND_DELEGATION-043
- **Title:** Column owner C reassigns owner / adds editor via grantColumn → executes
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **C** (owner of col:budget).
- **Given** `grantColumn` ACL = current `column_owner` or sheet `structural_owner`.
- **When** C calls `grantColumn(sheet=S, column=col:budget, column_owner=C, editors=[E])`.
- **Then** authorized; col:budget editors now include E; outcome `executed`. Follow-up `resolve_column_approvers(col:budget) = {C, E}`.
- **Covers:** `grantColumn` · `COLUMN_CONFIG_UPDATED`.

### PERMISSIONS_AND_DELEGATION-044
- **Title:** Sheet owner A reassigns a column owner via grantColumn → executes (admin path)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **A**.
- **Given** A is sheet `structural_owner`, an allowed grantColumn actor.
- **When** A calls `grantColumn(sheet=S, column=col:notes, column_owner=E)`.
- **Then** authorized; col:notes owner becomes E; outcome `executed`.
- **Covers:** `grantColumn` · `COLUMN_CONFIG_UPDATED`.

### PERMISSIONS_AND_DELEGATION-045
- **Title:** Non-owner non-sheet-owner F attempts grantColumn → CR to column owner
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **F**.
- **Given** F is neither col:budget owner (C) nor sheet owner (A).
- **When** F calls `grantColumn(sheet=S, column=col:budget, editors=[F])`.
- **Then** unauthorized; CR `resolved_approver` = column_owner C; outcome `suggested`. (No privilege self-grant.)
- **Covers:** `grantColumn` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-046
- **Title:** grantColumn effect is immediate for next updateCell authority check
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`; sequence after -043 added E as editor of col:budget.
- **Given** E is now an editor of col:budget.
- **When** E calls `updateCell(sheet=S, node=X, column=col:budget, value=3)`.
- **Then** `resolve_column_approvers(col:budget) = {C, E}`; E ∈ → authorized; outcome `executed`. Confirms ownership change feeds the resolver with no caching staleness.
- **Covers:** `grantColumn` + `updateCell` · `NODE_VALUE_UPDATED`.

---

## Section 7 — Change Request lifecycle & suggest-routing

### PERMISSIONS_AND_DELEGATION-047
- **Title:** Explicit suggestChange by E always creates a CR regardless of authority
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **E**.
- **Given** `suggestChange` ACL = always allowed.
- **When** E calls `suggestChange(sheet=S, target_kind=cell-value, operation=update, payload={node:X, column:col:status, value:"q"})`.
- **Then** a CR is created directly; `resolved_approver` resolved from payload (Axis 2 → C); outcome `suggested`.
- **Covers:** `suggestChange` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-048
- **Title:** Approve CR replays handler AS resolved_approver → real mutation event + CHANGE_APPROVED
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`; a PROPOSED CR from -021 (`resolved_approver=C`, update col:budget on X).
- **Given** CR in `proposed`, `resolved_approver=C`.
- **When** C calls `approveChange(change_request=CR)`.
- **Then** handler re-runs as **C** → cell `(X, col:budget)` updated, emits `NODE_VALUE_UPDATED`; `CR.resulting_event` linked; then `CHANGE_APPROVED` emitted; CR state → `approved` (terminal). Two events here are the *real mutation event* + the *lifecycle event* (the original `CHANGE_PROPOSED` was the single event at proposal time).
- **Covers:** `approveChange` · `NODE_VALUE_UPDATED`, `CHANGE_APPROVED`.

### PERMISSIONS_AND_DELEGATION-049
- **Title:** Reject CR → CHANGE_REJECTED, no data mutation
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`; PROPOSED CR (`resolved_approver=A`, addNode under P1 from -006).
- **Given** CR `proposed`, approver A.
- **When** A calls `rejectChange(change_request=CR)`.
- **Then** CR → `rejected` (terminal); `CHANGE_REJECTED` emitted; **no** Tree Node created; `resulting_event` empty.
- **Covers:** `rejectChange` · `CHANGE_REJECTED`.

### PERMISSIONS_AND_DELEGATION-050
- **Title:** Only resolved_approver may approve — wrong approver denied
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`; PROPOSED CR with `resolved_approver=C` (update col:budget).
- **Given** `approveChange` ACL = actor == `resolved_approver` (or column editor).
- **When** E calls `approveChange(change_request=CR)`.
- **Then** ACL-denied; CR stays `proposed`; no mutation, no `CHANGE_APPROVED`. (Denial does not itself create a nested CR — approve/reject are governance ops, not mutations.)
- **Covers:** `approveChange` (deny).

### PERMISSIONS_AND_DELEGATION-051
- **Title:** Column editor B may approve a CR on col:status (editor counts as approver)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`; PROPOSED CR from E updating col:status (`resolved_approver=C`, column has editor B).
- **Given** approve ACL = `resolved_approver` **or** a column editor; B is editor of col:status.
- **When** B calls `approveChange(change_request=CR)`.
- **Then** authorized; handler replays as **B**; `NODE_VALUE_UPDATED` then `CHANGE_APPROVED`; CR `approved`.
- **Covers:** `approveChange` · `NODE_VALUE_UPDATED`, `CHANGE_APPROVED`.

### PERMISSIONS_AND_DELEGATION-052
- **Title:** Requester may withdraw own CR; non-requester may not
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`; PROPOSED CR with `requester=E`.
- **Given** `withdrawChange` ACL = actor == CR `requester`.
- **When** (a) E calls `withdrawChange(CR)`; (b) F calls `withdrawChange(CR)` on a fresh equivalent CR.
- **Then** (a) CR → `withdrawn` (terminal), emits `CHANGE_REJECTED` with status=withdrawn; (b) F denied, CR unchanged.
- **Covers:** `withdrawChange` · `CHANGE_REJECTED` (status=withdrawn); deny path.

### PERMISSIONS_AND_DELEGATION-053
- **Title:** Approve/reject/withdraw on a terminal CR is rejected (idempotency)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`; an already-`approved` CR.
- **Given** terminal states `approved|rejected|withdrawn` accept no transitions.
- **When** the approver calls `approveChange(CR)` again (and `rejectChange`, `withdrawChange`).
- **Then** each is rejected; no second mutation; no duplicate `NODE_VALUE_UPDATED`/`CHANGE_APPROVED`; original `resulting_event` unchanged. Guarantees exactly-once replay.
- **Covers:** `approveChange`/`rejectChange`/`withdrawChange` (idempotency).

### PERMISSIONS_AND_DELEGATION-054
- **Title:** Stale approver re-resolution at decision time — delegation revoked between propose and approve
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`; PROPOSED structural CR (addNode under Y, `resolved_approver=D` at proposal). Then `BG_P2` revoked (see -035).
- **Given** at decision time the resolver re-computes the structural approver because grants changed; Y now resolves to **A**.
- **When** the system processes `approveChange` / routing.
- **Then** CR is re-routed: `resolved_approver` recomputed to **A**; D (stale) attempting to approve is denied; A may approve. Asserts decision-time re-resolution (PERMISSIONS §1 / ARCHITECTURE §5).
- **Covers:** `approveChange` (re-resolution) · `CHANGE_APPROVED` on A's approval.

### PERMISSIONS_AND_DELEGATION-055
- **Title:** moveNode CR carries co_approvers and routes to dest approver
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`; CR from -013 (A moves X into P2).
- **Given** CR `resolved_approver=D`, `payload.co_approvers=[A]`.
- **When** D approves.
- **Then** handler replays move as **D**; `NODE_MOVED` emitted; `CHANGE_APPROVED`. (Co-approver semantics: dest approver is the routing target; src co-approver recorded for accountability.)
- **Covers:** `approveChange` · `NODE_MOVED`, `CHANGE_APPROVED`.

---

## Section 8 — Owner-self policy (owners_must_use_change_requests)

### PERMISSIONS_AND_DELEGATION-056
- **Title:** With owners_must_use_change_requests=true, authorized owner action still yields a CR (self-approver)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S` with `settings.owners_must_use_change_requests=true`, persona **C** (owns col:budget).
- **Given** C is authorized on Axis 2 for col:budget.
- **When** C calls `updateCell(sheet=S, node=X, column=col:budget, value=4)`.
- **Then** instead of direct mutation, a CR is created with `resolved_approver=C` (self); emits `CHANGE_PROPOSED`; outcome `suggested`. Cell not yet changed until C approves.
- **Covers:** `updateCell` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-057
- **Title:** Self-CR can be approved by the same owner → real mutation
- **Level:** integration
- **Preconditions / fixtures:** Shared `S` with flag true; the self-CR from -056.
- **When** C calls `approveChange(CR)`.
- **Then** handler replays as C; `NODE_VALUE_UPDATED`; `CHANGE_APPROVED`; CR `approved`. Forced audit trail satisfied.
- **Covers:** `approveChange` · `NODE_VALUE_UPDATED`, `CHANGE_APPROVED`.

### PERMISSIONS_AND_DELEGATION-058
- **Title:** Owner-self policy applies to structural owner too (A addNode → self-CR)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S` with flag true, persona **A**.
- **When** A calls `addNode(sheet=S, parent=P1)`.
- **Then** authorized but policy forces CR with `resolved_approver=A`; `CHANGE_PROPOSED`; node created only on A's later approve.
- **Covers:** `addNode` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-059
- **Title:** With flag false (default), the same owner action mutates directly
- **Level:** integration
- **Preconditions / fixtures:** Shared `S` default settings, persona **C**.
- **When** C calls `updateCell(sheet=S, node=X, column=col:budget, value=4)`.
- **Then** direct mutation; `NODE_VALUE_UPDATED`; outcome `executed`; no CR. Contrast control for -056.
- **Covers:** `updateCell` · `NODE_VALUE_UPDATED`.

---

## Section 9 — Agent-as-User under the same ACL

### PERMISSIONS_AND_DELEGATION-060
- **Title:** Agent (own User, no authority) editing a column → Change Request, not bypass
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`; agent runs as its own Frappe User `AGENT` with no column ownership.
- **Given** agent calls capability via `getLLMTools` → identical `execute_action` path.
- **When** AGENT calls `updateCell(sheet=S, node=X, column=col:budget, value=5)`.
- **Then** Axis 2 approvers {C}; AGENT ∉ → CR `resolved_approver=C`, `actor=AGENT`, `actor_type=agent`; outcome `suggested`. Agent cannot escalate by being an agent.
- **Covers:** `updateCell` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-061
- **Title:** Agent that IS granted a column edits directly (authority is by identity, not surface)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S` where AGENT has been added as editor of col:notes via grantColumn.
- **When** AGENT calls `updateCell(sheet=S, node=X, column=col:notes, value="auto")`.
- **Then** AGENT ∈ approvers → authorized; `NODE_VALUE_UPDATED`; outcome `executed`. Confirms ACL keys on the actor identity uniformly.
- **Covers:** `updateCell` · `NODE_VALUE_UPDATED`.

### PERMISSIONS_AND_DELEGATION-062
- **Title:** Agent structural change outside any grant → CR to A (same as human non-owner)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, agent `AGENT`.
- **When** AGENT calls `deleteNode(sheet=S, node=X)`.
- **Then** approver A; AGENT ≠ A → CR `resolved_approver=A`; outcome `suggested`; identical decision to persona E (-008).
- **Covers:** `deleteNode` · `CHANGE_PROPOSED`.

---

## Section 10 — Surface parity (web ≡ REST ≡ agent)

### PERMISSIONS_AND_DELEGATION-063
- **Title:** Identical authority decision + event for the same capability across all three surfaces
- **Level:** e2e
- **Preconditions / fixtures:** Shared `S`; persona **B**; the same `updateCell(Z, col:name, "v")` invoked three ways.
- **Given** ARCHITECTURE §11 parity invariant.
- **When** the call is made via (1) web `executeAction`, (2) `POST /api/method/arbor.update_cell` as B, (3) agent tool as B-identity.
- **Then** all three resolve approvers {B}, authorize, run the same handler, and emit one `NODE_VALUE_UPDATED` each with equivalent payload `{node:Z, column:col:name, old, new}`; differences only in `actor_type`.
- **Covers:** `updateCell` · `NODE_VALUE_UPDATED` (×3, parity).

### PERMISSIONS_AND_DELEGATION-064
- **Title:** Unauthorized parity — same capability yields identical CR across surfaces
- **Level:** e2e
- **Preconditions / fixtures:** Shared `S`; persona **E**; `updateCell(X, col:budget, 1)` via web, REST, agent.
- **When** invoked on all three surfaces.
- **Then** each produces a CR with identical `target_kind=cell-value`, `operation=update`, `resolved_approver=C`, `payload`; each emits one `CHANGE_PROPOSED`. No surface has a privileged path.
- **Covers:** `updateCell` · `CHANGE_PROPOSED` (×3, parity).

### PERMISSIONS_AND_DELEGATION-065
- **Title:** EXT external system API write is bound by the two-axis ACL
- **Level:** e2e
- **Preconditions / fixtures:** Shared `S`; persona **EXT** (API key User), not a column owner.
- **When** EXT `POST /api/method/arbor.update_cell {node:X, column:col:status, value:"a"}`.
- **Then** Axis 2 {C, B}; EXT ∉ → CR `resolved_approver=C`; outcome `suggested`. EXT has no special privilege as an external system.
- **Covers:** `updateCell` · `CHANGE_PROPOSED`.

---

## Section 11 — Boundary, conflict & negative conditions

### PERMISSIONS_AND_DELEGATION-066
- **Title:** Two active grants on the SAME branch_root — resolver picks deterministically (most-recent active)
- **Level:** unit
- **Preconditions / fixtures:** Shared `S` with two active `scope=structure` grants on P2 (data anomaly): grantee D and grantee D3.
- **Given** `find_active_branch_grant` must be deterministic.
- **When** `resolve_structural_approver(S, Y)`.
- **Then** returns a single deterministic grantee (the most recently granted active row); never both, never nondeterministic. Documents the tie-break contract.
- **Covers:** `addNode` (resolver) — conflict handling.

### PERMISSIONS_AND_DELEGATION-067
- **Title:** moveNode where src parent == dest parent (no-op reparent) still requires that single approver
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **D**.
- **Given** `moveNode(node=Z, new_parent=P2)` and Z already under P2 (reorder only).
- **When** D calls it.
- **Then** src approver == dest approver == D → authorized; `NODE_MOVED` emitted (ordering change). Boundary: identical ends collapse to one authority check.
- **Covers:** `moveNode` · `NODE_MOVED`.

### PERMISSIONS_AND_DELEGATION-068
- **Title:** moveNode that would create a cycle (move node into its own descendant) is rejected pre-ACL
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **A**.
- **Given** `moveNode(node=P1, new_parent=X)` where X is a descendant of P1.
- **When** A (authorized on both ends) calls it.
- **Then** the handler rejects the structural invariant violation (NestedSet cycle); no `NODE_MOVED` emitted; no CR (this is a validation error, not an authority outcome). Asserts validity checks are independent of ACL.
- **Covers:** `moveNode` (validation guard).

### PERMISSIONS_AND_DELEGATION-069
- **Title:** updateCell on a non-existent (node, column) pair fails validation, not ACL
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **C** (owns col:budget).
- **Given** node id does not exist in `S`.
- **When** C calls `updateCell(sheet=S, node="bogus", column=col:budget, value=1)`.
- **Then** schema/existence validation fails before authority resolution; no event; no CR. (Existence of the cell's node is an Axis-1-governed fact but a missing node is a validation error.)
- **Covers:** `updateCell` (validation).

### PERMISSIONS_AND_DELEGATION-070
- **Title:** Concurrent updateCell on same cell — version increments serialize (optimistic concurrency)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **C** (owns col:budget); two concurrent authorized writes to `(X, col:budget)`.
- **Given** `Tree Node Value.version` counter.
- **When** two `updateCell` calls race.
- **Then** both authorized; writes serialize; final `version` reflects two increments; two `NODE_VALUE_UPDATED` events with correct `old→new` chaining; no lost-update without a version bump.
- **Covers:** `updateCell` · `NODE_VALUE_UPDATED` (concurrency).

### PERMISSIONS_AND_DELEGATION-071
- **Title:** deleteColumn does not strand structural authority — Axis 1 unaffected
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **C** deletes col:budget.
- **Given** Axis 1 and Axis 2 independent.
- **When** C deletes col:budget, then D calls `addNode(parent=Y)`.
- **Then** column deletion emits `COLUMN_CONFIG_UPDATED`; D's structural authority over P2 is unchanged → `addNode` executes. Confirms cross-axis non-interference.
- **Covers:** `deleteColumn`, `addNode` · `COLUMN_CONFIG_UPDATED`, `NODE_CREATED`.

### PERMISSIONS_AND_DELEGATION-072
- **Title:** Deleting a branch root cascades structural authority away with the subtree
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **A**; delete P2 (the delegated branch root) with cascade.
- **Given** `deleteNode(P2, cascade=true)`; approver of P2 walks P2→R → no grant on R, but BG_P2 is ON P2 itself → nearest grant = D.
- **When** A calls `deleteNode(sheet=S, node=P2)`.
- **Then** approver of P2 = **D** (grant is on P2, included in self-walk); A ≠ D → CR `resolved_approver=D`; outcome `suggested`. Asserts the grant on the node itself participates in its own delete authority (ancestors_self includes self).
- **Covers:** `deleteNode` · `CHANGE_PROPOSED`.

### PERMISSIONS_AND_DELEGATION-073
- **Title:** ancestors_self includes the node itself — addNode under a node bearing a grant uses that grant
- **Level:** unit
- **Preconditions / fixtures:** Shared `S`; grant `BG_P2` on P2.
- **Given** `resolve_structural_approver(S, P2)` (the grant node itself).
- **When** resolver walks P2→R, first element is P2 which has BG_P2.
- **Then** returns **D** (self counts). Distinguishes from a hypothetical "strict ancestors" bug that would skip self and return A.
- **Covers:** `addNode`/`deleteNode` (resolver — self inclusion).

### PERMISSIONS_AND_DELEGATION-074
- **Title:** suggestChange with malformed payload (missing required keys) fails validation
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **E**.
- **Given** `suggestChange` params_schema requires `sheet, target_kind, operation, payload`.
- **When** E calls `suggestChange(sheet=S, target_kind="cell-value")` (missing operation, payload).
- **Then** schema validation fails before CR creation; no CR; no `CHANGE_PROPOSED`.
- **Covers:** `suggestChange` (validation).

### PERMISSIONS_AND_DELEGATION-075
- **Title:** grantColumn cannot be used to grant a column on a different sheet's column (sheet/column mismatch)
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`, persona **A**; a column id belonging to another sheet `S2`.
- **When** A calls `grantColumn(sheet=S, column=<S2-column>, column_owner=E)`.
- **Then** validation rejects the cross-sheet reference; no mutation; no event. Boundary guard on referential integrity prior to ACL.
- **Covers:** `grantColumn` (validation).

### PERMISSIONS_AND_DELEGATION-076
- **Title:** Empty-set column (owner removed, no editors) — only sheet owner may re-grant
- **Level:** integration
- **Preconditions / fixtures:** Shared `S`; a column whose `column_owner` was cleared and `editors=[]` (edge data state).
- **Given** `resolve_column_approvers(col) = {}` (empty); no one can directly edit.
- **When** (a) E calls `updateCell` on it; (b) A (sheet owner) calls `grantColumn` to set an owner.
- **Then** (a) E ∉ {} → CR, but `resolved_approver` falls back to sheet `structural_owner` A (no column_owner to route to); (b) A authorized via sheet-owner branch of grantColumn → sets owner; `COLUMN_CONFIG_UPDATED`. Documents the empty-approver fallback routing.
- **Covers:** `updateCell` · `CHANGE_PROPOSED`; `grantColumn` · `COLUMN_CONFIG_UPDATED`.

---

## Coverage summary

- **Capabilities exercised:** `addNode`, `updateCell`, `moveNode`, `deleteNode`,
  `addColumn`, `updateColumn`, `deleteColumn`, `suggestChange`, `approveChange`,
  `rejectChange`, `withdrawChange`, `delegateBranch`, `revokeDelegation`, `grantColumn`.
- **Tree Events asserted:** `NODE_CREATED`, `NODE_VALUE_UPDATED`, `NODE_MOVED`,
  `NODE_DELETED`, `COLUMN_CONFIG_UPDATED`, `CHANGE_PROPOSED`, `CHANGE_APPROVED`,
  `CHANGE_REJECTED`, `DELEGATION_CHANGED`.
- **Persona matrix:** A (root), B/C (column owners, B also editor), D (delegated) + D2/D3
  (nested/conflict), E/F (suggest-only), AGENT (agent-as-user), EXT (external/API).
- **Invariants:** axis independence, nearest-grant-wins, root fallback, move-both-ends +
  co-approver, agent=human-under-ACL, surface parity, approver-only decisions,
  owner-self policy, ancestors_self self-inclusion, decision-time re-resolution,
  idempotent CR replay.
