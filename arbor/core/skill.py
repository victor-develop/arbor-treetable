"""``skill.md`` generator — the crawlable contract for external LLM agents.

An external agent (ChatGPT / Claude / the user's own) is bootstrapped with a
prompt pointing at ``/llm/skill.md``. That document is this module's output: a
machine-readable description of Arbor's API — the request envelope, the auth
model, the governance rules, and the FULL capability catalog — rendered from the
SAME :mod:`arbor.core.registry` the internal agent reads via ``get_llm_tools()``.

Rendering from the registry (not a hand-maintained doc) is the whole point:
internal and external agents can never diverge on what Arbor can do, and the
catalog can never go stale. Only ``is_exposed_to_llm`` capabilities appear — the
external agent sees exactly the internal agent's tool set (internalReset, role
admin and impersonation stay hidden).

PURE: zero frappe imports, deterministic output, unit-tested bench-free. The
adapter serves the string at a public route.
"""

from __future__ import annotations

import json
from typing import Optional

from .agent_scope import TOKEN_PREFIX, is_read_capability
from .registry import all_capabilities
from .types import Capability

#: Default public base for the worked examples when the caller passes none.
_DEFAULT_BASE_URL = "https://<your-arbor-host>"

# Capability groupings for the catalog — declaration ids mapped to a heading. A
# cap not matched by any group falls into "Other". Order here is the doc order.
_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Read (navigate & inspect)",
        (
            "getSheetSnapshot",
            "getSheetOverview",
            "getSheetDefinition",
            "listChildren",
            "getSubtree",
            "getNode",
            "searchNodes",
            "getCells",
        ),
    ),
    (
        "Rows & cells",
        ("addNode", "updateCell", "moveNode", "deleteNode"),
    ),
    (
        "Sheets & columns",
        ("createSheet", "addColumn", "updateColumn", "deleteColumn"),
    ),
    (
        "Change requests (the mutate-or-suggest path)",
        (
            "suggestChange",
            "suggestChanges",
            "approveChange",
            "rejectChange",
            "withdrawChange",
        ),
    ),
    (
        "Collaboration",
        (
            "addComment",
            "resolveComment",
            "deleteComment",
            "subscribe",
            "unsubscribe",
            "acknowledge",
        ),
    ),
    (
        "Governance",
        ("delegateBranch", "revokeDelegation", "grantColumn"),
    ),
    (
        "Process / SLA",
        ("defineProcess", "enableProcess", "disableProcess", "startProcessRun"),
    ),
    (
        "Roles",
        ("applyForRole", "withdrawRoleApplication"),
    ),
)


def _required(cap: Capability) -> list[str]:
    req = cap.params_schema.get("required", []) if isinstance(cap.params_schema, dict) else []
    return list(req)


def _render_capability(cap: Capability) -> str:
    """One capability as a markdown block: id, kind, ACL, and params schema."""
    kind = "read" if is_read_capability(cap.id) else "write"
    lines = [
        f"#### `{cap.id}` — {cap.name}",
        "",
        f"- kind: **{kind}**",
        f"- authorization: {cap.acl_rule}",
    ]
    req = _required(cap)
    if req:
        lines.append(f"- required params: {', '.join(f'`{r}`' for r in req)}")
    props = cap.params_schema.get("properties") if isinstance(cap.params_schema, dict) else None
    if props:
        lines.append("- params schema:")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(cap.params_schema, indent=2, sort_keys=True))
        lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _catalog(caps: dict[str, Capability]) -> str:
    """Render every exposed capability, grouped; anything ungrouped → 'Other'."""
    out: list[str] = []
    seen: set[str] = set()
    for heading, ids in _GROUPS:
        block = [cap_id for cap_id in ids if cap_id in caps]
        if not block:
            continue
        out.append(f"### {heading}")
        out.append("")
        for cap_id in block:
            out.append(_render_capability(caps[cap_id]))
            seen.add(cap_id)
    leftover = [cid for cid in caps if cid not in seen]
    if leftover:
        out.append("### Other")
        out.append("")
        for cap_id in leftover:
            out.append(_render_capability(caps[cap_id]))
    return "\n".join(out)


