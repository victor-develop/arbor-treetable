# Arbor — Test-Case Catalog: Web UI Surface

> **Status:** Test-first. Authored against the canonical specs
> ([`ARCHITECTURE.md`](../docs/ARCHITECTURE.md), [`PERMISSIONS.md`](../docs/PERMISSIONS.md),
> [`CAPABILITIES.md`](../docs/CAPABILITIES.md), [`DATA-MODEL.md`](../docs/DATA-MODEL.md))
> **before** implementation. IDs are stable; do not renumber.
>
> **Surface under test:** the standalone React frontend (the *thin shell* of
> ARCHITECTURE §4.1(a)). The web UI never mutates directly — it calls one
> `executeAction(actionId, params)` endpoint and renders from `getSheetSnapshot`.
> These tests assert **component rendering**, **interaction → executeAction wiring**, and
> the UI's reaction to the two `Outcome` kinds (`executed` vs `suggested`). Server-side
> ACL resolution, event emission, and dispatcher fan-out are covered by the backend
> catalog; here we assert the UI sends the **right capability call** and **renders the
> right result**, mocking `executeAction` / `getSheetSnapshot` / `agent.chat` at the
> client API boundary unless a row is explicitly marked `e2e`.

---

## Shared fixtures (canonical — referenced, never redefined)

All cases below reference these. Do **not** invent per-test worlds.

- **Personas** (per PERMISSIONS §2): **A** root structural owner; **B** owner of
  `col:name` (is_label) + `col:notes`, editor on `col:status`; **C** owner of `col:status`
  + `col:budget`; **D** delegated owner of sub-branch **P2** (active Branch Grant,
  `scope=structure`); **E**, **F** suggest-only (no grants, no columns); **G** sensitive
  subscriber (`requires_ack`, no edit authority); **EXT** external system.
- **Sample sheet `S`** (PERMISSIONS §2) — tree:
  ```
  R (root)            struct: A
  ├── P1              struct: A
  │   └── X           struct: A
  └── P2  ←Grant→D    struct: D
      ├── Y           struct: D (inherited)
      └── Z           struct: D (inherited)
  ```
  Columns: `col:name` (is_label, type `text`, owner **B**), `col:status`
  (`single-select-split`, owner **C**, editors `[B]`), `col:budget` (`number`, owner
  **C**), `col:notes` (`multiline-text`, owner **B**). A `col:tags`
  (`multi-select-split`, owner **C**) is added where split-multi behavior is exercised.
- **Snapshot shape:** `getSheetSnapshot(sheet)` returns `{ sheet, columns[], nodes[]
  (with lft/rgt + parent), values{(node,column)→{value,version}}, viewer: { user,
  per_column_can_edit{}, per_node_can_structure{} } }`. The UI derives owned-vs-suggest
  affordances from `viewer.*` flags supplied by the server (the UI does **not** re-run
  ACL). Tests assert the UI honors these flags.
- **API boundary doubles:** `executeAction` returns `Outcome` =
  `{kind:"executed", event, result}` or `{kind:"suggested", change_request, event}`.
  `agent.chat` streams `{type: thought|action|observation|final, ...}` frames.
- **Session helper:** `loginAs(persona)` sets `viewer` in the mocked snapshot.

**Outcome-rendering contract** (asserted repeatedly, defined once here):
- `executed` → optimistic cell/tree state commits; a transient "Saved" affordance shows;
  no CR banner.
- `suggested` → state does **not** commit (reverts to snapshot value); a "Suggestion sent
  to <approver>" toast/banner shows referencing `change_request`; the affordance shows a
  pending-suggestion marker.

---

## Index

- Tree render & expand/collapse — WEB_UI-001..010
- Inline cell edit (owned) vs suggest-mode (non-owned) — WEB_UI-011..025
- Split-column types (single/multi-select-split) — WEB_UI-026..035
- Drag-and-drop reorder/move (before/after/inside) — WEB_UI-036..050
- Schema editor (add/remove/configure columns) — WEB_UI-051..062
- AI agent sidebar (thin shell → agent.chat) — WEB_UI-063..073
- Import / export snapshot — WEB_UI-074..082
- Cross-cutting: surface-parity, conflict/idempotency, errors — WEB_UI-083..090

---

## 1. Tree render & expand/collapse

### WEB_UI-001
- **Title:** Initial render builds tree rows from snapshot in NestedSet order
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; `loginAs(A)`; `getSheetSnapshot` mocked to canonical.
- **Given** the snapshot with nodes R,P1,X,P2,Y,Z and their `parent`/`lft`/`rgt`.
- **When** the TreeTable mounts.
- **Then** rows render in depth-first order R → P1 → X → P2 → Y → Z; each row's indentation depth equals its ancestor count; X is nested under P1 and Y,Z under P2; the label cell shows the `col:name` value (label comes from the `is_label` column, not a node field).
- **Covers:** `getSheetSnapshot`; events: none (read).

### WEB_UI-002
- **Title:** Label column resolves from the is_label column value
- **Level:** unit
- **Preconditions / fixtures:** snapshot where `values[(X, col:name)] = "Task X"`.
- **Given** a row component for node X.
- **When** rendered.
- **Then** the displayed node label is "Task X"; if `values[(X, col:name)]` is absent the label falls back to a placeholder (e.g. node id), never to a hardcoded `name` field.
- **Covers:** `getSheetSnapshot`; events: none.

### WEB_UI-003
- **Title:** Group nodes show an expand/collapse affordance; leaves do not
- **Level:** unit
- **Preconditions / fixtures:** snapshot; P1,P2,R are `is_group`; X,Y,Z are leaves.
- **Given** rendered rows.
- **Then** R, P1, P2 render a chevron toggle; X, Y, Z render none (or a disabled spacer preserving alignment).
- **Covers:** `getSheetSnapshot`; events: none.

### WEB_UI-004
- **Title:** Collapsing a node hides its entire subtree
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; all expanded.
- **When** the user collapses P2.
- **Then** Y and Z are removed from the visible row set; P2 remains with a collapsed chevron; R, P1, X unaffected. Collapsing P1 hides X.
- **Covers:** none (pure view state); events: none.

