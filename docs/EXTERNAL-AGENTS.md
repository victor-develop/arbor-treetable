# Arbor — External LLM Agents (two-tier auth)

> **Status:** Single source of truth for the external-agent surface. Extends
> [ARCHITECTURE](ARCHITECTURE.md) §9 (API as a first-class peer) and §10 (auth
> seam). Implementers and test authors build against this.

Arbor supports **two** LLM-driven surfaces. They differ only in *who runs the
model and how it authenticates* — both reach the identical capability + ACL path
(ARCHITECTURE §4.2), so neither is more privileged than the user behind it.

| | **Internal agent chat** (AgentDock) | **External LLM agent** |
|---|---|---|
| Where the model runs | Inside Arbor (server-side Re-Act, ARCHITECTURE §8) | Outside Arbor — ChatGPT / Claude / the user's own agent |
| Entry point | `POST /api/method/arbor.agent.chat` | `POST /api/method/arbor.execute_action` (+ named shims) |
| Auth | The live web session (SSO/JWT), verbatim | **Frappe API key** + an optional **Arbor Agent Token** down-scope |
| How it learns the API | Compiled in (`registry.get_llm_tools()`) | Crawls the generated **`skill.md`** contract |
| Code path to the executor | `core.executor.execute_action` directly | adapter `api._dispatch` → `core.executor` |

The design goal is that you can paste **one bootstrap prompt** into any external
agent and it can operate your data safely, without ever handling a password,
cookie, or browser JWT.

---

## 1. Why API-key + a scope overlay (not a new bearer scheme)

The architecture already resolved this (auth/provider.py, RESOLVED OPEN QUESTION
4): *"an external system is just a normal Frappe User + API key bound by [the
two-axis] ACL … API-key auth is Frappe-native."* So we do **not** invent a JWT.

- **Identity** = a Frappe API key (`Authorization: token <key>:<secret>`). This
  resolves `frappe.session.user`; from there the full two-axis ACL +
  mutate-or-suggest executor runs, exactly as for the web app.
- **Down-scope** = an optional **Arbor Agent Token** (`X-Arbor-Agent-Token`
  header). It is a *ceiling*, never a widening: it can only *narrow* what its own
  user could already do.

Browser JWT / session cookies are for the first-party web app and the internal
chat — an external agent must not be handed them, and `skill.md` says so.

---

## 2. `skill.md` — the crawlable contract

- **Endpoint:** `GET /api/method/arbor.skill_md` — **public** (`allow_guest`),
  `text/markdown`.
- **Generated** from the capability registry (`core.skill.render_skill_md`), so
  it can never drift from the live API. Only `is_exposed_to_llm` capabilities
  appear — the external agent sees exactly the internal agent's tool set
  (`internalReset`, role admin, and impersonation stay hidden).
- **Public on purpose:** it describes the API *shape* + capability catalog only —
  never any tenant data — so the crawl never needs a credential. The secret is
  spent only on real API calls.

It documents: the request envelope, the two-tier auth headers, the
mutate-or-suggest governance rule, the discovery flow (`list_sheets` →
`getSheetDefinition` → explore reads → `execute_action`), and every capability's
JSON params schema.

---

## 3. The Arbor Agent Token

Persisted as the `Arbor Agent Token` DocType — but only its **keyed hash** is
stored (`core.agent_scope.hash_token`, HMAC-SHA256 keyed by the site secret). The
plaintext secret (`arbor_pat_…`) is returned **once** at issue and is never
recoverable. A leaked DB cannot forge or reverse a token.

### 3.1 Scope dimensions

An `AgentScope` (`core.agent_scope.AgentScope`) has two independent, intentionally
coarse dimensions:

| Dimension | Values | Meaning |
|---|---|---|
| `mode` | `read` \| `write` | `read` → only the side-effect-free explore/snapshot reads (`READ_ONLY_CAPABILITY_IDS`); `write` → every LLM-exposed capability (still floored by ACL). |
| `sheets` | `null` \| `[sheet id, …]` | `null` = every sheet the user can reach; a list restricts to those sheets. |

The read-only set is an **explicit allowlist**, not inferred — because inference
is unsafe: `acknowledge` and the impersonation control caps *look* like reads
(no handler, no emitted event) yet change state. A drift-guard test
(`tests/core/test_agent_scope.py`) fails if a new read-shaped capability is added
without being classified.

### 3.2 The gate (`authorize_scope`)

