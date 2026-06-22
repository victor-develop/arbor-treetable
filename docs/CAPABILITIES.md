# Arbor — Capability Registry

> The single source of truth for everything Arbor can do. Mirrors the reference repo's
> `capabilities/registry.ts`. Feeds four consumers: Web `executeAction`, auto-exposed
> REST, the Tree Event stream → webhooks, and LLM agent tools (`getLLMTools()` filtered
> by `is_exposed_to_llm`). Companion to [`ARCHITECTURE.md`](./ARCHITECTURE.md) §4 and
> [`PERMISSIONS.md`](./PERMISSIONS.md).

## Capability record shape

```python
Capability(
    id,                  # stable string id, e.g. "updateCell"
    name,                # human label
    params_schema,       # JSON-schema; validated by execute_action before dispatch
    axis,                # "structure" | "column" | "meta" | "none"
    target_kind,         # CR target_kind: node-structure | cell-value | column-schema | (none)
    operation,           # add | update | move | delete | (none)
    is_exposed_to_llm,   # filters getLLMTools()
    acl_rule,            # resolver invoked to decide authority
    emits,               # Tree Event type(s) emitted on success
    handler,             # the ONLY site of this mutation's logic
)
```

Every capability runs through `execute_action(action_id, params, actor)` (ARCHITECTURE
§4.2): validate → resolve ACL → **authorized: mutate + emit event**; **not authorized:
create Change Request + emit `CHANGE_PROPOSED`**.

---

## Registry table

| id | axis | target_kind / op | LLM? | ACL rule | emits (authorized) |
|---|---|---|---|---|---|
| `getSheetSnapshot` | none | — | ✅ | reader can view sheet | *(read; no event)* |
| `addNode` | structure | node-structure / add | ✅ | `resolve_structural_approver(parent)` | `NODE_CREATED` |
| `updateCell` | column | cell-value / update | ✅ | `resolve_column_approvers(column)` | `NODE_VALUE_UPDATED` |
| `moveNode` | structure | node-structure / move | ✅ | `resolve_structural_approver(src)` **and** `(dest)` | `NODE_MOVED` |
| `deleteNode` | structure | node-structure / delete | ✅ | `resolve_structural_approver(node)` | `NODE_DELETED` |
| `addColumn` | meta | column-schema / add | ✅ | sheet `structural_owner` (schema co-design) | `COLUMN_CONFIG_UPDATED` |
| `updateColumn` | meta | column-schema / update | ✅ | `resolve_column_approvers(column)` | `COLUMN_CONFIG_UPDATED` |
| `deleteColumn` | meta | column-schema / delete | ✅ | `resolve_column_approvers(column)` | `COLUMN_CONFIG_UPDATED` |
| `suggestChange` | none | *(from payload)* | ✅ | always allowed (creates CR) | `CHANGE_PROPOSED` |
| `approveChange` | none | — | ✅ | actor == CR `resolved_approver` (or column editor) | replay event + `CHANGE_APPROVED` |
| `rejectChange` | none | — | ✅ | actor == CR `resolved_approver` (or column editor) | `CHANGE_REJECTED` |
| `withdrawChange` | none | — | ✅ | actor == CR `requester` | `CHANGE_REJECTED` *(status=withdrawn)* |
| `subscribe` | none | — | ✅ | self-subscribe, or admin for others | `SUBSCRIPTION_CHANGED` |
| `unsubscribe` | none | — | ✅ | owner of the subscription | `SUBSCRIPTION_CHANGED` |
| `acknowledge` | none | — | ✅ | recipient of the notification | *(Acknowledgement row; no Tree Event)* |
| `delegateBranch` | structure | — | ✅ | `resolve_structural_approver(branch_root)` | `DELEGATION_CHANGED` |
| `revokeDelegation` | structure | — | ✅ | `granted_by` or ancestor structural owner | `DELEGATION_CHANGED` |
| `grantColumn` | column | — | ✅ | current `column_owner` or sheet `structural_owner` | `COLUMN_CONFIG_UPDATED` |
| `internalReset` | none | — | ❌ | system/admin only | *(administrative; not on stream)* |

> `is_exposed_to_llm = False` only for `internalReset` (destructive/global). `getLLMTools()`
> returns all others.

---

## Params schemas (JSON-schema style)

