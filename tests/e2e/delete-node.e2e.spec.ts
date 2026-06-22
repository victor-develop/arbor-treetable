// Runnable status: NEEDS A RUNNING APP. See ./README.md.
//
// Closes a built-but-unwired gap surfaced by the unwired-surface audit: the
// deleteNode capability was backend + agent reachable but had no Web-UI control.
// A structural owner can now delete a node from the row (gated on
// can_change_structure, two-step confirm) — funnelled through executeAction.
//
// Cases: WEB_UI-046 (owner deletes a node in own branch → executed, row removed).

import { test, expect } from "@playwright/test";
import { loginAs, openSheet, row } from "./fixtures";

test.describe("delete node from the row (e2e)", () => {
  test("a branch owner deletes a node via the row control (WEB_UI-046)", async ({ page }) => {
    // D holds structural authority over the P2 branch (Y, Z) via the seeded grant.
    await loginAs(page, "D");
    await openSheet(page);
    await expect(row(page, "Z")).toBeVisible();
    await expect(page.getByTestId("node-count")).toContainText("6 nodes");

    // Two-step confirm, then the capability executes and the row disappears.
    await page.getByTestId("delete-node-Z").click();
    await page.getByTestId("delete-node-confirm-Z").click();

    await expect(row(page, "Z")).toHaveCount(0);
    await expect(page.getByTestId("node-count")).toContainText("5 nodes");
  });

  test("a non-owner sees no delete control on a node outside their authority", async ({ page }) => {
    // A owns R/P1/X but not the P2 branch → no delete affordance on Z.
    await loginAs(page, "A");
    await openSheet(page);
    await expect(row(page, "Z")).toBeVisible();
    await expect(page.getByTestId("delete-node-Z")).toHaveCount(0);
  });
});