Applied at **every** capability entrypoint — `api._dispatch` **and**
`api.get_sheet_snapshot` (the latter reaches the executor directly and would
otherwise sidestep the sheet restriction) — **before** the executor. Order and
rules:

1. Unknown capability → error (404).
2. Not `is_exposed_to_llm` (e.g. `internalReset`) → **403**, regardless of mode.
3. `mode == read` and the capability mutates → **403**.
4. `sheets` restricted and `params.sheet` not in the set → **403**.
5. `sheets` restricted and the capability has **no** `sheet` param (account-level
   ops: `createSheet`, roles, `subscribe`-by-notification, `approve`-by-CR) →
   **403** (conservative; a sheet-bound token has no account-level business).

A scope violation is a **hard 403** — it *never* degrades to a Change Request
(unlike an unauthorized mutation, which does). Passing the gate is necessary but
not sufficient: the two-axis ACL still runs underneath.

### 3.3 Defense in depth

Because the token resolves to a Frappe user and the ACL still runs:

- A leaked **read-write** token still **cannot write a column the user doesn't
  own** — that write degrades to a Change Request routed to the owner.
- A token only ever adds scope to **its own user**: `A`'s API key + `B`'s token →
  **403** (`doc.user != frappe.session.user`).
- `revoke` / expiry is a hard **401** at the auth layer (distinct from scope 403s).

---

## 4. Endpoints

| Method / Path | Auth | Purpose |
|---|---|---|
| `GET /api/method/arbor.skill_md` | none (public) | The generated contract (markdown). |
| `POST /api/method/arbor.issue_agent_token` `{label?, mode, sheets?, ttl_days?}` | session / API key | Mint a token for the current user; returns the plaintext **once** + a ready-to-paste `bootstrap_prompt`. |
| `POST /api/method/arbor.revoke_agent_token` `{token_id}` | session / API key | Revoke a token (issuer or admin). Immediate kill switch. |
| `GET /api/method/arbor.list_agent_tokens` | session / API key | The current user's tokens (metadata only — never the hash). |
| `POST /api/method/arbor.execute_action` `{action_id, params}` | API key **+** `X-Arbor-Agent-Token` | Any capability, scoped by the token. |

### 4.1 Bootstrap prompt (what the user pastes)

`issue_agent_token` returns something like:

```
You can operate my Arbor data through its HTTP API. First read the contract:
  https://<host>/api/method/arbor.skill_md
API base: https://<host>/api/method/arbor.
On every request send header  X-Arbor-Agent-Token: arbor_pat_…
plus the Frappe API key I gave you as  Authorization: token <key>:<secret>
You may read and write; a write you aren't authorized for becomes a Change Request (not an error).
Begin by calling arbor.list_sheets, then follow the discovery flow in skill.md.
```

---

## 5. Verified behavior (live smoke, `arbor.test`)

Exercised end-to-end over HTTP as an external agent (API key + scoped tokens);
12/12 including every scope branch:

| Check | Result |
|---|---|
| `skill_md` crawl (public) | 200, markdown, 36 caps (8 read / 28 write); `internalReset` hidden |
| API key, **no** token header → read | 200 (gate no-ops; first-party path unaffected) |
| write token → owner write | 200 `executed` |
| **read token → mutating cap** | **403** (mode gate) |
| **sheet-scoped token → out-of-scope sheet** | **403** (sheet gate) |
| **sheet-scoped token → `createSheet`** | **403** (account-level denied) |
| **other user's token + this API key** | **403** (token↔user binding) |
| **revoked token reused** | **401** |

---

## 6. Code map

| Concern | Module |
|---|---|
| Contract generator (pure) | `arbor/core/skill.py` |
| Scope model + gate + token hashing (pure) | `arbor/core/agent_scope.py` |
| Token storage | `arbor/arbor/doctype/arbor_agent_token/` |
| HTTP endpoints + gate wiring | `arbor/arbor/api.py` (`skill_md`, `issue/revoke/list_agent_tokens`, `_agent_scope_from_request`, `_enforce_agent_scope`) |
| Whitelist aliases | `arbor/hooks.py` (`override_whitelisted_methods`) |
| Tests | `tests/core/test_skill.py`, `tests/core/test_agent_scope.py`, `tests/api/test_agent_token_bench.py` |

> **Non-request contexts:** the gate reads `frappe.local.request` (not
> `frappe.request`, a LocalProxy that is never `None`) and no-ops when unbound, so
> dispatch from background jobs / the scheduler / bench / the internal agent lane
> never crashes on the header read. Guarded by `test_agent_token_bench.py`.