```jsonc
// getSheetSnapshot
{ "type": "object", "required": ["sheet"],
  "properties": { "sheet": {"type": "string"} } }

// addNode  (Axis 1 — approver resolved against parent)
{ "type": "object", "required": ["sheet", "parent"],
  "properties": {
    "sheet":  {"type": "string"},
    "parent": {"type": ["string","null"], "description": "parent node; null = root"},
    "after":  {"type": ["string","null"], "description": "sibling to insert after (ordering)"},
    "values": {"type": "object", "description": "optional initial {column_field: value}"}
  } }

// updateCell  (Axis 2 — approver resolved against column)
{ "type": "object", "required": ["sheet", "node", "column", "value"],
  "properties": {
    "sheet":  {"type": "string"},
    "node":   {"type": "string"},
    "column": {"type": "string", "description": "Tree Column field key or name"},
    "value":  {}  // typed per column.type; arrays for select-split
  } }

// moveNode  (Axis 1 — BOTH src and dest parents resolved)
{ "type": "object", "required": ["sheet", "node", "new_parent"],
  "properties": {
    "sheet":      {"type": "string"},
    "node":       {"type": "string"},
    "new_parent": {"type": ["string","null"]},
    "after":      {"type": ["string","null"]}
  } }

// deleteNode  (Axis 1)
{ "type": "object", "required": ["sheet", "node"],
  "properties": { "sheet": {"type": "string"}, "node": {"type": "string"},
                  "cascade": {"type": "boolean", "default": true} } }

// addColumn  (meta — sheet structural_owner)
{ "type": "object", "required": ["sheet", "field", "label", "type"],
  "properties": {
    "sheet":  {"type": "string"},
    "field":  {"type": "string"},
    "label":  {"type": "string"},
    "type":   {"enum": ["text","multiline-text","number","single-select-split","multi-select-split"]},
    "options":{"type": ["object","null"]},
    "column_owner": {"type": "string"},
    "is_label": {"type": "boolean", "default": false}
  } }

// updateColumn / deleteColumn  (meta — column approvers)
{ "type": "object", "required": ["sheet", "column"],
  "properties": { "sheet": {"type":"string"}, "column": {"type":"string"},
                  "patch": {"type":"object"} } }   // deleteColumn ignores patch

// suggestChange  (always allowed — explicit CR creation)
{ "type": "object", "required": ["sheet", "target_kind", "operation", "payload"],
  "properties": {
    "sheet":       {"type": "string"},
    "target_kind": {"enum": ["node-structure","cell-value","column-schema"]},
    "operation":   {"enum": ["add","update","move","delete"]},
    "payload":     {"type": "object"}
  } }

// approveChange / rejectChange / withdrawChange
{ "type": "object", "required": ["change_request"],
  "properties": { "change_request": {"type": "string"},
                  "comment": {"type": "string"} } }

// subscribe
{ "type": "object", "required": ["scope", "target", "event_types", "delivery"],
  "properties": {
    "subscriber":  {"type": "string", "description": "defaults to actor"},
    "scope":       {"enum": ["sheet","branch","column"]},
    "target":      {"type": "string"},
    "event_types": {"type": "array", "items": {"type": "string"}},
    "delivery":    {"enum": ["in-app","email","webhook"]},
    "requires_ack":{"type": "boolean", "default": false}
  } }

// unsubscribe
{ "type": "object", "required": ["subscription"],
  "properties": { "subscription": {"type": "string"} } }

// acknowledge
{ "type": "object", "required": ["notification"],
  "properties": { "notification": {"type": "string"} } }

// delegateBranch  (Axis 1 — delegate a sub-branch)
{ "type": "object", "required": ["sheet", "branch_root", "grantee"],
  "properties": { "sheet": {"type":"string"}, "branch_root": {"type":"string"},
                  "grantee": {"type":"string"} } }

// revokeDelegation
{ "type": "object", "required": ["branch_grant"],
  "properties": { "branch_grant": {"type":"string"} } }

// grantColumn  (Axis 2 — set column owner / editors)
{ "type": "object", "required": ["sheet", "column"],
  "properties": { "sheet": {"type":"string"}, "column": {"type":"string"},
                  "column_owner": {"type":"string"},
                  "editors": {"type":"array","items":{"type":"string"}} } }

// internalReset  (NOT exposed to LLM)
{ "type": "object", "required": ["sheet"],
  "properties": { "sheet": {"type":"string"}, "confirm": {"const": true} } }
```

---

## Authorized vs. suggested behavior

For any **mutating** capability, `execute_action` produces one of two outcomes
(ARCHITECTURE §4.2):

- **Authorized** → run `handler` → emit the capability's `emits` event.
- **Not authorized** → create a Change Request (`target_kind`/`operation` from the
  capability) carrying the original `params` as `payload`, `resolved_approver` from the
  ACL rule → emit `CHANGE_PROPOSED`. On later `approveChange`, the same `handler` runs as
  the approver and emits the real event, linked via `resulting_event`.

`suggestChange` is the explicit form of the second branch (always allowed) for callers who
*intend* to suggest without first attempting a direct mutation.

---

## getLLMTools() contract

```python
def get_llm_tools():
    return [
        to_litellm_tool(cap)              # name, description, params_schema → tool def
        for cap in registry.all()
        if cap.is_exposed_to_llm
    ]
```

The agent (ARCHITECTURE §8) calls these tools, which route through the identical
`execute_action`. The agent's own User identity is subject to the same ACL, so an
unauthorized agent action becomes a Change Request — never a privileged bypass.
