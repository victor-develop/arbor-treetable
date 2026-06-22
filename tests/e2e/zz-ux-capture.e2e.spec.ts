// Runnable status: NEEDS A RUNNING APP. See ./README.md.
//
// UX review capture — NOT an assertion suite. Drives each Arbor surface into a
// representative state and saves a screenshot under docs/ux-review/ for the
// per-surface UX-designer review (Frappe UI conformance). Named zz-* so it runs
// last in the serial suite (after the functional specs).

import { test, expect, type APIRequestContext } from "@playwright/test";
import { loginAs, openSheet, openGovernanceTab, resetSeed, SHEET } from "./fixtures";

const DIR = "docs/ux-review";
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

test("ux-01 main sheet view (owner)", async ({ page }) => {
  await loginAs(page, "B"); // owns col:notes → shows editable + suggest cells
  await openSheet(page);
  await expect(page.getByTestId("tree-table")).toBeVisible();
  await page.screenshot({ path: `${DIR}/ux-01-main.png`, fullPage: true });
});

test("ux-02 column schema editor (modal)", async ({ page }) => {
  await loginAs(page, "C"); // owns col:budget
  await openSheet(page);
  await page.getByTestId("col-settings-open-col:budget").click();
  await expect(page.getByTestId("column-settings-modal")).toBeVisible();
  await page.screenshot({ path: `${DIR}/ux-02-column-modal.png`, fullPage: true });
});

test("ux-03 change request review inbox", async ({ page, request }) => {
  await resetSeed();
  await call(request, "A", "arbor.execute_action", {
    action_id: "updateCell",
    params: { sheet: SHEET, node: "X", column: "col:budget", value: 500 },
  });
  await loginAs(page, "C");
  await page.goto(`/?sheet=${encodeURIComponent(SHEET)}`);
  await expect(page.getByTestId("tree-table")).toBeVisible();
  await openGovernanceTab(page, "Change Requests");
  await expect(page.getByTestId("cr-inbox").locator('[data-testid^="cr-panel-"]').first()).toBeVisible();
  await page.screenshot({ path: `${DIR}/ux-03-cr-inbox.png`, fullPage: true });
});

test("ux-04 notification inbox", async ({ page, request }) => {
  await resetSeed();
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
  await page.screenshot({ path: `${DIR}/ux-04-notifications.png`, fullPage: true });
});

test("ux-05 branch delegation control", async ({ page }) => {
  await loginAs(page, "A");
  await openSheet(page);
  await openGovernanceTab(page, "Delegations");
  await expect(
    page.getByTestId("delegation-control").locator('[data-grantee="d@arbor.example"]'),
  ).toBeVisible();
  await page.screenshot({ path: `${DIR}/ux-05-delegation.png`, fullPage: true });
});

test("ux-06 agent sidebar (tools open)", async ({ page }) => {
  await loginAs(page, "B");
  await openSheet(page);
  await page.getByTestId("agent-tools-toggle").click();
  await expect(page.getByTestId("agent-tools")).toBeVisible();
  await page.screenshot({ path: `${DIR}/ux-06-agent.png`, fullPage: true });
});

test("ux-07 delete confirm + cell editing", async ({ page }) => {
  await loginAs(page, "D"); // owns P2 branch
  await openSheet(page);
  await page.getByTestId("delete-node-Z").click();
  await expect(page.getByTestId("delete-node-confirm-Z")).toBeVisible();
  await page.screenshot({ path: `${DIR}/ux-07-delete-confirm.png`, fullPage: true });
});
