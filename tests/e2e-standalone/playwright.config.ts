// Playwright config for the STANDALONE-adapter e2e lane.
//
// Unlike tests/e2e (which needs a hand-started Vite + Frappe bench), this lane is
// SELF-BOOTING: Playwright's webServer starts the real standalone FastAPI server
// (sqlite, dev-login, serving the built frontend/dist) and tears it down after.
// Prereqs: `cd frontend && npx vite build` (a fresh dist) and the python venv in
// ARBOR_SA_PYTHON (defaults to the repo-local throwaway venv).
//
// Run: cd frontend && npm run test:e2e:standalone

import * as path from "path";
import { defineConfig, devices } from "@playwright/test";

const ROOT = path.resolve(__dirname, "..", "..");
const PY = process.env.ARBOR_SA_PYTHON ?? "/Users/victorzhou/temp/arbor-sa-venv/bin/python";
const PORT = Number(process.env.ARBOR_SA_E2E_PORT ?? 3199);

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.e2e\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 1,
  reporter: process.env.CI ? "github" : "list",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // Fresh sqlite DB per run (rm first) so specs own their world.
    command:
      `rm -f /tmp/arbor-e2e.db && cd ${ROOT} && ` +
      `ARBOR_DEV_LOGIN=1 ARBOR_SECRET_KEY=e2e ` +
      `ARBOR_ADMIN_EMAILS=admin@e2e.local ` +
      `DATABASE_URL=sqlite:////tmp/arbor-e2e.db ` +
      `ARBOR_FRONTEND_DIST=${ROOT}/frontend/dist ` +
      `${PY} -m uvicorn arbor.standalone.app:app --host 127.0.0.1 --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/healthz`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
