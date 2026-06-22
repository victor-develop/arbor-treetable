// Runnable status: NEEDS A RUNNING APP. See ./README.md.
//
// Closes a built-but-unwired gap surfaced by the unwired-surface audit: the
// ColumnSettings schema editor (configure / delete / reassign ownership) was
// fully implemented + unit-tested (WEB_UI-056..062) but never mounted — column
// headers had no affordance to open it. This drives the now-mounted surface:
// the column owner opens settings from the header gear, renames the column, and
// saves — the updateColumn capability commits directly and the header updates.
//
// Cases: WEB_UI-056 (owner configures a column via the Web UI — the mount gap).

import { test, expect } from "@playwright/test";
import { loginAs, openSheet } from "./fixtures";

test.describe("column settings schema editor (e2e)", () => {
  test("the column owner opens settings from the header and renames the column (WEB_UI-056)", async ({
    page,
  }) => {
    // C owns col:budget → can configure it directly.
    await loginAs(page, "C");
    await openSheet(page);

    // Open the schema editor from the column header gear.
    await page.getByTestId("col-settings-open-col:budget").click();
    const modal = page.getByTestId("column-settings-modal");
    await expect(modal).toBeVisible();
    const save = modal.getByTestId("cs-save");
    await expect(save).toHaveAttribute("data-mode", "direct"); // owner → direct commit

    // Rename and save → updateColumn executes and the header label updates.
    await modal.getByTestId("cs-label").fill("Budget ($)");
    await save.click();
    await expect(page.getByTestId("banner")).toHaveAttribute("data-kind", "saved");
    await expect(page.getByTestId("col-head-col:budget")).toContainText("Budget ($)");
  });
});
