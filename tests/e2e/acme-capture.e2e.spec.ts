// Capture the ACME showcase walkthrough screenshots for docs/DEMO-JOURNEYS.md.
//
// CAPTURE spec (not an assertion suite): drives each new surface into a
// representative state as ADMIN and writes a PNG to docs/demo/acme/. Run against
// the PUBLIC frontend (which reads window.__ARBOR_AUTH__) with an admin API key:
//
//   cd frontend && ARBOR_E2E_BASE_URL=http://localhost:5174 \
//     ARBOR_E2E_ADMIN_KEY="$(cat /tmp/acme_admin_key)" \
//     npx playwright test --config ../tests/e2e/playwright.config.ts acme-capture
//
// Re-seed demo/showcase/seed.py first so the governance inboxes are populated.

import { test, expect, type Page } from "@playwright/test";

const DIR = "docs/demo/acme";
const SHEET = "ACME";
const KEY = process.env.ARBOR_E2E_ADMIN_KEY;

async function loginAdmin(page: Page): Promise<void> {
  if (KEY) {
    await page.addInitScript((token) => {
      (window as unknown as { __ARBOR_AUTH__?: string }).__ARBOR_AUTH__ = token;
    }, `token ${KEY}`);
  }
}

async function openACME(page: Page): Promise<void> {
  await page.goto(`/?sheet=${SHEET}`);
  await expect(page.getByTestId("tree-table")).toBeVisible();
}

async function openTab(page: Page, name: string) {
  const t = page.getByRole("tab", { name });
  await expect(t).toBeVisible();
  await t.click();
  return page.getByTestId("governance-panel");
}

test.beforeEach(async ({ page }) => {
  await loginAdmin(page);
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("01 — sheet list home page", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("sheet-list")).toBeVisible();
  await page.screenshot({ path: `${DIR}/01-sheet-list.png` });
});

test("02 — ACME tree table (J1)", async ({ page }) => {
  await openACME(page);
  await page.screenshot({ path: `${DIR}/02-tree-table.png` });
  await page.screenshot({ path: `${DIR}/02-tree-table-full.png`, fullPage: true });
});

test("03 — Change Request inbox (J2)", async ({ page }) => {
  await openACME(page);
  const panel = await openTab(page, "Change Requests");
  await expect(page.getByTestId("cr-inbox")).toBeVisible();
  await panel.screenshot({ path: `${DIR}/03-change-requests.png` });
});

test("04 — Roles: applications + grants + assign (J5)", async ({ page }) => {
  await openACME(page);
  const panel = await openTab(page, "Roles");
  await expect(page.getByTestId("roles-panel")).toBeVisible();
  await panel.screenshot({ path: `${DIR}/04-roles.png` });
});

test("05 — Branch delegations (J6)", async ({ page }) => {
  await openACME(page);
  const panel = await openTab(page, "Delegations");
  await expect(page.getByTestId("delegation-control")).toBeVisible();
  await panel.screenshot({ path: `${DIR}/05-delegations.png` });
});

test("06 — Activity timeline (J13)", async ({ page }) => {
  await openACME(page);
  const panel = await openTab(page, "Activity");
  await expect(page.getByTestId("activity-filter-type")).toBeVisible();
  await panel.screenshot({ path: `${DIR}/06-activity.png` });
});

test("07 — Activity filtered by type (J13 filters)", async ({ page }) => {
  await openACME(page);
  const panel = await openTab(page, "Activity");
  await page.getByTestId("activity-filter-type").selectOption("CHANGE_PROPOSED");
  await page.waitForTimeout(500);
  await panel.screenshot({ path: `${DIR}/07-activity-filtered.png` });
});

test("08 — View column manager popover (J11)", async ({ page }) => {
  await openACME(page);
  await page.locator(".arbor-view-disclosure > summary").click();
  await expect(page.locator(".arbor-view-menu")).toBeVisible();
  await page.waitForTimeout(200);
  await page.screenshot({ path: `${DIR}/08-view-popover.png` });
});

test("09 — mobile (390×844)", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openACME(page);
  await page.screenshot({ path: `${DIR}/09-mobile.png`, fullPage: true });
});
