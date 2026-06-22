// Playwright config for the Arbor Web-UI e2e specs.
//
// Runnable status: NEEDS A RUNNING APP. These specs assume a live Vite frontend
// (default http://localhost:5173) proxying /api to a running Frappe backend with
// the canonical seed (tests/TEST-PLAN.md §2) loaded. See ./README.md.
//
// The integrator adds @playwright/test to the toolchain (lane manifest nodeDeps);
// this file is intentionally self-contained so it can be hoisted to the repo root
// or referenced via `--config tests/e2e/playwright.config.ts`.

import { defineConfig, devices } from "@playwright/test";

const BASE_URL = process.env.ARBOR_E2E_BASE_URL ?? "http://localhost:5173";

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.e2e\.spec\.ts/,
  // e2e specs hit a real backend and share ONE seeded sheet (reset per test via
  // fixtures.resetSeed), so they run strictly serially — one worker, no cross-file
  // parallelism — otherwise concurrent resets race on sheet S.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  // One retry: these specs drive a real browser + backend, so occasional
  // render/commit timing flakes (e.g. a refetch landing mid-interaction) retry green.
  retries: 1,
  reporter: process.env.CI ? "github" : "list",
  // The dev backend is single-process and each test re-seeds (drop+reseed+clear
  // cache), so requests can be slow under load — give assertions + tests headroom.
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