### WEB_UI-005
- **Title:** Expanding restores children and is idempotent
- **Level:** integration
- **Preconditions / fixtures:** P2 collapsed (WEB_UI-004).
- **When** the user expands P2, then clicks expand again.
- **Then** Y and Z reappear once (no duplicate rows); the second click is a no-op.
- **Covers:** none; events: none.

### WEB_UI-006
- **Title:** Expand/collapse state is local view state, never an executeAction
- **Level:** integration
- **Preconditions / fixtures:** spy on `executeAction`.
- **When** the user toggles any chevron.
- **Then** `executeAction` is **not** called (expansion is not a capability); no Tree Event is implied.
- **Covers:** none — negative assertion guarding the registry boundary.

### WEB_UI-007
- **Title:** Deeply nested / large subtree renders without losing ordering
- **Level:** unit
- **Preconditions / fixtures:** snapshot extended with a chain under X (X→X1→X2→X3) plus 50 siblings under P1 with `idx` 0..49.
- **Then** siblings render in ascending `idx`; the X-chain nests 3 levels deeper; row count equals node count when fully expanded.
- **Covers:** `getSheetSnapshot`; events: none. (Boundary: depth + sibling breadth.)

### WEB_UI-008
- **Title:** Empty sheet renders an empty-state with an enabled "add root node" affordance for the structural owner
- **Level:** integration
- **Preconditions / fixtures:** sheet `S'` with zero nodes; `loginAs(A)` (root owner).
- **Then** an empty-state is shown; the "add root node" control is enabled (A is `structural_owner`, the resolver returns A for `parent=null`).
- **Covers:** `addNode` (affordance only); events: none yet.

### WEB_UI-009
- **Title:** Non-owner sees add-root affordance in suggest styling
- **Level:** integration
- **Preconditions / fixtures:** empty sheet `S'`; `loginAs(E)`.
- **Then** the "add root node" control renders in suggest mode (e.g. "Suggest a node") because `viewer.per_node_can_structure[root]` is false for E.
- **Covers:** `addNode`/`suggestChange` (affordance); events: none yet.

### WEB_UI-010
- **Title:** Snapshot reload after an event reconciles tree without full remount
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; a `NODE_CREATED` arrives (new child W under P1) via refetch.
- **When** the snapshot is refetched and applied.
- **Then** W appears under P1 in correct `idx` position; existing expand/collapse view state for unrelated nodes (P2 collapsed) is preserved.
- **Covers:** `getSheetSnapshot`; events: `NODE_CREATED` (consumed, not emitted by UI).

---

## 2. Inline cell edit — owned (direct) vs non-owned (suggest)

### WEB_UI-011
- **Title:** Owner edits owned cell → executeAction(updateCell) → executed commit
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; `loginAs(B)`; `executeAction` mocked to return `kind:"executed"`; B owns `col:notes`.
- **When** B double-clicks cell (X, col:notes), types "ship by Q3", commits (Enter/blur).
- **Then** `executeAction("updateCell", {sheet:S, node:X, column:col:notes, value:"ship by Q3"})` is called exactly once; on `executed` the cell shows the new value, a "Saved" affordance flashes, no CR banner.
- **Covers:** `updateCell`; events: `NODE_VALUE_UPDATED`.

### WEB_UI-012
- **Title:** Editing the label cell routes through updateCell on the is_label column (Axis 2)
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; `loginAs(B)` (owns `col:name`).
- **When** B renames node X via the label cell to "Task X v2".
- **Then** the call is `updateCell` with `column = col:name` (the is_label column) — **not** a node-rename structural call; outcome `executed`.
- **Covers:** `updateCell`; events: `NODE_VALUE_UPDATED`. (Asserts ARCHITECTURE §2.3 — label is Axis 2.)

### WEB_UI-013
- **Title:** Editor (not owner) on a column can edit directly
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; `loginAs(B)`; B is an **editor** on `col:status`; `viewer.per_column_can_edit[col:status]=true`.
- **When** B sets (X, col:status).
- **Then** the cell renders in **owned/edit** mode (not suggest); `updateCell` called; outcome `executed`.
- **Covers:** `updateCell`; events: `NODE_VALUE_UPDATED`. (PERMISSIONS §B: editors edit directly.)

### WEB_UI-014
- **Title:** Non-owner edit → suggest-mode UI → executeAction returns suggested
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; `loginAs(A)`; A owns no columns; `viewer.per_column_can_edit[col:budget]=false`; `executeAction` mocked to return `kind:"suggested", change_request:CR1, resolved_approver:C`.
- **When** A edits (X, col:budget) to 500.
- **Then** the input renders in **suggest styling** ("Suggest a change"); on commit `executeAction("updateCell", …)` is called; outcome `suggested` → the cell **reverts** to its snapshot value, a banner "Suggestion sent to C" referencing CR1 shows, and a pending marker appears on the cell.
- **Covers:** `updateCell` (→ CR path); events: `CHANGE_PROPOSED`.

### WEB_UI-015
- **Title:** Non-owned cell affordance is visually distinguished before interaction
- **Level:** unit
- **Preconditions / fixtures:** `loginAs(E)`; `viewer.per_column_can_edit` all false.
- **Then** every editable cell shows a suggest-affordance (e.g. pencil-with-arrow / muted style) rather than a direct-edit affordance; read-only-by-policy cells (`editable=false`) show neither.
- **Covers:** none (affordance derivation from `viewer` flags).

### WEB_UI-016
- **Title:** Column with `editable=false` is non-editable for everyone, even the owner
- **Level:** unit
- **Preconditions / fixtures:** `col:budget` snapshot has `editable=false`; `loginAs(C)` (owner).
- **Then** the cell is read-only; no edit/suggest affordance; double-click does nothing; `executeAction` never called.
- **Covers:** `updateCell` (suppressed); events: none. (DATA-MODEL §2 `editable`.)

### WEB_UI-017
- **Title:** Escape cancels an in-progress edit without calling executeAction
- **Level:** unit
- **Preconditions / fixtures:** `loginAs(B)`; editing (X, col:notes).
- **When** B types then presses Escape.
- **Then** the cell reverts to the original value; `executeAction` is not called.
- **Covers:** `updateCell` (negative); events: none.

