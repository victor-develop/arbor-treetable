# Arbor — Data Model (DocType Specification)

> Canonical DocType definitions. Companion to
> [`ARCHITECTURE.md`](./ARCHITECTURE.md). Schema is **meta-model driven**: a sheet's
> columns are *data* (Tree Column rows), not Frappe fields.

Conventions:
- All DocTypes are **app `arbor`** (open-source core). `arbor_sso_overlay` adds no
  DocTypes.
- `Link` = Frappe Link field (FK). `JSON` = Frappe `JSON`/`Code` field. `Select` = Frappe
  Select with fixed options. `Child Table` = embedded grid (DocType with `istable=1`).
- Every governed mutation flows through `execute_action`; **raw REST writes to governed
  DocTypes are disallowed** (Frappe permissions deny direct write; reads allowed).

---

## 1. Tree Sheet

A table instance (the root governance object).

| Field | Type | Notes |
|---|---|---|
| `title` | Data | required |
| `description` | Text | optional |
| `structural_owner` | Link → User | root branch owner ("A"); Axis-1 fallback approver |
| `status` | Select | `draft` \| `active` \| `archived` |
| `settings` | JSON | policy flags, e.g. `{ "owners_must_use_change_requests": true }` |

- `structural_owner` is the terminal approver of the Axis-1 resolution walk.
- `settings.owners_must_use_change_requests` (bool) forces even authorized owners to route
  through Change Requests (self-approver) for an audit trail.

---

## 2. Tree Column

The schema definition for a sheet. **One row per column.** Mutable at runtime.

| Field | Type | Notes |
|---|---|---|
| `sheet` | Link → Tree Sheet | required |
| `field` | Data | internal key (stable, unique within sheet), e.g. `name`, `budget` |
| `label` | Data | display label |
| `type` | Select | `text` \| `multiline-text` \| `number` \| `single-select-split` \| `multi-select-split` |
| `options` | JSON | for select types: option list / split config; null otherwise |
| `width` | Int | UI column width (px) |
| `editable` | Check | whether the column accepts value edits at all |
| `column_owner` | Link → User | **Axis-2 owner** (personas B, C, …) |
| `editors` | Child Table → Tree Column Editor | extra users who may approve/edit this column |
| `is_label` | Check | exactly one column per sheet has `is_label=1` → supplies node display label |

- Unique constraint: `(sheet, field)`.
- `column_owner` + `editors[]` are the **entire** Axis-2 authority for the column — there
  is no separate Column Grant DocType (decision locked in ARCHITECTURE §2.2).
- Adding/removing a Tree Column row emits `COLUMN_CONFIG_UPDATED`.

### 2.1 Tree Column Editor (Child Table)

| Field | Type | Notes |
|---|---|---|
| `user` | Link → User | an additional owner-equivalent editor/approver for the parent column |

---

## 3. Tree Node

The tree structure. **Frappe NestedSet.**

| Field | Type | Notes |
|---|---|---|
| `sheet` | Link → Tree Sheet | required |
| `parent_tree_node` | Link → Tree Node | NestedSet parent; **null = root** |
| `lft` | Int | NestedSet left bound (managed by Frappe) |
| `rgt` | Int | NestedSet right bound (managed by Frappe) |
| `is_group` | Check | NestedSet group flag (may have children) |
| `idx` | Int | ordering among siblings |

- DocType sets `nsm_parent_field = "parent_tree_node"` (NestedSet mixin).
- **No label/name content field** — the human-visible label is the Tree Node Value for the
  `is_label` column (ARCHITECTURE §2.3).
- Ancestor query (Axis-1 walk): `WHERE sheet=? AND lft <= node.lft AND rgt >= node.rgt
  ORDER BY lft DESC` → nearest ancestor first.
- Descendant query (branch subscription match): `WHERE sheet=? AND lft > root.lft
  AND rgt < root.rgt`.

---

## 4. Tree Node Value

A single **cell**, keyed by `(node, column)`. **Its own DocType** (not a JSON blob) so
every cell has independent audit trail, version, and field-level permission.

| Field | Type | Notes |
|---|---|---|
| `sheet` | Link → Tree Sheet | denormalized for fast sheet-scoped queries |
| `node` | Link → Tree Node | required |
| `column` | Link → Tree Column | required |
| `value` | JSON | typed per `column.type`; selects store arrays |
| `version` | Int | incremented on each value update |

- Unique constraint: `(node, column)`.
- Frappe's built-in document versioning + the `version` counter give per-cell history.
- Updated only via `updateCell` (Axis 2). Emits `NODE_VALUE_UPDATED` with payload
  `{ node, column, old_value, new_value, version }`.

> **Decision (locked):** separate DocType over JSON blob. A blob would force one
> permission + one version for the entire row, breaking Axis-2 per-column ownership and
> per-cell history. See ARCHITECTURE §3.3.

---

## 5. Branch Grant

Delegable **structural** ownership of a sub-branch (Axis 1).

| Field | Type | Notes |
|---|---|---|
| `sheet` | Link → Tree Sheet | required |
| `branch_root` | Link → Tree Node | subtree root being delegated |
| `grantee` | Link → User | becomes structural approver within the subtree (persona D) |
| `scope` | Select | `structure` (only value today; reserved for future scopes) |
| `granted_by` | Link → User | delegating owner (must hold authority over `branch_root`) |
| `active` | Check | revocable; resolution walk considers only `active=1` |

- Created by `delegateBranch`; deactivated by `revokeDelegation`. Both emit
  `DELEGATION_CHANGED`.
- Resolution: nearest active grant on the ancestor chain wins (ARCHITECTURE §2.1).

---

## 6. Change Request

