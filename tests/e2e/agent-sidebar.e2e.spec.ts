// Runnable status: NEEDS A RUNNING APP (Vite frontend + Frappe backend with a
// real server-side agent behind arbor.agent.chat that streams NDJSON Re-Act
// frames). See ./README.md.
//
// The agent acts as its OWN User under the same two-axis ACL (ARCHITECTURE §8):
// an authorized action executes and the affected grid cell updates; an
// unauthorized action becomes a Change Request (a CR chip), NOT a silent
// mutation. The transcript attributes executed changes to the agent
// (actor_type=agent badge). These prove the governance property end-to-end.
//
// Cases: WEB_UI-065 (agent executed action refreshes the cell), WEB_UI-066
//        (agent unauthorized action → CR chip, no mutation), WEB_UI-073
//        (agent actor badge).
//
// NOTE: outcomes depend on which columns the agent's User owns in the seeded
// world. These specs assume the canonical seed grants the agent edit authority
// on col:status (executed path) and none on col:budget (CR path), matching
// web-ui.md. Adjust the agent's grants in the seed if your deployment differs.

import { test, expect } from "@playwright/test";
import { loginAs, openSheet, cell, splitCell } from "./fixtures";

test.describe("agent sidebar (e2e)", () => {
  test.beforeEach(async ({ page }) => {
    // The agent drives a real LLM (several sequential calls per turn); give these
    // specs plenty of headroom over the default test timeout.
    test.setTimeout(150_000);
    // B is the editor of col:status (→ status edits execute) but does NOT own
    // col:budget (→ budget edits suggest): exactly the split WEB_UI-065 (executed)
    // vs -066 (suggested) assert. The agent acts under this user's authority.
    await loginAs(page, "B");
    await openSheet(page);
    await expect(page.getByTestId("agent-sidebar")).toBeVisible();
  });

  async function ask(page: import("@playwright/test").Page, message: string) {
    await page.getByTestId("agent-input").fill(message);
    await page.getByTestId("agent-send").click();
  }

  test("an executed agent action updates the affected cell + summarizes (WEB_UI-065/-073)", async ({ page }) => {
    await ask(page, "set X status to done");

    // The Re-Act loop makes several sequential LLM calls (read → mutate → final),
    // so allow generous time for the real provider before the summary appears.
    await expect(page.getByTestId("frame-final")).toBeVisible({ timeout: 60_000 });
    // executed observation drives a snapshot refetch → the split-select cell shows
    // "done" as the selected segment (col:status is single-select-split)
    await expect(
      splitCell(page, "X", "col:status").getByTestId("segment-done"),
    ).toHaveAttribute("aria-checked", "true");
    // the agent reads first (getSheetSnapshot → observation outcome "read") then
    // mutates, so assert an EXECUTED observation is present in the feed.
    await expect(
      page.locator('[data-testid="frame-observation"][data-outcome="executed"]'),
    ).toBeVisible();
  });

  test("an unauthorized agent action files a Change Request, not a mutation (WEB_UI-066)", async ({ page }) => {
    const budgetBefore = await cell(page, "X", "col:budget").textContent();

    await ask(page, "change X budget to 99999");

    await expect(page.getByTestId("frame-final")).toBeVisible({ timeout: 60_000 });
    // a CR chip renders (suggested observation); grid cell unchanged
    await expect(page.locator('[data-testid^="cr-chip-"]').first()).toBeVisible();
    await expect(cell(page, "X", "col:budget")).toHaveText(budgetBefore ?? "");
  });
});
