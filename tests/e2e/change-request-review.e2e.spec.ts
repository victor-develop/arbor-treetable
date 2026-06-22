// Runnable status: NEEDS A RUNNING APP (Vite frontend + Frappe backend with the
// canonical seed). See ./README.md.
//
// Closes the "I don't see the screen to review a change request" gap: the
// ChangeRequestPanel was built + unit-tested but never mounted in App.tsx, so an
// approver had no Web-UI surface to act on a proposed CR. This drives the full
// suggest → review → approve lifecycle through the real capability API:
//   1. A (no edit authority) edits col:budget → server files a CR to its owner C.
//   2. C opens the sheet → the CR inbox lists the proposed CR with Approve/Reject.
//   3. C approves → the capability replays as C and the seeded 1000 becomes 500.
//
// The inbox calls one real capability per decision (approveChange / rejectChange
// / withdrawChange), never an ad-hoc write — same funnel as every other surface.
//
// Cases: WEB_UI-093 (approver sees the inbox), WEB_UI-094 (approve applies the change).

import { test, expect } from "@playwright/test";
import { loginAs, openSheet, openGovernanceTab, cell, SHEET } from "./fixtures";

test.describe("change request review inbox (e2e)", () => {
  test("an approver reviews and approves a proposed change via the inbox (WEB_UI-093/-094)", async ({
    page,
  }) => {
    // A owns no columns; editing col:budget (owned by C) files a CR to C.
    await loginAs(page, "A");
    await openSheet(page);

    const budget = cell(page, "X", "col:budget");
    await expect(budget).toHaveAttribute("data-mode", "suggest");
    await budget.dblclick();
    const input = page.getByTestId("cell-input");
    await input.fill("500");
    await input.blur();
    await expect(page.getByTestId("banner")).toHaveAttribute("data-kind", "suggested");

    // Switch to C, the resolved approver. Navigate WITHOUT re-seeding (openSheet
    // resets the sheet, which would drop the CR A just filed); the open CR must
    // survive into C's session and surface in the inbox.
    await loginAs(page, "C");
    await page.goto(`/?sheet=${encodeURIComponent(SHEET)}`);
    await expect(page.getByTestId("tree-table")).toBeVisible();

    // The CR inbox now lives behind the consolidated Governance panel's
    // "Change Requests" tab; only the active tab mounts its content.
    await openGovernanceTab(page, "Change Requests");

    const inbox = page.getByTestId("cr-inbox");
    await expect(inbox).toBeVisible();
    const panel = inbox.locator('[data-testid^="cr-panel-"]').first();
    await expect(panel).toBeVisible();
    await expect(panel).toHaveAttribute("data-status", "proposed");

    // C is the approver → Approve/Reject are offered; approving applies the change.
    const approve = panel.getByTestId("cr-approve");
    await expect(approve).toBeVisible();
    await approve.click();

    // The capability replays as C: the seeded 1000 becomes the proposed 500, and
    // the CR drops out of the proposed inbox.
    await expect(cell(page, "X", "col:budget")).toContainText("500");
    await expect(inbox.locator('[data-testid^="cr-panel-"]')).toHaveCount(0);
  });
});
