// Runnable status: NEEDS A RUNNING APP (Vite frontend + Frappe backend with the
// canonical seed). See ./README.md.
//
// RENOVATION capture — NOT an assertion suite. Drives the P1 (three-zone shell +
// tabbed Governance inbox) and P2 (bulk CR triage) renovated layout into its
// representative states and writes full-page PNGs under
// docs/ux-review/renovation/ for the UX-designer review. No AI-agent steps (kept
// FAST + deterministic): all data is arranged via the capability API, never the
// live z.ai agent. Named renovation-* so it slots into the serial runner.
//
// Scenes:
//   1. main idle view (renovated three-zone shell, sticky agent rail)
//   2. Governance → Change Requests tab populated with 2-3 CRs (approver C)
//   3. same, with rows selected + the sticky bulk bar visible
//   4. Governance → Notifications tab populated
//   5. Governance → Delegations tab

import { test, expect, type APIRequestContext } from "@playwright/test";
import { loginAs, openSheet, openGovernanceTab, resetSeed, SHEET } from "./fixtures";

const DIR = "docs/ux-review/renovation";
const KEY = (p: string) => process.env[`ARBOR_E2E_${p}_KEY`];

async function call(
  request: APIRequestContext,
  persona: string,
  method: string,
  body: Record<string, unknown>,
): Promise<void> {
  const key = KEY(persona);
  const res = await request.post(`/api/method/${method}`, {
    headers: key ? { Authorization: `token ${key}` } : {},
    data: body,
  });
  if (!res.ok()) throw new Error(`${method} as ${persona} failed: ${res.status()}`);
}

// Arrange three proposed CRs all routed to approver C: three suggest-only edits
// to col:budget (owned by C) on distinct nodes. A and E cannot edit col:budget,
// so each dispatch files a CR whose resolved approver is C.
async function seedThreeCrsForC(request: APIRequestContext): Promise<void> {
  await resetSeed();
  await call(request, "A", "arbor.execute_action", {
    action_id: "updateCell",
    params: { sheet: SHEET, node: "X", column: "col:budget", value: 501 },
  });
  await call(request, "E", "arbor.execute_action", {
    action_id: "updateCell",
    params: { sheet: SHEET, node: "Y", column: "col:budget", value: 502 },
  });
  await call(request, "A", "arbor.execute_action", {
    action_id: "updateCell",
    params: { sheet: SHEET, node: "Z", column: "col:budget", value: 503 },
  });
}

// Open the sheet as C WITHOUT re-seeding (openSheet resets the sheet, which would
// drop the CRs just filed); the open CRs must survive into C's session.
async function openSheetAsCNoReseed(page: import("@playwright/test").Page): Promise<void> {
  await loginAs(page, "C");
  await page.goto(`/?sheet=${encodeURIComponent(SHEET)}`);
  await expect(page.getByTestId("tree-table")).toBeVisible();
}

test("renovation 01 — main idle view (three-zone shell)", async ({ page }) => {
  // B owns col:notes → shows a representative mix of editable + suggest cells.
  await loginAs(page, "B");
  await openSheet(page);
  await expect(page.getByTestId("tree-table")).toBeVisible();
  await page.screenshot({ path: `${DIR}/01-main-idle.png`, fullPage: true });
});

test("renovation 02 — Governance: Change Requests tab populated", async ({ page, request }) => {
  await seedThreeCrsForC(request);
  await openSheetAsCNoReseed(page);

  await openGovernanceTab(page, "Change Requests");
  const inbox = page.getByTestId("cr-inbox");
  await expect(inbox).toBeVisible();
  await expect(inbox.locator('[data-testid^="cr-panel-"]')).toHaveCount(3);
  // C is approver of all three → each exposes a selection checkbox.
  await expect(inbox.locator('[data-testid^="cr-select-"]')).toHaveCount(3);

  await page.screenshot({ path: `${DIR}/02-cr-tab-populated.png`, fullPage: true });
});

test("renovation 03 — Change Requests: selected rows + sticky bulk bar", async ({
  page,
  request,
}) => {
  await seedThreeCrsForC(request);
  await openSheetAsCNoReseed(page);

  await openGovernanceTab(page, "Change Requests");
  const inbox = page.getByTestId("cr-inbox");
  await expect(inbox).toBeVisible();
  await expect(inbox.locator('[data-testid^="cr-select-"]')).toHaveCount(3);

  // Select everything C can approve → the sticky bulk bar docks at the bottom.
  await page.getByTestId("cr-select-all").click();
  const bulkBar = page.getByTestId("cr-bulk-bar");
  await expect(bulkBar).toBeVisible();
  await expect(bulkBar).toContainText("3 selected");

  await page.screenshot({ path: `${DIR}/03-bulk-bar-selected.png`, fullPage: true });
});

test("renovation 04 — Governance: Notifications tab populated", async ({ page, request }) => {
  await resetSeed();
  // G subscribes to the sheet (ack-required) then a change is proposed → G gets
  // an in-app notification.
  await call(request, "G", "arbor.subscribe", {
    scope: "sheet",
    target: SHEET,
    event_types: ["CHANGE_PROPOSED", "CHANGE_APPROVED"],
    delivery: "in-app",
    requires_ack: 1,
  });
  await call(request, "A", "arbor.execute_action", {
    action_id: "updateCell",
    params: { sheet: SHEET, node: "X", column: "col:budget", value: 777 },
  });

  await loginAs(page, "G");
  await page.goto(`/?sheet=${encodeURIComponent(SHEET)}`);
  await expect(page.getByTestId("tree-table")).toBeVisible();

  await openGovernanceTab(page, "Notifications");
  await expect(
    page.getByTestId("notification-inbox").locator('[data-testid^="notification-"]').first(),
  ).toBeVisible();

  await page.screenshot({ path: `${DIR}/04-notifications-tab.png`, fullPage: true });
});

test("renovation 05 — Governance: Delegations tab", async ({ page }) => {
  // A is the sheet structural owner; the seed has one active grant (P2 → d@).
  await loginAs(page, "A");
  await openSheet(page);

  await openGovernanceTab(page, "Delegations");
  const control = page.getByTestId("delegation-control");
  await expect(control).toBeVisible();
  await expect(control.locator('[data-grantee="d@arbor.example"]')).toBeVisible();

  await page.screenshot({ path: `${DIR}/05-delegations-tab.png`, fullPage: true });
});
