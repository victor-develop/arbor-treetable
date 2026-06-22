// Runnable status: NEEDS A RUNNING APP (Vite frontend + Frappe backend with the
// canonical seed). See ./README.md.
//
// Tree expand/collapse is pure local view state — it must NEVER hit the network
// (WEB_UI-006 invariant) and must hide/restore the whole subtree. Dragging a
// node whose subtree is collapsed moves the whole subtree (WEB_UI-048).
//
// Cases: WEB_UI-004 (collapse hides subtree), WEB_UI-005 (expand restores, idempotent),
//        WEB_UI-048 (collapsed subtree moves intact).

import { test, expect } from "@playwright/test";
import { loginAs, openSheet, row, chevron, dragRowOnto } from "./fixtures";

test.describe("tree expand / collapse (e2e)", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page, "A");
    await openSheet(page);
  });

  test("collapsing P2 hides Y and Z; siblings unaffected (WEB_UI-004)", async ({ page }) => {
    await expect(row(page, "Y")).toBeVisible();
    await expect(row(page, "Z")).toBeVisible();

    await chevron(page, "P2").click();

    await expect(row(page, "Y")).toHaveCount(0);
    await expect(row(page, "Z")).toHaveCount(0);
    await expect(row(page, "P2")).toBeVisible();
    await expect(row(page, "X")).toBeVisible(); // under P1, unaffected
  });

  test("expanding restores children exactly once; a second toggle is idempotent (WEB_UI-005)", async ({ page }) => {
    await chevron(page, "P2").click(); // collapse
    await expect(row(page, "Y")).toHaveCount(0);

    await chevron(page, "P2").click(); // expand
    await expect(row(page, "Y")).toHaveCount(1);
    await expect(row(page, "Z")).toHaveCount(1);

    // collapse + expand again — still exactly one each (no duplicate rows)
    await chevron(page, "P2").click();
    await chevron(page, "P2").click();
    await expect(row(page, "Y")).toHaveCount(1);
    await expect(row(page, "Z")).toHaveCount(1);
  });

  test("dragging a collapsed group moves its whole subtree intact (WEB_UI-048)", async ({ page }) => {
    // Collapse P1 (hides X), then move P1 after P2 at root level.
    await chevron(page, "P1").click();
    await expect(row(page, "X")).toHaveCount(0);

    await dragRowOnto(page, "P1", "P2", "after");

    // After the executed move + snapshot refetch, P1 still parents X.
    await expect(row(page, "P1")).toBeVisible();
    // Re-expand to confirm subtree integrity.
    await chevron(page, "P1").click();
    await expect(row(page, "X")).toBeVisible();
  });
});
