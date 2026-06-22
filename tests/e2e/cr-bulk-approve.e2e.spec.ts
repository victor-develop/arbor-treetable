// Runnable status: NEEDS A RUNNING APP (Vite frontend + Frappe backend with the
// canonical seed). See ./README.md.
//
// Drives the P2 bulk Change-Request workflow end to end: two distinct suggesters
// (A and E, neither of whom owns col:budget) each propose a change to a cell C
// owns, so the server files TWO CRs whose resolved approver is C. C then clears
// the whole queue in one gesture via the new bulk bar instead of N per-row
// clicks.
//
// Arrange (via API, the parity surface): as A and as E, dispatch updateCell on
// col:budget (501 and 502) → two proposed CRs routed to approver C. Assert (via
// UI): C opens the Change Requests Governance tab, selects every CR it can
// approve, bulk-approves, and the queue drains while at least one budget value
// applies.
//
// RED until P1 (GovernancePanel tabs) + P2 (selection model + BulkActionBar) ship.

import { test, expect, type APIRequestContext } from "@playwright/test";
import { loginAs, resetSeed, openGovernanceTab, SHEET } from "./fixtures";

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

test.describe("bulk change-request approval (e2e)", () => {
  test("an approver bulk-approves every CR it can decide and the queue drains (WEB_UI-P2)", async ({
    page,
    request,
  }) => {
    await resetSeed();

    // Two different suggest-only personas each propose a change to col:budget
    // (owned by C) → the server files two CRs whose resolved approver is C.
    await call(request, "A", "arbor.execute_action", {
      action_id: "updateCell",
      params: { sheet: SHEET, node: "X", column: "col:budget", value: 501 },
    });
    await call(request, "E", "arbor.execute_action", {
      action_id: "updateCell",
      params: { sheet: SHEET, node: "Y", column: "col:budget", value: 502 },
    });

    // C opens the sheet WITHOUT re-seeding (openSheet resets the sheet, which
    // would drop the CRs just filed); both open CRs must survive into C's session.
    await loginAs(page, "C");
    await page.goto(`/?sheet=${encodeURIComponent(SHEET)}`);
    await expect(page.getByTestId("tree-table")).toBeVisible();

    // Open the consolidated Governance panel's "Change Requests" tab; only the
    // active tab mounts its content, so the inbox is hidden until the tab is hit.
    await openGovernanceTab(page, "Change Requests");

    const inbox = page.getByTestId("cr-inbox");
    await expect(inbox).toBeVisible();
    // Both proposed CRs are queued and C is their approver → both expose a
    // selection checkbox.
    await expect(inbox.locator('[data-testid^="cr-panel-"]')).toHaveCount(2);
    await expect(inbox.locator('[data-testid^="cr-select-"]')).toHaveCount(2);

    // "Select all I can approve" toggles only the actionable subset (here, both).
    await page.getByTestId("cr-select-all").click();

    // The sticky bulk bar appears once >=1 is selected, summarising the count.
    const bulkBar = page.getByTestId("cr-bulk-bar");
    await expect(bulkBar).toBeVisible();
    await expect(bulkBar).toContainText("2 selected");

    // Bulk approve loops approveChange per selected CR id.
    await bulkBar.getByTestId("cr-bulk-approve").click();

    // Completion summary: 2 approved, 0 failed; and after the snapshot refetch the
    // applied CRs leave the queue entirely.
    await expect(page.getByTestId("cr-bulk-summary")).toContainText("2 approved");
    await expect(inbox.locator('[data-testid^="cr-panel-"]')).toHaveCount(0);

    // At least one proposed budget value applied (the capability replayed as C).
    await expect(
      page.locator('[data-testid="tree-table"]'),
    ).toContainText(/50[12]/);
  });
});