### WEB_UI-018
- **Title:** Committing an unchanged value is a no-op (no executeAction)
- **Level:** unit
- **Preconditions / fixtures:** `loginAs(B)`; (X, col:notes) value "v1".
- **When** B enters edit and commits without changing the value.
- **Then** no `updateCell` is dispatched (UI diffs against snapshot); no event.
- **Covers:** `updateCell` (idempotency guard); events: none.

### WEB_UI-019
- **Title:** Number column rejects non-numeric input client-side
- **Level:** unit
- **Preconditions / fixtures:** `loginAs(C)`; `col:budget` type `number`.
- **When** C types "abc".
- **Then** input is rejected/blocked or commit is disabled with a validation hint; `executeAction` not called with an invalid value (mirrors server `params_schema` typing).
- **Covers:** `updateCell`; events: none. (Boundary: type validation.)

### WEB_UI-020
- **Title:** Server-side suggested outcome on a value the UI optimistically committed → revert
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(B)`; optimistic UI enabled; `executeAction` returns `suggested` despite the UI predicting `executed` (e.g. `viewer` flags stale).
- **When** B commits an edit, UI optimistically shows the new value, then `suggested` arrives.
- **Then** the optimistic value is **rolled back** to snapshot; the suggestion banner shows. (Guards against trusting client-side prediction over the authoritative outcome.)
- **Covers:** `updateCell`; events: `CHANGE_PROPOSED`.

### WEB_UI-021
- **Title:** Owner-self policy (owners_must_use_change_requests) → owner's edit yields suggested
- **Level:** integration
- **Preconditions / fixtures:** sheet `S` with `settings.owners_must_use_change_requests=true`; `loginAs(B)` (owns `col:notes`); `executeAction` returns `kind:"suggested", resolved_approver:B`.
- **When** B edits (X, col:notes).
- **Then** the UI renders the suggested outcome (banner "Suggestion sent to B" — self-approver), cell not committed until the CR is approved. The UI does not assume owner==direct; it honors the returned `Outcome`.
- **Covers:** `updateCell`; events: `CHANGE_PROPOSED`. (PERMISSIONS §1.2.)

### WEB_UI-022
- **Title:** Pending suggestion marker clears after approval refetch
- **Level:** integration
- **Preconditions / fixtures:** WEB_UI-014 state (CR1 pending on (X,col:budget)); approval happens server-side; snapshot refetch now shows value 500.
- **When** the snapshot is refetched.
- **Then** the pending marker clears and the committed value 500 shows.
- **Covers:** `getSheetSnapshot`; events consumed: `CHANGE_APPROVED`, `NODE_VALUE_UPDATED`.

### WEB_UI-023
- **Title:** Concurrent edit conflict surfaces a version-stale error
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(C)`; (X, col:budget) snapshot `version=3`; `executeAction` returns an error `VERSION_CONFLICT` (server saw `version=4`).
- **When** C commits an edit based on stale version.
- **Then** the UI shows a conflict notice, does not silently overwrite, offers refresh; the local optimistic value is reverted.
- **Covers:** `updateCell`; events: none (rejected before mutation). (Conflict/idempotency.)

### WEB_UI-024
- **Title:** Empty / cleared value commit on a text column sends explicit empty value
- **Level:** unit
- **Preconditions / fixtures:** `loginAs(B)`; (X, col:notes)="v1".
- **When** B clears the cell and commits.
- **Then** `updateCell` is sent with `value:""` (a deliberate clear, distinct from no-op); outcome rendered accordingly.
- **Covers:** `updateCell`; events: `NODE_VALUE_UPDATED`. (Boundary: empty vs unchanged.)

### WEB_UI-025
- **Title:** Two rapid commits on the same cell are serialized, not interleaved
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(B)`; slow `executeAction`.
- **When** B commits "a", then immediately "ab" before the first resolves.
- **Then** calls are serialized (second waits or supersedes the first deterministically); final committed state matches the last resolved outcome; no lost-update race in the UI.
- **Covers:** `updateCell`; events: `NODE_VALUE_UPDATED`. (Idempotency/ordering.)

---

## 3. Split-column types (single-select-split / multi-select-split)

### WEB_UI-026
- **Title:** single-select-split renders one option group with mutually-exclusive segments
- **Level:** unit
- **Preconditions / fixtures:** `col:status` type `single-select-split`, `options={groups:[{label:"Stage",options:["todo","doing","done"]}]}`.
- **Then** the cell renders the split control with segments todo/doing/done; selecting "doing" deselects others; the rendered value is a single scalar (stored as a 1-element array per DATA-MODEL §4 "selects store arrays").
- **Covers:** `getSheetSnapshot` render; events: none.

### WEB_UI-027
- **Title:** single-select-split commit sends a single-element array value
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(C)` (owner of `col:status`); `executeAction` → executed.
- **When** C selects "done" on (X, col:status).
- **Then** `updateCell({…, column:col:status, value:["done"]})` is called; committed.
- **Covers:** `updateCell`; events: `NODE_VALUE_UPDATED`.

### WEB_UI-028
- **Title:** multi-select-split allows multiple selections and sends an array
- **Level:** integration
- **Preconditions / fixtures:** `col:tags` type `multi-select-split`, owner C; `loginAs(C)`.
- **When** C selects "urgent" and "backend".
- **Then** `updateCell({…, value:["urgent","backend"]})`; both segments render active.
- **Covers:** `updateCell`; events: `NODE_VALUE_UPDATED`.

### WEB_UI-029
- **Title:** multi-select-split deselect removes one value, preserving the rest
- **Level:** integration
- **Preconditions / fixtures:** (X, col:tags)=["urgent","backend"]; `loginAs(C)`.
- **When** C deselects "urgent".
- **Then** `updateCell({…, value:["backend"]})`; order preserved.
- **Covers:** `updateCell`; events: `NODE_VALUE_UPDATED`.

