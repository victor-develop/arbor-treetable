// Runnable status: NEEDS A RUNNING APP (Vite frontend + Frappe backend with the
// canonical seed). See ./README.md.
//
// Drag-and-drop is just a UI way to call moveNode: the SAME backend outcomes the
// REST/agent surfaces produce must result here. A drop with authority at both
// ends executes (tree re-renders in the new order); a drop with authority at
// only one end is suggested (tree reverts, a CR-bearing toast names the approver
// + co-approver). This is the "drag→same moveNode outcomes" mapping the task
// requires.
//
// Cases: WEB_UI-036 (drop inside → executed), WEB_UI-038 (drop after → reorder
//        executed), WEB_UI-041 (cross-branch one-end → suggested, co-approver).

import { test, expect } from "@playwright/test";
import { loginAs, openSheet, row, dragRowOnto } from "./fixtures";

test.describe("drag-and-drop move → moveNode outcomes (e2e)", () => {
  test("D drops Z inside Y (both in own branch) → executed, Z nests under Y (WEB_UI-036)", async ({ page }) => {
    await loginAs(page, "D"); // D owns the P2 subtree (both ends)
    await openSheet(page);

    await dragRowOnto(page, "Z", "Y", "inside");

    // executed → Saved toast, no CR
    await expect(page.getByTestId("banner")).toHaveAttribute("data-kind", "saved");
    await expect(page.getByTestId("banner-cr")).toHaveCount(0);
    // after the async refetch Z appears nested deeper than Y (its new parent);
    // poll so a one-shot read can't race the re-render.
    await expect
      .poll(async () => {
        const yDepth = Number(await row(page, "Y").getAttribute("data-depth"));
        const zDepth = Number(await row(page, "Z").getAttribute("data-depth"));
        return zDepth > yDepth;
      })
      .toBe(true);
  });

  test("D reorders Y after Z within P2 → executed, sibling order Z then Y (WEB_UI-038)", async ({ page }) => {
    await loginAs(page, "D");
    await openSheet(page);

    await dragRowOnto(page, "Y", "Z", "after");

    await expect(page.getByTestId("banner")).toHaveAttribute("data-kind", "saved");
    // The executed move triggers an async refetch + re-render; poll the visible
    // row order until Z precedes Y (a one-shot read can race the refetch).
    await expect
      .poll(async () => {
        const order = await page
          .getByTestId(/^row-/)
          .evaluateAll((els) => els.map((e) => e.getAttribute("data-testid")));
        return order.indexOf("row-Z") < order.indexOf("row-Y");
      })
      .toBe(true);
  });

  test("A moves X into P2 (authority only at source) → suggested to D, co-approver A; tree reverts (WEB_UI-041)", async ({ page }) => {
    await loginAs(page, "A"); // A owns R/P1/X but not P2 (D owns dest)
    await openSheet(page);

    const xDepthBefore = await row(page, "X").getAttribute("data-depth");
    await dragRowOnto(page, "X", "P2", "inside");

    const banner = page.getByTestId("banner");
    await expect(banner).toHaveAttribute("data-kind", "suggested");
    // approver/co-approver are Frappe user ids (D → d@arbor.example, A → a@arbor.example)
    await expect(banner).toContainText("Suggestion sent to d@arbor.example");
    await expect(banner).toContainText("co-approver: a@arbor.example");
    await expect(page.getByTestId("banner-cr")).toBeVisible();
    // layout reverted: X stays at its original depth under P1
    await expect(row(page, "X")).toHaveAttribute("data-depth", xDepthBefore ?? "2");
  });
});