A deferred capability call (the suggest/approve unit).

| Field | Type | Notes |
|---|---|---|
| `sheet` | Link → Tree Sheet | required |
| `target_kind` | Select | `node-structure` \| `cell-value` \| `column-schema` |
| `operation` | Select | `add` \| `update` \| `move` \| `delete` |
| `payload` | JSON | the exact capability params to replay on approval |
| `requester` | Link → User | who suggested (human or agent user) |
| `resolved_approver` | Link → User | computed via ACL resolver; re-checked at decision time |
| `status` | Select | `proposed` \| `approved` \| `rejected` \| `withdrawn` |
| `decided_by` | Link → User | approver/rejecter |
| `decided_at` | Datetime | |
| `resulting_event` | Link → Tree Event | the mutation event produced on approval |

- On `approveChange`: replay `cap.handler(payload, actor=resolved_approver)`, link
  `resulting_event`, emit `CHANGE_APPROVED`. State machine in ARCHITECTURE §5.

---

## 7. Subscription

Who watches what.

| Field | Type | Notes |
|---|---|---|
| `subscriber` | Data/Link | User name OR external system identifier |
| `subscriber_kind` | Select | `user` \| `external` |
| `scope` | Select | `sheet` \| `branch` \| `column` |
| `target` | Dynamic Link | Tree Sheet \| Tree Node (branch root) \| Tree Column |
| `event_types` | JSON | subset of event types, e.g. `["CHANGE_PROPOSED","CHANGE_APPROVED"]` (proposed \| approved \| rejected and others) |
| `delivery` | Select | `in-app` \| `email` \| `webhook` |
| `requires_ack` | Check | if set, recipient must acknowledge (powers accountability report) |

- Created/removed by `subscribe` / `unsubscribe`; emits `SUBSCRIPTION_CHANGED`.
- Branch-scope matching uses NestedSet descendant range on `target`.

---

## 8. Notification

One row per `(tree_event, recipient)`. Produced by the notification dispatcher off the
Tree Event stream — never by capabilities directly.

| Field | Type | Notes |
|---|---|---|
| `tree_event` | Link → Tree Event | source event |
| `change_request` | Link → Change Request | nullable; set when event relates to a CR |
| `recipient` | Link → User | resolved subscriber |
| `channel` | Select | `in-app` \| `email` \| `webhook` |
| `delivered_at` | Datetime | set on delivery |
| `requires_ack` | Check | copied from subscription |

---

## 9. Acknowledgement

One row per `(notification, user)`.

| Field | Type | Notes |
|---|---|---|
| `notification` | Link → Notification | required |
| `user` | Link → User | acknowledger |
| `acked_at` | Datetime | set by `acknowledge` capability |

- Accountability ("N notified / M acked") =
  `count(Notification where requires_ack=1)` vs `count(Acknowledgement)` for an event/CR.

---

## 10. Webhook Endpoint

External system subscription target.

| Field | Type | Notes |
|---|---|---|
| `url` | Data | delivery URL |
| `secret` | Password | HMAC-SHA256 signing secret |
| `event_types` | JSON | subscribed Tree Event types |
| `scope` | Select | `sheet` \| `branch` \| `column` |
| `target` | Dynamic Link | scope target |
| `active` | Check | |

---

## 11. Webhook Delivery

One delivery-attempt log row.

| Field | Type | Notes |
|---|---|---|
| `endpoint` | Link → Webhook Endpoint | required |
| `tree_event` | Link → Tree Event | payload source |
| `status` | Select | `pending` \| `delivered` \| `failed` \| `exhausted` |
| `attempts` | Int | attempt count |
| `last_response` | Text | last HTTP status/body excerpt |
| `next_retry_at` | Datetime | backoff schedule |
| `signature` | Data | `sha256=<hmac>` sent in `X-Arbor-Signature` |

- Retry backoff (default 6 attempts ~24h): 0s, 30s, 5m, 30m, 2h, 12h. `delivered` on 2xx;
  else reschedule until `exhausted`. `X-Arbor-Event-Id = tree_event` for idempotency.

---

## 12. Tree Event

The **append-only** event-sourced log. Every mutation emits exactly one.

| Field | Type | Notes |
|---|---|---|
| `sheet` | Link → Tree Sheet | required |
| `type` | Select | see event types below |
| `payload` | JSON | event-specific data |
| `actor_type` | Select | `human` \| `agent` \| `system` |
| `actor` | Link → User | acting identity (agent uses its own User) |
| `change_request` | Link → Change Request | nullable; set for CR-related events |

**Event types (closed set):**

```
NODE_CREATED · NODE_DELETED · NODE_MOVED · NODE_VALUE_UPDATED ·
COLUMN_CONFIG_UPDATED · CHANGE_PROPOSED · CHANGE_APPROVED · CHANGE_REJECTED ·
SUBSCRIPTION_CHANGED · DELEGATION_CHANGED · IMPORT_COMPLETED
```

- Written **only** by `arbor.events.emitter.emit_event`. Notifications and webhooks are
  derived consumers (ARCHITECTURE §6, §7).
- Append-only: no update/delete capability is exposed; `internalReset` (not exposed to
  LLM) is the only administrative purge.

---

## 13. Uniqueness & integrity constraints (summary)

| Constraint | Enforced on |
|---|---|
| `(sheet, field)` unique | Tree Column |
| exactly one `is_label=1` per sheet | Tree Column |
| `(node, column)` unique | Tree Node Value |
| NestedSet `lft/rgt` consistency | Tree Node |
| `(notification, user)` unique | Acknowledgement |
| Tree Event append-only | Tree Event |
| governed DocTypes: write only via `execute_action` | all mutating DocTypes |
