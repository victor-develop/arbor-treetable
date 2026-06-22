# Arbor

> Governed, API-first, agent-native tree tables.

A collaborative tree-table SaaS that solves fine-grained governance over
hierarchical data: schema co-design, **two-axis ownership** (delegable branch
structure + column fields), suggest/approve **change requests**, subscriber
notifications with an acknowledgement ledger, **webhook** events, an API that is
a first-class peer to the web UI, and a server-side **Re-Act AI agent**.

Built on the **Frappe Framework** with a standalone **React** frontend.
Engineering taste follows the reference repo
[`github.com/victor-develop/React-TreeTable-Demo`](https://github.com/victor-develop/React-TreeTable-Demo):
one event-sourced capability registry, one centralized `executeAction`, schema
as data.

The canonical, locked specification lives in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
[`docs/DATA-MODEL.md`](docs/DATA-MODEL.md),
[`docs/CAPABILITIES.md`](docs/CAPABILITIES.md),
[`docs/PERMISSIONS.md`](docs/PERMISSIONS.md), with resolved open questions in
[`docs/DECISIONS.md`](docs/DECISIONS.md). The 506-case test catalog is in
[`tests/TEST-PLAN.md`](tests/TEST-PLAN.md).

---

## Architecture: ports & adapters

```
arbor/core/            PURE domain core — ZERO frappe imports. Bench-free.
                       Registry · ACL resolver · execute_action · CR state
                       machine · snapshot serializer · HMAC · backoff · agent
                       Re-Act loop · the Repository / EventSink / LLMProvider
                       PORTS. Unit-testable with plain pytest.

arbor/ (Frappe app)    ADAPTER — DocTypes; a FrappeRepository + FrappeEventSink
                       implementing the core ports; one whitelisted method per
                       capability (all funnelling into core.execute_action); the
                       notification + webhook dispatchers; the LiteLLM provider;
                       agent.chat; the AuthProvider (Local / OIDC).

frontend/              Standalone Vite + React thin shell over the capability API
                       (executeAction, getSheetSnapshot, agent.chat). Snapshot-
                       driven; edit-vs-suggest affordances come from snapshot ACL
                       hints. Vitest for component tests.

arbor_sso_overlay/     SEPARATE app implementing AuthProvider via an employee
                       SSO SDK. NOT imported by core; injected only in a
                       private deployment.
```

**DRY mandate.** Exactly ONE registry, ONE `execute_action`, ONE ACL resolver,
ONE event emitter, ONE snapshot serializer, ONE notification dispatcher, ONE
webhook dispatcher. No surface re-implements mutation or ACL logic.

---

## Open-source vs. SSO isolation

**Everything except `arbor_sso_overlay/` is open-source-ready.** No SSO-overlay
SDK import appears anywhere in the core or the Frappe app. Authentication is a
pluggable `AuthProvider` interface (ARCHITECTURE §10): the core ships
`LocalAuthProvider` and a generic `OIDCAuthProvider`; the employee SSO
integration is an isolated app selected via site config
(`arbor.auth.provider_class`). The pure core's test suites mock `AuthProvider`.

---

## Repository layout

```
arbor/core/            Pure domain core (bench-free). arbor.core.*
arbor/arbor/           Frappe adapter modules. arbor.arbor.* on a bench:
  ├─ doctype/          the 13 DocTypes (+2 child tables), module = "Arbor"
  ├─ adapter/          FrappeRepository · FrappeEventSink · canonical seed
  ├─ dispatch/         notification + webhook dispatchers (frappe_dispatch wiring)
  ├─ agent/            Re-Act loop · LiteLLM provider · agent.chat endpoint
  └─ api.py            ONE whitelisted method per capability (→ core.execute_action)
arbor/auth/            AuthProvider seam: Local + generic OIDC providers
arbor/api.py, arbor/agent/, arbor/adapter/   thin re-export shims for the
                       documented collapsed public paths (no logic)
arbor/hooks.py         the ONE integrator manifest (doc_events, scheduler,
                       whitelist aliases)
frontend/              React 19 + Vite + Tailwind thin shell
arbor_sso_overlay/     separate, isolated employee SSO app (deployment-only)
tests/                 the 506-case catalog + bench-free and bench-tier suites
```

## Build / run / test

See **[`RUNNING.md`](RUNNING.md)** for the four test tiers (bench-free core
pytest, frontend Vitest, Frappe integration pytest, Playwright e2e), how to
install/run the Frappe app and the React app, and how to enable/disable the
employee SSO module.

Quick start:

```bash
make test-core        # (a) bench-free pure-core pytest — runs anywhere
make test-frontend    # (b) frontend Vitest (after make install-frontend)
make lint             # ruff (python) + tsc --noEmit (frontend type-check)
```

The pure core has **no third-party runtime dependencies**. The Frappe adapter's
deploy deps (`requests`, `litellm`, `PyJWT`, `cryptography`) live in the `app`
optional-dependency group in `pyproject.toml` and are imported lazily so the
bench-free core never pulls them.

---

## Status

**Build complete and assembled.** All lanes are integrated against the single
DRY core: the 13 DocTypes, the Frappe adapter (FrappeRepository / FrappeEventSink
/ canonical seed), the notification + webhook dispatchers, the server-side Re-Act
agent, the whitelisted capability API, the auth seam (Local / OIDC), the React 19
+ Tailwind frontend, and the isolated employee SSO app. The shared manifests
(`arbor/hooks.py`, `arbor/modules.txt`, `pyproject.toml`,
`frontend/package.json`) are wired in `arbor/hooks.py`.

DRY verified: exactly ONE registry, ONE `execute_action`, ONE ACL resolver, ONE
event emitter (`FrappeEventSink`), ONE snapshot serializer, ONE notification
dispatcher, ONE webhook dispatcher — every surface funnels into them.

Bench-free suites are green here (core + dispatch + webhooks + parity harness +
agent orchestration + auth + seed parity + doctype schema) and the frontend
Vitest suite is green (92 tests, `tsc` clean, production build OK). The
bench-tier and Playwright tiers run against a Frappe site / running app per
`RUNNING.md`.