### WEB_UI-030
- **Title:** Split control on a non-owned column is read/suggest-only
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(E)`; `col:status` not editable by E.
- **When** E interacts with the split segments.
- **Then** selection opens suggest-mode; commit dispatches `updateCell` and renders the `suggested` outcome (CR to C) — segments revert to snapshot until approval.
- **Covers:** `updateCell`; events: `CHANGE_PROPOSED`.

### WEB_UI-031
- **Title:** Split options come from snapshot column config, not hardcoded
- **Level:** unit
- **Preconditions / fixtures:** snapshot where `col:status.options` lists 4 options.
- **Then** exactly the 4 configured segments render; if options later change (COLUMN_CONFIG_UPDATED + refetch) the control re-renders the new set.
- **Covers:** `getSheetSnapshot`; events consumed: `COLUMN_CONFIG_UPDATED`.

### WEB_UI-032
- **Title:** Empty multi-select commit sends empty array (clear all)
- **Level:** unit
- **Preconditions / fixtures:** (X, col:tags)=["urgent"]; `loginAs(C)`.
- **When** C deselects the last value.
- **Then** `updateCell({…, value:[]})`; control shows none selected. (Boundary.)
- **Covers:** `updateCell`; events: `NODE_VALUE_UPDATED`.

### WEB_UI-033
- **Title:** Stored value not in current options renders as an "unknown/legacy" chip
- **Level:** unit
- **Preconditions / fixtures:** (X, col:status)=["archived"] but options no longer include "archived".
- **Then** the control shows "archived" flagged as out-of-set (not silently dropped) so the user can re-pick; committing a valid pick replaces it. (Boundary/robustness.)
- **Covers:** `getSheetSnapshot`; events: none.

### WEB_UI-034
- **Title:** single-select-split cannot hold two values via the UI
- **Level:** unit
- **Preconditions / fixtures:** `loginAs(C)`; `col:status` single.
- **When** C clicks "todo" then "done".
- **Then** only "done" is selected; the committed array has length 1 (UI enforces single cardinality regardless of click sequence).
- **Covers:** `updateCell`; events: `NODE_VALUE_UPDATED`. (Boundary.)

### WEB_UI-035
- **Title:** Split control keyboard accessibility (arrow/space) and ARIA roles
- **Level:** unit
- **Preconditions / fixtures:** `loginAs(C)`; split control focused.
- **Then** segments are reachable by arrow keys, toggled by space/enter, expose `role=radiogroup` (single) or `role=group`+`aria-pressed` (multi); commit behavior identical to mouse.
- **Covers:** `updateCell` (a11y path); events: `NODE_VALUE_UPDATED`.

---

## 4. Drag-and-drop reorder / move (before / after / inside)

> Move authority is Axis 1 and requires authority over **both** src and dest parents
> (PERMISSIONS §4). Reorder among siblings of the **same** parent is still a `moveNode`
> (same parent, changed `after`). The UI computes `{node, new_parent, after}` from the
> drop target and calls `executeAction("moveNode", …)`.

### WEB_UI-036
- **Title:** Drop "inside" a group sets new_parent = that group
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; `loginAs(A)`; drag X onto P2 with an **inside** drop indicator; `executeAction` mocked.
- **When** the drop completes.
- **Then** `executeAction("moveNode", {sheet:S, node:X, new_parent:P2, after:null})` is called. (A authority over both ends decides the outcome separately — see WEB_UI-041.)
- **Covers:** `moveNode`; events: `NODE_MOVED`.

### WEB_UI-037
- **Title:** Drop "before" a sibling sets new_parent = sibling's parent, after = previous sibling
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; `loginAs(D)`; drag Z to **before** Y (both under P2).
- **When** dropped.
- **Then** `moveNode({node:Z, new_parent:P2, after:null})` (Z becomes first child; `after=null` means head). D owns P2 (both ends) → outcome `executed`.
- **Covers:** `moveNode`; events: `NODE_MOVED`.

### WEB_UI-038
- **Title:** Drop "after" a sibling sets after = that sibling
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; `loginAs(D)`; drag Y to **after** Z under P2.
- **When** dropped.
- **Then** `moveNode({node:Y, new_parent:P2, after:Z})`; outcome `executed`; sibling order becomes Z, Y.
- **Covers:** `moveNode`; events: `NODE_MOVED`.

### WEB_UI-039
- **Title:** Three drop zones are distinguishable per row (before / inside / after)
- **Level:** unit
- **Preconditions / fixtures:** rendered row P2 (a group) during a drag.
- **Then** hovering the top edge shows "before", the middle (for a group) shows "inside", the bottom edge shows "after"; a leaf row (X) offers only before/after (no inside).
- **Covers:** `moveNode` (drop-target geometry); events: none until drop.

### WEB_UI-040
- **Title:** Pure reorder within same parent emits moveNode with same new_parent
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(D)`; reorder Y/Z under P2.
- **When** D drags Z above Y.
- **Then** `moveNode({node:Z, new_parent:P2, after:null})` — same parent, changed ordering; outcome `executed`. (Reorder is not a separate capability.)
- **Covers:** `moveNode`; events: `NODE_MOVED`.

### WEB_UI-041
- **Title:** Move across branches without authority at one end → suggested (CR to dest)
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; `loginAs(A)`; move X (src parent P1, A) → into P2 (dest D). A is src approver but not dest; `executeAction` returns `kind:"suggested", resolved_approver:D, co_approvers:[A]`.
- **When** A drops X inside P2.
- **Then** the tree **reverts** to pre-drag layout; a banner "Move suggested to D (co-approver: A)" shows referencing the CR. (PERMISSIONS §4, example A `moveNode(X→P2)`.)
- **Covers:** `moveNode`; events: `CHANGE_PROPOSED`.

### WEB_UI-042
- **Title:** Delegated owner moving within own branch → executed
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; `loginAs(D)`; move Z under Y (both inside P2; D owns both ends).
- **When** D drops Z inside Y (making Y a group).
- **Then** `moveNode({node:Z, new_parent:Y})`; outcome `executed`; Z renders nested under Y.
- **Covers:** `moveNode`; events: `NODE_MOVED`. (PERMISSIONS §D.)

