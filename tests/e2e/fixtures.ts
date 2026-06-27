// Shared e2e helpers — persona login, sheet navigation, and tree-row locators.
//
// Runnable status: NEEDS A RUNNING APP. See ./README.md. These helpers bind to
// the SAME data-testid hooks the React components expose (frontend/src/**), so
// the e2e selectors and the bench-free component selectors stay in lockstep.

import { type Page, expect } from "@playwright/test";

export const SHEET = process.env.ARBOR_E2E_SHEET ?? "S";
export type Persona = "A" | "B" | "C" | "D" | "E" | "F" | "G";

// Inject a per-persona Authorization header before any app code runs. The shell
// reads it via setAuthHeaderProvider (frontend/src/api.ts). In CI, seed one
// Frappe API key per persona and pass it as ARBOR_E2E_<PERSONA>_KEY.
export async function loginAs(page: Page, persona: Persona): Promise<void> {
  const key = process.env[`ARBOR_E2E_${persona}_KEY`];
  if (key) {
    await page.addInitScript((token) => {
      // The app exposes a hook for the open-source build; SSO build overrides it.
      (window as unknown as { __ARBOR_AUTH__?: string }).__ARBOR_AUTH__ = token;
    }, `token ${key}`);
  }
  // Otherwise rely on an already-authenticated session (interactive/SSO run).
}

// Reset the canonical sheet S to its seeded state before a test. The live e2e
// stack has no per-test rollback (unlike the bench integration tests), so each
// test re-seeds to stay isolated. Requires ARBOR_E2E_ADMIN_KEY + developer_mode
// (arbor.arbor.testing.reset_e2e). No-op if the key is absent (interactive runs).
export async function resetSeed(): Promise<void> {
  const key = process.env.ARBOR_E2E_ADMIN_KEY;
  if (!key) return;
  const base = process.env.ARBOR_E2E_BASE_URL ?? "http://localhost:5173";
  const res = await fetch(`${base}/api/method/arbor.arbor.testing.reset_e2e`, {
    method: "POST",
    headers: { Authorization: `token ${key}` },
  });
  if (!res.ok) throw new Error(`reset_e2e failed: ${res.status}`);
}

// Open a sheet and wait for it to mount. A populated sheet renders the tree
// table; an empty one (e.g. the S2 import target) renders the empty-state — wait
// for either so the helper works for both.
export async function openSheet(page: Page, sheet = SHEET): Promise<void> {
  await resetSeed();
  await page.goto(`/?sheet=${encodeURIComponent(sheet)}`);
  await expect(
    page.locator('[data-testid="tree-table"], [data-testid="empty-state"]').first(),
  ).toBeVisible();
}

// Open one of the consolidated Governance panel's tabs (P1). Only the active
// tab mounts its content, so any spec asserting cr-inbox / notification-inbox /
// delegation-control visibility must click the relevant tab first. Targets the
// tab by its ARIA role + visible label (stable across the panel's testid scheme).
export type GovernanceTab = "Change Requests" | "Notifications" | "Delegations";
export async function openGovernanceTab(page: Page, tab: GovernanceTab): Promise<void> {
  const t = page.getByRole("tab", { name: tab });
  await expect(t).toBeVisible();
  await t.click();
}

// Open the header "Data" disclosure (P1). ImportExport moved out of the main
// stack into a collapsible <details> labelled "Data"; its controls (export-btn /
// import-text / import-confirm) are display:none while the disclosure is closed,
// so any spec touching them must expand it first. Idempotent: a no-op if already
// open. Mirrors openGovernanceTab — the e2e selectors track the new shell layout.
export async function openDataDisclosure(page: Page): Promise<void> {
  const details = page.getByTestId("data-disclosure");
  await expect(details).toBeVisible();
  if (!(await details.evaluate((el) => (el as HTMLDetailsElement).open))) {
    await details.getByText("Data", { exact: true }).click();
  }
  await expect(page.getByTestId("export-btn")).toBeVisible();
}

export function row(page: Page, node: string) {
  return page.getByTestId(`row-${node}`);
}

export function cell(page: Page, node: string, column: string) {
  return row(page, node).locator(`[data-column="${column}"] [data-testid="cell"]`);
}

export function splitCell(page: Page, node: string, column: string) {
  return row(page, node).locator(`[data-column="${column}"] [data-testid="split-cell"]`);
}

export function chevron(page: Page, node: string) {
  return page.getByTestId(`chevron-${node}`);
}

// Simulate an HTML5 drag-drop between two rows. Drag now starts ONLY from the
// per-row grip handle (single-click on a cell edits instead), so we drag the
// handle — Playwright's dragTo dispatches the dragstart/dragover/drop sequence
// the handle/row handlers listen for. The `targetPosition` y-fraction selects
// before/inside/after (TreeRow geometry). The handle is hover-revealed, so we
// hover the source row first to make it draggable.
export async function dragRowOnto(
  page: Page,
  srcNode: string,
  destNode: string,
  position: "before" | "inside" | "after",
): Promise<void> {
  const dest = row(page, destNode);
  const box = await dest.boundingBox();
  if (!box) throw new Error(`no bounding box for row-${destNode}`);
  const yFrac = position === "before" ? 0.15 : position === "after" ? 0.85 : 0.5;
  await row(page, srcNode).hover();
  await page.getByTestId(`drag-handle-${srcNode}`).dragTo(dest, {
    targetPosition: { x: box.width / 2, y: box.height * yFrac },
  });
}
