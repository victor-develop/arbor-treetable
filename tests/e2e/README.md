# Arbor Web-UI end-to-end specs (Playwright)

**Runnable status: NEEDS A RUNNING APP.** Every spec in this directory drives a
live browser against a running Arbor stack. They are *not* bench-free and *not*
run by `vitest` — the snapshot-driven component behaviour is already covered
bench-free under `frontend/src/**/*.test.tsx`. These specs prove the same
capability wiring through the real browser + real capability API end-to-end.

## What must be running

1. **Frappe backend** with the Arbor app installed and the canonical seed loaded
   (the `seed_canonical_sheet` fixture from `tests/TEST-PLAN.md §2` — sheet `S`,
   nodes `R/P1/X/P2/Y/Z`, columns `col:name/status/budget/notes/tags`, personas
   A..G). Exposed at `http://localhost:8000`. The whitelisted methods the UI
   calls (`arbor.execute_action`, `arbor.get_sheet_snapshot`, `arbor.agent.chat`)
   must be reachable.
2. **Vite frontend** (`cd frontend && npm run dev`) at `http://localhost:5173`,
   which proxies `/api` to the backend (see `frontend/vite.config.ts`).

Override the base URL with `ARBOR_E2E_BASE_URL` and the sheet with
`ARBOR_E2E_SHEET` (defaults: `http://localhost:5173`, `S`).

## Persona login

The shell sends `Authorization` from a pluggable auth header provider
(`frontend/src/api.ts setAuthHeaderProvider`). In CI the recommended path is to
seed a Frappe API key per persona and inject it via `ARBOR_E2E_<PERSONA>_KEY`;
`fixtures.ts#loginAs` reads that and sets the header through an init script. If
your deployment uses the employee SSO app, drive the real login instead — the
selectors below are auth-agnostic.

## Running

```bash
# from repo root, with both servers up:
npx playwright test tests/e2e
```

A `playwright.config.ts` lives in this directory; point Playwright at it with
`--config tests/e2e/playwright.config.ts` if you do not hoist it to the repo
root. The integrator wires `@playwright/test` into the toolchain (see the lane
manifest `nodeDeps`).

## Case coverage (web-ui.md, e2e-marked rows)

| Spec file | Cases |
|---|---|
| `tree-expand-collapse.e2e.spec.ts` | WEB_UI-004, -005, -048 |
| `inline-edit.e2e.spec.ts` | WEB_UI-011, -014 (direct-vs-suggest toast) |
| `dnd-move.e2e.spec.ts` | WEB_UI-036, -038, -041 (drag→same moveNode outcomes) |
| `agent-sidebar.e2e.spec.ts` | WEB_UI-065, -066, -073 |
| `import-export.e2e.spec.ts` | WEB_UI-082 (round-trip, IMPORT_COMPLETED) |
| `unsubscribe.e2e.spec.ts` | WEB_UI-091/-092 (TEST-PLAN §5.1 subscribe/unsubscribe parity) |
