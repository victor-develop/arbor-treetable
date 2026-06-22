// Runnable status: NEEDS A RUNNING APP (Vite frontend + Frappe backend with the
// canonical seed). See ./README.md.
//
// Inline cell edit drives the real updateCell capability end-to-end and renders
// the two Outcome kinds: an OWNED edit commits with a "Saved" toast (executed);
// a NON-OWNED edit reverts and shows a "Suggestion sent to <approver>" toast
// referencing the real Change Request (suggested). The toast distinction is the
// direct-vs-suggest signal the task calls for.
//
// Cases: WEB_UI-011 (owner direct → Saved), WEB_UI-014 (non-owner → suggested toast).

import { test, expect } from "@playwright/test";
import { loginAs, openSheet, cell } from "./fixtures";

test.describe("inline edit → direct-vs-suggest toast (e2e)", () => {
  test("owner edits an owned cell → executed commit + Saved toast (WEB_UI-011)", async ({ page }) => {
    // B owns col:notes.
    await loginAs(page, "B");
    await openSheet(page);

    const notes = cell(page, "X", "col:notes");
    await expect(notes).toHaveAttribute("data-mode", "edit");
    await notes.dblclick();
    const input = page.getByTestId("cell-input");
    await input.fill("ship by Q3");
    await input.blur();

    const banner = page.getByTestId("banner");
    await expect(banner).toHaveAttribute("data-kind", "saved");
    await expect(notes).toContainText("ship by Q3");
    // executed path must show no CR reference
    await expect(page.getByTestId("banner-cr")).toHaveCount(0);
  });

  test("non-owner edits a cell → suggested toast naming the approver, value reverts (WEB_UI-014)", async ({ page }) => {
    // A owns no columns; col:budget is owned by C → the server files a CR to C.
    await loginAs(page, "A");
    await openSheet(page);

    const budget = cell(page, "X", "col:budget");
    await expect(budget).toHaveAttribute("data-mode", "suggest");
    await budget.dblclick();
    const input = page.getByTestId("cell-input");
    await input.fill("500");
    await input.blur();

    const banner = page.getByTestId("banner");
    await expect(banner).toHaveAttribute("data-kind", "suggested");
    // resolved approver = the column owner's Frappe user id (C → c@arbor.example)
    await expect(banner).toContainText("Suggestion sent to c@arbor.example");
    await expect(page.getByTestId("banner-cr")).toBeVisible(); // real CR id rendered
    // optimistic 500 reverted to the seeded snapshot value 1000
    await expect(budget).toContainText("1000");
  });
});