def render_skill_md(base_url: Optional[str] = None) -> str:
    """Render the full ``skill.md`` contract from the registry.

    ``base_url`` (the deployment origin, no trailing slash) is woven into the
    endpoint + examples; it defaults to a placeholder so the doc is renderable
    bench-free and in tests.
    """
    base = (base_url or _DEFAULT_BASE_URL).rstrip("/")
    exposed = {c.id: c for c in all_capabilities() if c.is_exposed_to_llm}
    read_ids = [cid for cid in exposed if is_read_capability(cid)]

    return f"""# Arbor — LLM agent skill

Arbor is a governed, API-first tree-table service. Everything you can do is a
**capability**: a named action with a JSON params schema, executed through ONE
endpoint and governed by ONE authorization model. This document is generated
from Arbor's capability registry, so it is always in sync with the live API.

There are {len(exposed)} capabilities available to you ({len(read_ids)} read,
{len(exposed) - len(read_ids)} write).

## Endpoint & envelope

Every action goes through one generic dispatch:

```
POST {base}/api/method/arbor.execute_action
Content-Type: application/json

{{"action_id": "<capabilityId>", "params": {{ ... }}}}
```

Each capability also has a named alias (`{base}/api/method/arbor.<verb>`, e.g.
`arbor.update_cell`) if you prefer, but `execute_action` covers all of them.

The response is a stable envelope:

```json
{{"kind": "executed | suggested | read", "data": {{}}, "change_request": "<id?>"}}
```

- `kind: "executed"` — the change was applied (you had authority).
- `kind: "suggested"` — you did NOT have authority, so Arbor turned your write
  into a **Change Request** routed to the owner. This is NOT an error: it is the
  governed happy path. `change_request` carries the id to track.
- `kind: "read"` — a read result (or a `VERSION_CONFLICT` on an optimistic write).

## Authentication

Authenticate with the credentials from your bootstrap prompt. Two headers:

1. Frappe API key (identifies the acting user):
   `Authorization: token <api_key>:<api_secret>`
2. Arbor Agent Token (optional down-scope — read-only vs read-write, and which
   sheets): `X-Arbor-Agent-Token: {TOKEN_PREFIX}...`

Do NOT paste passwords, cookies, or a browser JWT — those are for the first-party
web app, not for you. If a call returns 401, the API key is missing/expired; 403
means either the Agent Token's scope forbids it or (for a control action) you
lack authority.

## Governance you must understand

- **Two-axis ACL.** Authority is split: *structural* (who owns a branch/row) and
  *column* (who owns a column's values). You may have one, both, or neither on a
  given cell.
- **Mutate-or-suggest.** If you try a write you're not authorized for, it does
  not fail — it becomes a Change Request (`kind: "suggested"`). So prefer just
  attempting the write; fall back to `suggestChange` only when you want to
  propose explicitly.
- **Read ACL.** Reads only ever return columns you may see; forbidden columns
  (and their cells) simply do not appear.

## How to start (discovery flow)

1. `arbor.list_sheets` — list the sheets you can reach.
2. `getSheetDefinition {{ "sheet": "<id>" }}` — the cheap schema/governance read:
   columns, owners, process. NO row data. Use this to learn column ids/types.
3. `getSheetOverview` / `listChildren` / `getSubtree` / `getCells` — page through
   the actual tree and cells (never fetch a whole large sheet at once).
4. `execute_action` with a write capability (e.g. `updateCell`) — remembering the
   mutate-or-suggest rule above.

## Worked example — read a definition, then update a cell

```
POST {base}/api/method/arbor.execute_action
{{"action_id": "getSheetDefinition", "params": {{"sheet": "acme"}}}}

POST {base}/api/method/arbor.execute_action
{{"action_id": "updateCell",
  "params": {{"sheet": "acme", "node": "n-42", "column": "col:status", "value": "done"}}}}
# -> {{"kind": "executed", ...}}  if you own col:status
# -> {{"kind": "suggested", "change_request": "CR-…"}}  if you don't (routed to the owner)
```

## Capability catalog

{_catalog(exposed)}
"""
