// Runnable status: NEEDS A RUNNING APP. See ./README.md.
//
// Regenerates the captured user-journey screenshots (docs/journey/01..16) with
// the current Frappe-UI styling. This is a CAPTURE spec, not an assertion suite:
// each test drives a surface into its representative state and writes a full-page
// PNG. Named journey-* and ordered 01..16 so the serial runner produces them in
// sequence. Agent steps (08/09) call the real z.ai agent and need long timeouts.

import { readFileSync } from "node:fs";
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";
import { loginAs, openSheet, openGovernanceTab, openDataDisclosure, resetSeed, cell, dragRowOnto, SHEET } from "./fixtures";

const DIR = "docs/journey";
const KEY = (p: string) => process.env[`ARBOR_E2E_${p}_KEY`];

async function api(
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

async function ask(page: Page, message: string) {
  await page.getByTestId("agent-input").fill(message);
  await page.getByTestId("agent-send").click();
}

test("journey 01 — governed tree table", async ({ page }) => {
  await loginAs(page, "B");
  await openSheet(page);
  await expect(page.getByTestId("tree-table")).toBeVisible();
  await page.screenshot({ path: `${DIR}/01-governed-tree-table.png`, fullPage: true });
});

test("journey 02 — owner edit executed (Saved)", async ({ page }) => {
  await loginAs(page, "B"); // owns col:notes
  await openSheet(page);
  const notes = cell(page, "X", "col:notes");
  await notes.dblclick();
  await page.getByTestId("cell-input").fill("ship by Q3");
  await page.getByTestId("cell-input").blur();
  await expect(page.getByTestId("banner")).toHaveAttribute("data-kind", "saved");
  await page.screenshot({ path: `${DIR}/02-owner-edit-executed.png`, fullPage: true });
});

test("journey 03 — non-owner suggested change", async ({ page }) => {
  await loginAs(page, "E"); // cannot edit col:budget (owned by C)
  await openSheet(page);
  const budget = cell(page, "X", "col:budget");
  await budget.dblclick();
  await page.getByTestId("cell-input").fill("500");
  await page.getByTestId("cell-input").blur();
  await expect(page.getByTestId("banner")).toHaveAttribute("data-kind", "suggested");
  await page.screenshot({ path: `${DIR}/03-non-owner-suggested-cr.png`, fullPage: true });
});

test("journey 04 — schema add column", async ({ page }) => {
  await loginAs(page, "A"); // structural owner can add columns
  await openSheet(page);
  await page.getByTestId("ac-field").fill("priority");
  await page.getByTestId("ac-label").fill("Priority");
  await page.getByTestId("ac-type").selectOption("number");
  await page.getByTestId("ac-submit").click();
  // addColumn commits server-side; reload to refetch the snapshot so the new
  // Priority column renders in the grid.
  await page.waitForTimeout(500);
  await page.reload();
  await expect(page.getByTestId("tree-table")).toBeVisible();
  await expect(page.getByText("Priority", { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: `${DIR}/04-schema-add-column.png`, fullPage: true });
});

test("journey 05 — drag reparent executed", async ({ page }) => {
  await loginAs(page, "D"); // owns the P2 branch
  await openSheet(page);
  await dragRowOnto(page, "Z", "Y", "inside");
  await expect(page.getByTestId("banner")).toHaveAttribute("data-kind", "saved");
  await page.screenshot({ path: `${DIR}/05-drag-reparent-executed.png`, fullPage: true });
});

test("journey 06 — move suggested with co-approver", async ({ page }) => {
  await loginAs(page, "A"); // authority at source only → suggested to D, co-approver A
  await openSheet(page);
  await dragRowOnto(page, "X", "P2", "inside");
  await expect(page.getByTestId("banner")).toHaveAttribute("data-kind", "suggested");
  await page.screenshot({ path: `${DIR}/06-move-suggested-co-approver.png`, fullPage: true });
});

test("journey 07 — subscribe to changes", async ({ page }) => {
  await loginAs(page, "G");
  await openSheet(page);
  const control = page.getByTestId("subscription-control");
  if ((await control.getAttribute("data-subscribed")) === "false") {
    await page.getByTestId("subscribe-btn").click();
    await expect(control).toHaveAttribute("data-subscribed", "true");
  }
  await page.screenshot({ path: `${DIR}/07-subscribe-to-changes.png`, fullPage: true });
});

test("journey 08 — agent executes", async ({ page }) => {
  test.setTimeout(150_000);
  await loginAs(page, "B"); // edits col:status execute under B's authority
  await openSheet(page);
  await ask(page, "set X status to done");
  await expect(page.getByTestId("frame-final")).toBeVisible({ timeout: 90_000 });
  await page.screenshot({ path: `${DIR}/08-agent-executes.png`, fullPage: true });
});

test("journey 09 — agent files a change request", async ({ page }) => {
  test.setTimeout(150_000);
  await loginAs(page, "B"); // no col:budget authority → agent action becomes a CR
  await openSheet(page);
  await ask(page, "change X budget to 99999");
  await expect(page.getByTestId("frame-final")).toBeVisible({ timeout: 90_000 });
  await expect(page.locator('[data-testid^="cr-chip-"]').first()).toBeVisible();
  await page.screenshot({ path: `${DIR}/09-agent-files-change-request.png`, fullPage: true });
});

test("journey 10+11 — import preview then result", async ({ page }) => {
  await loginAs(page, "A");
  await openSheet(page, "S");
  await openDataDisclosure(page); // ImportExport now lives in the header "Data" disclosure (P1)
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByTestId("export-btn").click(),
  ]);
  const exported = readFileSync((await download.path())!, "utf8");

  await openSheet(page, "S2");
  await openDataDisclosure(page);
  await page.getByTestId("import-text").fill(exported);
  await expect(page.getByTestId("import-preview")).toBeVisible();
  await page.screenshot({ path: `${DIR}/10-import-preview.png`, fullPage: true });

  await page.getByTestId("import-confirm").click();
  await expect(page.getByTestId("banner")).toContainText(/import/i);
  await expect(page.getByTestId("node-count")).toContainText("6 nodes");
  await page.screenshot({ path: `${DIR}/11-import-result.png`, fullPage: true });
});

