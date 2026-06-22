# Running & testing Arbor

Arbor is **ports & adapters**. The pure domain core (`arbor/core`) is bench-free;
the Frappe adapter, dispatchers, agent, auth, and surfaces are wired on top. The
test suite is split into four tiers by what infrastructure they need.

| Tier | Needs | Runs here? | Command |
|---|---|---|---|
| (a) Pure core pytest | nothing (plain Python) | ✅ yes | `make test-core` |
| (b) Frontend Vitest | Node + `npm install` | ✅ yes | `make test-frontend` |
| (c) Frappe integration pytest | a Frappe bench + site | ⛔ needs bench | `bench --site <site> run-tests --module …` |
| (d) Playwright e2e | a running app (backend + Vite) | ⛔ needs app | `cd frontend && npm run test:e2e` |

> Module-path note: on a bench the app package root is the repo-level `arbor/`
> package, so the pure core is `arbor.core.*` and the Frappe adapter modules are
> `arbor.arbor.*`. The documented short public paths (`arbor.execute_action`,
> `arbor.agent.chat`, `arbor.auth.*`) are exposed via
> `override_whitelisted_methods` in `arbor/hooks.py` plus the thin re-export
> shims `arbor/api.py`, `arbor/agent/chat.py`, `arbor/adapter/*`.

---

## (a) Bench-free core pytest — runs here

The pure-domain suite needs no Frappe and no third-party runtime deps (only
`pytest`).

```bash
# from the repo root
python3 -m pytest tests/core -m core
# or
make test-core
```

### Wider bench-free suite

