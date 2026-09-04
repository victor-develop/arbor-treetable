# Arbor — standalone adapter (frappe-free)

`arbor/standalone/` is a second, framework-light adapter over the SAME pure core
(`arbor/core`): SQLAlchemy models + a `Repository` implementation, a FastAPI app
mirroring the `/api/method/arbor.*` surface (the React frontend runs unchanged),
session-cookie auth (OIDC or a dev login), and in-process background jobs
(webhook retries, process SLA sweep). The whole `tests/core` suite re-runs over
the SQL repository (`tests/standalone`), so the two adapters cannot drift.

## Run

```bash
pip install fastapi uvicorn sqlalchemy pymysql itsdangerous authlib python-multipart litellm
cd frontend && npm ci && npx vite build && cd ..
DATABASE_URL=sqlite:///arbor.db ARBOR_DEV_LOGIN=1 ARBOR_SECRET_KEY=change-me \
  uvicorn arbor.standalone.app:app --host 0.0.0.0 --port 3000
```

MySQL: set `DATABASE_URL=mysql://user:pass@host/db` (the `mysql://` scheme is
rewritten to `mysql+pymysql://`). Schema bootstraps idempotently at startup.

## Environment reference

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | sqlite (default) or MySQL DSN |
| `ARBOR_SECRET_KEY` | session-cookie signing key (random per-boot when unset) |
| `ARBOR_ADMIN_EMAILS` | comma-separated admin bootstrap (stamped `is_admin` at login) |
| `ARBOR_FRONTEND_DIST` | path to the built SPA (default `frontend/dist`) |
| `ARBOR_DEV_LOGIN=1` | password-less email login — dev/demo ONLY, and only when OIDC is absent |
| `ARBOR_OIDC_ISSUER` / `_CLIENT_ID` / `_CLIENT_SECRET` | OIDC login (discovery-capable issuer; PKCE S256 always sent; secret optional for public clients) |
| `ARBOR_OIDC_REDIRECT` | explicit callback URL — set it when TLS terminates at a proxy (the request scheme lies) |
| `ARBOR_AGENT_MODEL` / `_API_KEY` / `_API_BASE` / `_MAX_STEPS` | the in-app LLM agent (LiteLLM model ref, e.g. `anthropic/...` or `openai/...` against an OpenAI-compatible gateway) |
| `ARBOR_NO_BACKGROUND=1` | disable the in-process retry/SLA threads (tests) |

## External LLM agents

The crawlable contract is served at `GET /llm/skill.md` (and
`arbor.skill_md`). Mint a scoped token while logged in:

```
POST /api/method/arbor.issue_agent_token {"label": "my bot", "mode": "read", "sheets": ["s1"], "ttl_days": 30}
```

The response includes the plaintext token (shown once) and a `bootstrap_prompt`
to paste into any external agent. The agent sends `X-Arbor-Agent-Token` on every
call — the ONE header is both identity and scope. `arbor.list_agent_tokens` /
`arbor.revoke_agent_token` manage the fleet. See
[EXTERNAL-AGENTS](EXTERNAL-AGENTS.md).

## Test lanes

```bash
python -m pytest tests/core tests/standalone   # pure core + the SQL re-run
cd frontend && npx vitest run                  # FE units
npx playwright test --config tests/e2e-standalone/playwright.config.ts  # self-booting e2e (repo root)
```