test("journey 12 — change request review", async ({ page, request }) => {
  await resetSeed();
  await api(request, "A", "arbor.execute_action", {
    action_id: "updateCell",
    params: { sheet: SHEET, node: "X", column: "col:budget", value: 500 },
  });
  await loginAs(page, "C"); // resolved approver
  await page.goto(`/?sheet=${encodeURIComponent(SHEET)}`);
  await expect(page.getByTestId("tree-table")).toBeVisible();
  await openGovernanceTab(page, "Change Requests");
  await expect(page.getByTestId("cr-inbox").locator('[data-testid^="cr-panel-"]').first()).toBeVisible();
  await page.screenshot({ path: `${DIR}/12-change-request-review.png`, fullPage: true });
});

test("journey 13 — column schema editor", async ({ page }) => {
  await loginAs(page, "C"); // owns col:budget
  await openSheet(page);
  await page.getByTestId("col-settings-open-col:budget").click();
  await expect(page.getByTestId("column-settings-modal")).toBeVisible();
  await page.screenshot({ path: `${DIR}/13-column-settings.png`, fullPage: true });
});

test("journey 14 — delete a node (confirm)", async ({ page }) => {
  await loginAs(page, "D"); // owns the P2 branch
  await openSheet(page);
  await page.getByTestId("delete-node-Z").click();
  await expect(page.getByTestId("delete-node-confirm-Z")).toBeVisible();
  await page.screenshot({ path: `${DIR}/14-delete-node.png`, fullPage: true });
});

test("journey 15 — notification inbox", async ({ page, request }) => {
  await resetSeed();
  await api(request, "G", "arbor.subscribe", {
    scope: "sheet",
    target: SHEET,
    event_types: ["CHANGE_PROPOSED", "CHANGE_APPROVED"],
    delivery: "in-app",
    requires_ack: 1,
  });
  await api(request, "A", "arbor.execute_action", {
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
  await page.screenshot({ path: `${DIR}/15-notification-inbox.png`, fullPage: true });
});

test("journey 16 — branch delegation control", async ({ page }) => {
  await loginAs(page, "A"); // sheet owner; may delegate / revoke
  await openSheet(page);
  await openGovernanceTab(page, "Delegations");
  await expect(
    page.getByTestId("delegation-control").locator('[data-grantee="d@arbor.example"]'),
  ).toBeVisible();
  await page.screenshot({ path: `${DIR}/16-delegation.png`, fullPage: true });
});