### WEB_UI-043
- **Title:** Move out of a delegated branch to a parent owned by A → suggested to A
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; `loginAs(C)`; drag Y (src D) → under P1 (dest A); C owns neither end; `executeAction` returns `suggested, resolved_approver:A`.
- **When** C drops.
- **Then** revert + "suggested to A" banner. (PERMISSIONS §C `moveNode(Y→P1)`.)
- **Covers:** `moveNode`; events: `CHANGE_PROPOSED`.

### WEB_UI-044
- **Title:** Illegal drop — node onto its own descendant — is rejected client-side
- **Level:** unit
- **Preconditions / fixtures:** sheet `S`; drag P2 onto Z (Z is P2's descendant).
- **Then** the drop indicator shows "not allowed"; drop is blocked; `executeAction` is **not** called (cycle prevention before the server round-trip).
- **Covers:** `moveNode` (negative); events: none. (Boundary: NestedSet cycle.)

### WEB_UI-045
- **Title:** Drop node onto itself is a no-op
- **Level:** unit
- **Preconditions / fixtures:** drag X, drop on X.
- **Then** no `moveNode` call; tree unchanged.
- **Covers:** `moveNode` (negative); events: none.

### WEB_UI-046
- **Title:** Move to root level sends new_parent = null
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(A)`; drag X to the root drop zone (top-level, after P2).
- **Then** `moveNode({node:X, new_parent:null, after:P2})`. (Root authority resolves to A.)
- **Covers:** `moveNode`; events: `NODE_MOVED`.

### WEB_UI-047
- **Title:** Optimistic move animation reverts on suggested/error outcome
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(A)`; WEB_UI-041 setup with optimistic reorder enabled.
- **When** X visually moves into P2, then `suggested` arrives.
- **Then** the row animates back to its original position under P1; final layout equals snapshot.
- **Covers:** `moveNode`; events: `CHANGE_PROPOSED`.

### WEB_UI-048
- **Title:** Drag a node whose subtree is collapsed moves the whole subtree
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(A)`; P1 collapsed (hiding X); drag P1 after P2.
- **Then** `moveNode({node:P1, new_parent:null, after:P2})`; on refetch X is still under P1 (subtree integrity); P1 remains collapsed.
- **Covers:** `moveNode`; events: `NODE_MOVED`.

### WEB_UI-049
- **Title:** Keyboard-driven move (cut / paste-into) issues the same moveNode
- **Level:** unit
- **Preconditions / fixtures:** `loginAs(D)`; focus Z, invoke "cut", focus Y, invoke "paste inside".
- **Then** equivalent `moveNode({node:Z, new_parent:Y})` call; a11y parity with drag.
- **Covers:** `moveNode`; events: `NODE_MOVED`.

### WEB_UI-050
- **Title:** Drag-and-drop disabled entirely when viewer has no structural authority anywhere
- **Level:** unit
- **Preconditions / fixtures:** `loginAs(E)`; `viewer.per_node_can_structure` all false.
- **Then** rows are still draggable but every drop resolves to suggest-mode (a drop produces a `moveNode` whose `suggested` outcome is rendered) — the UI does not silently swallow the intent; alternatively a global "suggest moves" hint is shown. Asserts E can still *propose* moves.
- **Covers:** `moveNode`; events: `CHANGE_PROPOSED`.

---

## 5. Schema editor (add / remove / configure columns) — permission-gated

> `addColumn` is gated on the sheet `structural_owner` (A); `updateColumn`/`deleteColumn`
> on the column's Axis-2 approvers; `grantColumn` on current `column_owner` or sheet owner
> (CAPABILITIES registry). All emit `COLUMN_CONFIG_UPDATED`.

### WEB_UI-051
- **Title:** Sheet owner sees enabled "Add column"; non-owner sees suggest/disabled
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; render header toolbar for `loginAs(A)` then `loginAs(C)`.
- **Then** for A the "Add column" button is enabled (direct); for C it is shown in suggest-mode or disabled per `viewer.can_add_column=false`.
- **Covers:** `addColumn`; events: none yet.

### WEB_UI-052
- **Title:** Owner adds a column → executeAction(addColumn) with full schema
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(A)`; `executeAction` → executed; new col `field:priority`, type `number`, owner C.
- **When** A fills the add-column form (field, label, type, column_owner) and submits.
- **Then** `executeAction("addColumn", {sheet:S, field:"priority", label:"Priority", type:"number", column_owner:C})`; on executed the new column header renders.
- **Covers:** `addColumn`; events: `COLUMN_CONFIG_UPDATED`.

### WEB_UI-053
- **Title:** Add-column form enforces the allowed type enum
- **Level:** unit
- **Preconditions / fixtures:** add-column form rendered.
- **Then** the type selector offers exactly `text, multiline-text, number, single-select-split, multi-select-split` (CAPABILITIES schema); no other type is selectable.
- **Covers:** `addColumn`; events: none. (Boundary: enum.)

### WEB_UI-054
- **Title:** Select-split column requires options config before submit
- **Level:** unit
- **Preconditions / fixtures:** add-column form; type set to `single-select-split`.
- **Then** an options editor appears; submit is disabled until at least one option is defined; for non-select types the options editor is hidden.
- **Covers:** `addColumn`; events: none. (Validation.)

### WEB_UI-055
- **Title:** Non-owner add-column submit renders suggested outcome
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(C)`; `executeAction("addColumn",…)` returns `kind:"suggested", resolved_approver:A`.
- **When** C submits an add-column.
- **Then** no column header appears; a "Schema change suggested to A" banner shows. (Surface honors outcome even if it optimistically rendered the toolbar in suggest-mode.)
- **Covers:** `addColumn`; events: `CHANGE_PROPOSED`.

### WEB_UI-056
- **Title:** Column owner configures (renames/resizes) own column → updateColumn
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(C)` (owns `col:budget`); open column settings; change label to "Budget ($)" and width.
- **When** C saves.
- **Then** `executeAction("updateColumn", {sheet:S, column:col:budget, patch:{label:"Budget ($)", width:…}})`; executed; header re-renders.
- **Covers:** `updateColumn`; events: `COLUMN_CONFIG_UPDATED`.

### WEB_UI-057
- **Title:** Non-approver configuring a column → suggested
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(E)`; open `col:budget` settings; `executeAction` → suggested to C.
- **When** E saves a label change.
- **Then** the header does not change; "suggested to C" banner. (PERMISSIONS Axis 2: updateColumn approvers = column owner/editors.)
- **Covers:** `updateColumn`; events: `CHANGE_PROPOSED`.

### WEB_UI-058
- **Title:** Column owner deletes a column → deleteColumn (with confirm)
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(C)` (owns `col:budget`); click delete, confirm in dialog.
- **When** confirmed.
- **Then** `executeAction("deleteColumn", {sheet:S, column:col:budget})`; executed; the column and its cells disappear after refetch.
- **Covers:** `deleteColumn`; events: `COLUMN_CONFIG_UPDATED`.

### WEB_UI-059
- **Title:** Delete is blocked / warned for the is_label column
- **Level:** unit
- **Preconditions / fixtures:** `loginAs(B)` (owns `col:name`, is_label); attempt delete.
- **Then** the UI blocks or strongly warns (exactly one is_label column must exist per sheet — DATA-MODEL §13); `deleteColumn` is not dispatched without reassigning the label column first.
- **Covers:** `deleteColumn` (constraint guard); events: none. (Boundary.)

### WEB_UI-060
- **Title:** grantColumn from the column settings reassigns owner/editors
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(C)` (current owner of `col:status`); open "Ownership"; add F as editor, keep owner.
- **When** C saves.
- **Then** `executeAction("grantColumn", {sheet:S, column:col:status, column_owner:C, editors:[B,F]})`; executed; editor list reflects B,F.
- **Covers:** `grantColumn`; events: `COLUMN_CONFIG_UPDATED`.

### WEB_UI-061
- **Title:** Non-owner cannot open grantColumn directly (suggest or hidden)
- **Level:** unit
- **Preconditions / fixtures:** `loginAs(E)`; `col:status` settings.
- **Then** the Ownership tab is hidden or disabled (grantColumn gated to column_owner or sheet owner); E cannot dispatch `grantColumn` directly.
- **Covers:** `grantColumn` (gating); events: none.

### WEB_UI-062
- **Title:** Duplicate field key is rejected before submit
- **Level:** unit
- **Preconditions / fixtures:** `loginAs(A)`; add-column form; field set to existing `name`.
- **Then** inline validation flags the duplicate ((sheet, field) unique — DATA-MODEL §13); submit disabled; `addColumn` not dispatched.
- **Covers:** `addColumn`; events: none. (Boundary: uniqueness.)

---

## 6. AI agent sidebar (thin shell → agent.chat)

> The sidebar is a thin client over `POST agent.chat {sheet, message}` (ARCHITECTURE §8).
> It owns **zero** mutation logic; it streams Re-Act frames and links to any CRs the agent
> files. The agent acts as its **own User** under the same ACL, so its unauthorized actions
> become CRs (ARCHITECTURE §8, PERMISSIONS §4.5).

### WEB_UI-063
- **Title:** Sending a message calls agent.chat with sheet + message
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; sidebar open; `agent.chat` mocked.
- **When** the user types "set X status to done" and sends.
- **Then** `agent.chat({sheet:S, message:"set X status to done"})` is called once; the message appears in the transcript.
- **Covers:** `arbor.agent.chat`; events: depends on agent actions.

### WEB_UI-064
- **Title:** Streamed Re-Act frames render in order (thought → action → observation → final)
- **Level:** integration
- **Preconditions / fixtures:** `agent.chat` streams frames: thought, action(updateCell), observation(executed), final.
- **Then** the sidebar renders each frame in arrival order; action frames show the tool name + params; the final natural-language summary renders last.
- **Covers:** `arbor.agent.chat`; events: `NODE_VALUE_UPDATED` (from the agent's action).

### WEB_UI-065
- **Title:** Agent action that executes refreshes the affected tree/cell
- **Level:** e2e
- **Preconditions / fixtures:** real-ish stack; agent identity owns/edits `col:status`; message asks to set (X, col:status)=done.
- **When** the agent runs `updateCell` and it executes.
- **Then** the main grid's (X, col:status) cell updates to "done" (sidebar action → snapshot refetch / event-driven update); transcript shows "I updated 1 cell".
- **Covers:** `updateCell` via agent; events: `NODE_VALUE_UPDATED`.

### WEB_UI-066
- **Title:** Agent action lacking authority surfaces a Change Request, not a mutation
- **Level:** integration
- **Preconditions / fixtures:** agent user owns no columns; message asks to change (X, col:budget); `agent.chat` streams an action whose observation is `suggested` (CR to C).
- **Then** the grid cell does **not** change; the transcript/final says a change request was filed for C; a CR chip linking to the CR renders. (Key governance property: agent = human under ACL.)
- **Covers:** `updateCell` via agent → CR; events: `CHANGE_PROPOSED`.

### WEB_UI-067
- **Title:** Mixed-outcome summary ("updated N, filed M CRs") renders accurately
- **Level:** integration
- **Preconditions / fixtures:** agent stream: 3 executed updateCell + 2 suggested.
- **Then** the final summary reflects "3 cells updated, 2 change requests filed"; 2 CR chips render; grid reflects only the 3 executed changes after refetch.
- **Covers:** `updateCell`/`addNode`; events: `NODE_VALUE_UPDATED`, `CHANGE_PROPOSED`.

### WEB_UI-068
- **Title:** Sidebar never calls a mutation capability directly
- **Level:** unit
- **Preconditions / fixtures:** spy on `executeAction`; interact only with the sidebar.
- **Then** the sidebar's only network call for actions is `agent.chat`; `executeAction` is not invoked by the sidebar (it is a thin shell). (ARCHITECTURE §8 thin-shell invariant.)
- **Covers:** none — negative/architectural guard.

### WEB_UI-069
- **Title:** Streaming error mid-loop shows a recoverable error, transcript preserved
- **Level:** integration
- **Preconditions / fixtures:** `agent.chat` stream errors after the first observation.
- **Then** prior frames remain visible; an inline error with retry shows; no partial state is assumed committed (UI relies on emitted events/refetch, not on the transcript).
- **Covers:** `arbor.agent.chat`; events: whatever already emitted.

### WEB_UI-070
- **Title:** Agent tools shown to the user exclude non-LLM capabilities
- **Level:** unit
- **Preconditions / fixtures:** sidebar's "what can the agent do" affordance reads tool list from `getLLMTools`-derived metadata.
- **Then** the listed tools exclude `internalReset` (`is_exposed_to_llm=false`); all other capabilities appear. (CAPABILITIES getLLMTools contract.)
- **Covers:** `getLLMTools` (rendering); events: none.

### WEB_UI-071
- **Title:** Empty / whitespace message is not sent
- **Level:** unit
- **Preconditions / fixtures:** sidebar input empty.
- **Then** send is disabled; `agent.chat` not called. (Boundary.)
- **Covers:** `arbor.agent.chat` (negative); events: none.

### WEB_UI-072
- **Title:** CR chip from the agent transcript opens the same CR review UI as the grid
- **Level:** integration
- **Preconditions / fixtures:** WEB_UI-066 produced CR for C; `loginAs(C)` viewing.
- **When** C clicks the CR chip.
- **Then** the standard Change Request review panel opens (approve/reject), wired to `approveChange`/`rejectChange` — same component used elsewhere (DRY; no agent-specific approval path).
- **Covers:** `approveChange`/`rejectChange`; events: `CHANGE_APPROVED`/`CHANGE_REJECTED`.

### WEB_UI-073
- **Title:** Agent actor_type is reflected as "agent" in resulting event/notification UI
- **Level:** integration
- **Preconditions / fixtures:** agent executes a cell update; subsequent activity feed / event row.
- **Then** the activity entry attributes the change to the agent user with an "agent" actor badge (distinguishing `actor_type=agent` from `human`). (DATA-MODEL §12 actor_type.)
- **Covers:** `updateCell`; events: `NODE_VALUE_UPDATED` (actor_type=agent).

---

## 7. Import / export snapshot

> Export serializes the current sheet via the shared snapshot serializer
> (`getSheetSnapshot`); import replays into a sheet and (on success) emits
> `IMPORT_COMPLETED` (DATA-MODEL §12 event set). Import is governed — rows land via
> capabilities, so unauthorized content becomes CRs, not silent writes.

### WEB_UI-074
- **Title:** Export downloads the full snapshot (columns + nodes + values + ownership)
- **Level:** integration
- **Preconditions / fixtures:** sheet `S`; `loginAs(A)`; `getSheetSnapshot` mocked.
- **When** the user clicks Export.
- **Then** a file is produced whose contents equal the snapshot shape: all columns with `column_owner`/`editors`/`options`, all nodes with parent/ordering, all cell values+versions; round-trips through the same serializer used for render.
- **Covers:** `getSheetSnapshot`; events: none.

### WEB_UI-075
- **Title:** Export reflects the viewer's read scope, not more
- **Level:** unit
- **Preconditions / fixtures:** `loginAs(E)`; snapshot already filtered to E's visible scope by the server.
- **Then** the export equals exactly what the snapshot returned (UI does not fabricate hidden data); no client-side privilege escalation.
- **Covers:** `getSheetSnapshot`; events: none.

### WEB_UI-076
- **Title:** Import preview validates structure before any write
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(A)`; choose a valid export file for an empty target sheet.
- **When** the file is selected.
- **Then** a preview/diff renders (columns to add, nodes to create, cells to set); **no** `executeAction` fires until the user confirms.
- **Covers:** `addColumn`/`addNode`/`updateCell` (deferred); events: none yet.

### WEB_UI-077
- **Title:** Confirming import replays through capabilities (governed), not raw writes
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(A)`; confirmed import of columns + small tree.
- **When** confirmed.
- **Then** the UI issues capability calls (`addColumn`, `addNode`, `updateCell`) — or a single import endpoint that itself funnels through `execute_action` — never a direct DocType write; on success `IMPORT_COMPLETED` is reflected (toast + refetch).
- **Covers:** `addColumn`,`addNode`,`updateCell`; events: `IMPORT_COMPLETED` (+ underlying NODE_CREATED/NODE_VALUE_UPDATED/COLUMN_CONFIG_UPDATED).

### WEB_UI-078
- **Title:** Import where the actor lacks authority yields CRs, not failures
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(E)` importing into sheet `S`; E owns nothing; capability calls return `suggested`.
- **When** E confirms import.
- **Then** the result summarizes "N change requests created" (each routed to the resolved approver); the tree/cells do not change directly. (Governance: import is not a privilege bypass.)
- **Covers:** `addNode`/`updateCell` → CR; events: `CHANGE_PROPOSED`.

### WEB_UI-079
- **Title:** Malformed import file is rejected with a clear error
- **Level:** unit
- **Preconditions / fixtures:** `loginAs(A)`; choose a file with invalid JSON / missing required keys.
- **Then** an error renders identifying the problem; no preview, no `executeAction`; the user can pick another file.
- **Covers:** none (client validation); events: none. (Boundary.)

### WEB_UI-080
- **Title:** Import file referencing an unknown column type is rejected
- **Level:** unit
- **Preconditions / fixtures:** import file with a column `type:"rich-text"` (not in the enum).
- **Then** validation flags the unsupported type before write; user must fix/remap. (Boundary: type enum parity with CAPABILITIES.)
- **Covers:** `addColumn`; events: none.

### WEB_UI-081
- **Title:** Import is idempotency-aware: re-importing the same file does not duplicate
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(A)`; import once (succeeds), then import the same file again.
- **Then** the second preview detects existing matching nodes/columns and offers skip/merge rather than blindly creating duplicates (unique `(sheet, field)` and stable node identity respected); confirming "skip" issues no redundant `addColumn`.
- **Covers:** `addColumn`/`addNode`; events: none on skip. (Idempotency.)

### WEB_UI-082
- **Title:** Export → import round-trip preserves tree shape, ownership, and split values
- **Level:** e2e
- **Preconditions / fixtures:** `loginAs(A)`; export sheet `S`, import into a fresh sheet `S2`.
- **Then** `S2` reproduces R/P1/X/P2/Y/Z structure, the four+ columns with owners B/C and editors, and split-column array values; `IMPORT_COMPLETED` emitted; a `getSheetSnapshot(S2)` equals `getSheetSnapshot(S)` modulo ids/timestamps.
- **Covers:** `getSheetSnapshot`,`addColumn`,`addNode`,`updateCell`; events: `IMPORT_COMPLETED`.

---

## 8. Cross-cutting: surface parity, conflict/idempotency, errors

### WEB_UI-083
- **Title:** Surface parity — web updateCell call shape equals the REST method contract
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(B)`; capture the request the UI sends for an owned edit.
- **Then** the UI's `executeAction("updateCell", params)` body matches the documented `POST /api/method/arbor.update_cell` params (sheet, node, column, value) exactly — same capability, same ACL path (ARCHITECTURE §11). No UI-only fields are injected.
- **Covers:** `updateCell`; events: `NODE_VALUE_UPDATED`. (Invariant: surface parity.)

### WEB_UI-084
- **Title:** Every mutating affordance maps to a registry capability id (no ad-hoc calls)
- **Level:** unit
- **Preconditions / fixtures:** enumerate all interactive controls (cell edit, add/move/delete node, schema ops, CR decisions, subscribe, delegate).
- **Then** each dispatches `executeAction` with an `action_id` that exists in the capability registry; there is no UI control that mutates outside `executeAction`. (ARCHITECTURE §4.1(a).)
- **Covers:** all mutating capabilities; events: respective. (Architectural guard.)

### WEB_UI-085
- **Title:** Unauthorized executeAction response (server denies even the suggest) shows error, not silent commit
- **Level:** integration
- **Preconditions / fixtures:** `executeAction` returns a hard error (e.g. actor not a sheet member at all).
- **Then** the UI shows the error and does not commit optimistic state; distinct from the `suggested` path.
- **Covers:** any capability; events: none.

### WEB_UI-086
- **Title:** Stale viewer flags corrected by authoritative Outcome (executed where UI predicted suggest)
- **Level:** integration
- **Preconditions / fixtures:** `viewer.per_column_can_edit[col:notes]=false` (stale) but server returns `executed`.
- **When** the user (actually now an editor) commits an edit.
- **Then** the UI commits the value on `executed` even though it had rendered suggest-mode; the authoritative `Outcome` wins over client prediction (symmetric to WEB_UI-020).
- **Covers:** `updateCell`; events: `NODE_VALUE_UPDATED`.

### WEB_UI-087
- **Title:** Network failure during a mutation reverts optimistic state and offers retry
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(B)`; `executeAction` rejects with a network error.
- **Then** optimistic edit reverts; a retry affordance appears; no event assumed.
- **Covers:** `updateCell`; events: none.

### WEB_UI-088
- **Title:** Approve/withdraw controls visibility follows requester vs approver role
- **Level:** unit
- **Preconditions / fixtures:** CR1 (requester E, resolved_approver C); render CR panel as `loginAs(C)`, `loginAs(E)`, `loginAs(F)`.
- **Then** C sees Approve/Reject (and not Withdraw); E sees Withdraw (and not Approve/Reject); F sees neither (read-only). (PERMISSIONS §4.7.)
- **Covers:** `approveChange`/`rejectChange`/`withdrawChange`; events: none until acted.

### WEB_UI-089
- **Title:** Double-clicking Approve does not double-replay the capability
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(C)`; CR1 pending; click Approve twice rapidly.
- **Then** `approveChange` is dispatched once (button disables on first click / response); the CR reaches `approved` exactly once; no duplicate `CHANGE_APPROVED`. (Idempotency.)
- **Covers:** `approveChange`; events: `CHANGE_APPROVED` (once).

### WEB_UI-090
- **Title:** Acknowledge affordance appears only for requires_ack notifications (persona G)
- **Level:** integration
- **Preconditions / fixtures:** `loginAs(G)`; in-app notification with `requires_ack=true` for a `CHANGE_APPROVED`/`NODE_DELETED` in P2.
- **When** G opens the notification.
- **Then** an "Acknowledge" button shows; clicking dispatches `executeAction("acknowledge", {notification})`; after success the item shows acked state; non-ack notifications show no such button. (PERMISSIONS §G.)
- **Covers:** `acknowledge`; events: none (Acknowledgement row; no Tree Event).

---

## Coverage map (capability → cases)

| Capability | Cases |
|---|---|
| `getSheetSnapshot` | 001,002,003,007,010,022,026,031,033,074,075,082 |
| `addNode` | 008,009,077,078,082 |
| `updateCell` | 011–025,027–030,032,034,035,065,066,067,073,077,078,082,083,086,087 |
| `moveNode` | 036–050 |
| `deleteNode` | (affordance referenced; structural delete covered by backend) — 084 |
| `addColumn` | 051–055,062,076,077,080,081,082 |
| `updateColumn` | 056,057 |
| `deleteColumn` | 058,059 |
| `grantColumn` | 060,061 |
| `delegateBranch` | 084 (affordance enumeration) |
| `suggestChange` | 009,014,030 (suggest-mode rendering) |
| `approveChange` | 072,088,089 |
| `rejectChange` | 072,088 |
| `withdrawChange` | 088 |
| `acknowledge` | 090 |
| `arbor.agent.chat` | 063–073 |
| `getLLMTools` | 070 |

| Tree Event | Cases asserting (emitted or consumed) |
|---|---|
| `NODE_CREATED` | 010,077,082 |
| `NODE_MOVED` | 036–040,042,046,048,049 |
| `NODE_VALUE_UPDATED` | 011,012,013,024,025,027–029,032,034,035,065,067,073,077,082,083,086 |
| `COLUMN_CONFIG_UPDATED` | 031,052,056,058,060,077 |
| `CHANGE_PROPOSED` | 014,020,021,030,041,043,050,055,057,066,067,078 |
| `CHANGE_APPROVED` | 022,072,089 |
| `CHANGE_REJECTED` | 072,088 |
| `IMPORT_COMPLETED` | 077,082 |
| `DELEGATION_CHANGED` | (backend; UI affordance only) 084 |
