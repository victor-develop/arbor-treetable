// Runnable status: NEEDS A RUNNING APP (Vite frontend + Frappe backend with the
// canonical seed). See ./README.md.
//
// Tests-first (RED until P1 + P2 land): drives the bulk-reject triage path on the
// consolidated Governance panel's "Change Requests" tab.
//
// Arrange (via API, the parity surface): A (no authority on col:budget, owned by
// C) proposes two edits on two distinct nodes (X 1000→500, Y 5000→999). Each files
// a CR whose resolved approver is C. Act (via UI): C opens the sheet, switches to
// the Change Requests governance tab, selects all the CRs it can approve, clicks
// the bulk "Reject N", fills the single shared reason, and confirms the batch
// reject. Assert: both CRs leave the proposed queue AND the underlying seeded
// values are untouched (reject never replays the change).
//
// The bulk bar loops the SAME rejectChange dispatch per selected id — no new
// capability, no ad-hoc write. This is the P2 bulk-reject counterpart to the
// existing single-CR approve spec (change-request-review.e2e.spec.ts).

import { test, expect, type APIRequestContext } from "@playwright/test";
import { loginAs, resetSeed, openGovernanceTab, cell, SHEET } from "./fixtures";

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

test.describe("bulk change-request reject (e2e)", () => {
  test("an approver bulk-rejects two suggested CRs with a shared reason; values stay unchanged", async ({
    page,
    request,
  }) => {
    await resetSeed();

    // A owns no columns. Editing col:budget (owner C) on two distinct nodes files
    // two separate CRs, both resolving to approver C. Seeded X=1000, Y=5000.
    await call(request, "A", "arbor.execute_action", {
      action_id: "updateCell",
      params: { sheet: SHEET, node: "X", column: "col:budget", value: 500 },
    });
    await call(request, "A", "arbor.execute_action", {
      action_id: "updateCell",
      params: { sheet: SHEET, node: "Y", column: "col:budget", value: 999 },
    });

    // C is the resolved approver. Open WITHOUT re-seeding so the two proposed CRs
    // survive into C's session (openSheet would reset and drop them).
    await loginAs(page, "C");
    await page.goto(`/?sheet=${encodeURIComponent(SHEET)}`);
    await expect(page.getByTestId("tree-table")).toBeVisible();

    // Open the consolidated Governance panel's Change Requests tab (P1). Only the
    // active tab's content mounts, so the inbox is reachable only after this click.
    await openGovernanceTab(page, "Change Requests");

    const inbox = page.getByTestId("cr-inbox");
    await expect(inbox).toBeVisible();
    // Both proposed CRs are listed and C is the approver on each.
    await expect(inbox.locator('[data-testid^="cr-panel-"]')).toHaveCount(2);

    // Capture the two CR record names so we can target their row checkboxes and,
    // after reject, assert they have left the proposed queue.
    const panels = inbox.locator('[data-testid^="cr-panel-"]');
    const crNames: string[] = [];
    for (let i = 0; i < 2; i++) {
      const tid = await panels.nth(i).getAttribute("data-testid");
      crNames.push(tid!.replace("cr-panel-", ""));
    }

    // Select every CR the viewer can approve (the actionable subset = both here).
    await page.getByTestId("cr-select-all").click();
    for (const name of crNames) {
      await expect(page.getByTestId(`cr-select-${name}`)).toBeChecked();
    }

    // The sticky bulk bar appears with the count once >=1 is selected.
    const bulkBar = page.getByTestId("cr-bulk-bar");
    await expect(bulkBar).toBeVisible();
    await expect(bulkBar).toContainText("2 selected");

    // Reject N → the shared optional reason field appears; fill it then confirm.
    await page.getByTestId("cr-bulk-reject").click();
    const reason = page.getByTestId("cr-bulk-reject-reason");
    await expect(reason).toBeVisible();
    await reason.fill("Budget figures are out of date — resubmit after Q3 close.");
    // Second click on the bulk reject confirms the batch (warns it drops the batch).
    await page.getByTestId("cr-bulk-reject").click();

    // One consolidated summary line — no N-toast spam. Both rejected, none failed.
    const summary = page.getByTestId("cr-bulk-summary");
    await expect(summary).toBeVisible();
    await expect(summary).toContainText("2");
    await expect(summary).toContainText("0 failed");

    // Both CRs leave the proposed queue after the refresh.
    await expect(inbox.locator('[data-testid^="cr-panel-"]')).toHaveCount(0);

    // Reject never replays: the seeded values are untouched in the grid.
    await expect(cell(page, "X", "col:budget")).toContainText("1000");
    await expect(cell(page, "Y", "col:budget")).toContainText("5000");
  });
});