Beyond `tests/core`, several other lanes are fully bench-free (dispatch,
webhooks, the cross-surface parity harness half, agent orchestration, auth seam,
seed parity, doctype-schema checks). Bench-marked tests inside these dirs
auto-skip when `frappe` is absent (each lane's `conftest.py`), so this is safe on
a plain checkout:

```bash
python3 -m pytest \
  tests/core tests/dispatch tests/webhooks tests/api tests/agent \
  tests/auth tests/adapter tests/doctype
```

Expected: all pass, with the `@pytest.mark.bench` REST/agent/adapter-integration
tests reported as skipped (frappe not importable).

All pytest markers (`core`, `dispatch`, `webhooks`, `agent`, `auth`, `adapter`,
`parity`, `bench`) are registered in `pyproject.toml`.

---

## (b) Frontend Vitest — runs here

```bash
make install-frontend          # cd frontend && npm install
make test-frontend             # cd frontend && npm test  (vitest run)
cd frontend && npm run lint    # tsc --noEmit type-check
cd frontend && npm run build   # tsc && vite build (verifies Tailwind/PostCSS)
```

The frontend is React 19 + Vite + Tailwind. Vitest only collects
`src/**/*.test.{ts,tsx}`, so the repo-root Playwright specs are never picked up
by the component suite.

---

## (c) Frappe integration pytest — needs a bench

These bind to the real adapter (`FrappeRepository` / `FrappeEventSink`), the
whitelisted REST funnel (`arbor.api.*`), and the dispatchers against the
canonical seed. They are `@pytest.mark.bench` and require an importable `frappe`
+ a site.

```bash
# Preferred: Frappe's own runner (transactional rollback + site context)
tests/backend/run_backend_tests.sh <site>

# Or from inside a bench-activated virtualenv (frappe importable, site set):
PYTEST=1 tests/backend/run_backend_tests.sh
# equivalently:
python3 -m pytest tests/backend -m bench
python3 -m pytest tests/adapter tests/api tests/agent -m bench
```

The REST parity suite (`tests/api/test_rest_parity_bench.py`), the agent chat
endpoint (`tests/agent/test_chat_endpoint_bench.py`), and the adapter API
integration (`tests/adapter/test_api_integration.py`) are also bench-tier.

---

## (d) Playwright e2e — needs a running app

End-to-end specs drive a real browser against a running stack. Install the
browser binary once, bring up both servers + the canonical seed, then run:

```bash
cd frontend && npm install
npx playwright install chromium
# with backend (:8000) + Vite (:5173) up and sheet S seeded:
cd frontend && npm run test:e2e
# equivalently from repo root:
npx playwright test --config tests/e2e/playwright.config.ts
```

Persona auth: seed a Frappe API key per persona and inject via
`ARBOR_E2E_<PERSONA>_KEY` (see `tests/e2e/README.md`). Override base URL/sheet
with `ARBOR_E2E_BASE_URL` / `ARBOR_E2E_SHEET`.

---

## Installing & running the Frappe app

```bash
# on an existing bench
bench get-app arbor /path/to/this/repo      # or a git remote
bench --site <site> install-app arbor
bench --site <site> migrate                 # sync the 13 DocTypes
bench start                                  # serves the API at :8000
```

The app wires (in `arbor/hooks.py`):

- `doc_events["Tree Event"]["after_insert"]` → the ONE dispatcher entrypoint that
  feeds BOTH the notification and webhook dispatchers (DRY).
- `scheduler_events["cron"]["* * * * *"]` → the webhook retry runner on the core
  backoff schedule (0s, 30s, 5m, 30m, 2h, 12h).
- `override_whitelisted_methods` → the documented `arbor.*` short API paths +
  `arbor.agent.chat` + `arbor.accountability` + `arbor.auth.*`.

Governed DocTypes are read-only over raw REST (writes go through the capability
methods); Tree Event is append-only.

Seed the canonical sheet for demos/manual testing (in `bench --site <site>
console`):

```python
from arbor.adapter.seed import seed_canonical_sheet
seed_canonical_sheet()
```

### Agent + webhook deploy deps

The adapter lane needs `requests`, `litellm`, `PyJWT`, `cryptography` (declared
as the `app` optional-dependency group in `pyproject.toml`; `litellm`/`PyJWT` are
imported lazily so the bench-free core never pulls them):

```bash
pip install -e ".[app]"     # inside the bench venv
```

Configure the agent LLM via the agent config (model / provider; see
`arbor/arbor/agent/config.py`).

---

## Running the React app

```bash
cd frontend
npm install
npm run dev        # Vite dev server at :5173, proxies /api -> :8000
npm run build      # production bundle in frontend/dist
```

The shell is snapshot-driven: edit-vs-suggest affordances come from the snapshot
ACL hints returned by `arbor.get_sheet_snapshot`. The agent sidebar streams
NDJSON Re-Act frames from `arbor.agent.chat`.

---

## Enabling / disabling the employee SSO module

`arbor_sso_overlay/` is a **separate app**, injected only in a private
deployment. Nothing in `arbor.core` or the open-source `arbor` app imports it.

**Enable:**

```bash
bench --site <site> install-app arbor_sso_overlay
```

Then in `site_config.json`:

```json
{
  "arbor.auth.provider_class": "arbor_sso_overlay.provider.EmployeeSSOProvider",
  "arbor_sso_overlay": {
    "client_id": "<employee-sso-client-id>",
    "jwks_uri": "https://<idp>/jwks",
    "issuer": "https://<idp>/"
  }
}
```

The frontend overlay build additionally wraps the root in
`<AuthProviderEmployee config={{clientId, forceLogin:true}}>` and sets
`Authorization: await getAuthorization()` on every call (see
`arbor_sso_overlay/frontend/authProviderEmployee.tsx`). Add
`@your-org/employee-sso-sdk` to the **private deployment's** frontend
build only — never to the open-source `frontend/package.json`.

To verify every request enforces the employee JWT, opt into the optional
`before_request` hook documented in `arbor_sso_overlay/arbor_sso_overlay/hooks.py`.

**Disable:** remove the `arbor.auth.provider_class` key (the resolver falls back
to the open-source `LocalAuthProvider`) and/or uninstall the app. The
open-source build never depends on it.
