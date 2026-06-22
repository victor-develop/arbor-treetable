// Runnable status: NEEDS A RUNNING APP (Vite frontend + Frappe backend with the
// canonical seed). See ./README.md.
//
// Closes the TEST-PLAN §5.1 surface-parity gap: `subscribe` was exercised on
// every surface but `unsubscribe` was missing from the Web UI. This drives the
// full subscribe → unsubscribe lifecycle through the real capability API so the
// pair is symmetric and the §11 surface-parity guarantee holds for both halves.
// The Web UI calls one capability each (subscribe / unsubscribe) — both real
// registry capabilities, never an ad-hoc write.
//
// Cases: WEB_UI-091 (Web-UI subscribe), WEB_UI-092 (Web-UI unsubscribe — the gap).

import { test, expect } from "@playwright/test";
import { loginAs, openSheet } from "./fixtures";

test.describe("subscribe / unsubscribe Web-UI parity (TEST-PLAN §5.1, e2e)", () => {
  test("a viewer can subscribe then unsubscribe via the UI (WEB_UI-091/-092)", async ({ page }) => {
    // F is a plain member with no edit authority — a natural subscriber.
    await loginAs(page, "F");
    await openSheet(page);

    const control = page.getByTestId("subscription-control");
    await expect(control).toBeVisible();

    // Subscribe (WEB_UI-091) — control flips to the subscribed state.
    if ((await control.getAttribute("data-subscribed")) === "false") {
      await page.getByTestId("subscribe-btn").click();
      await expect(control).toHaveAttribute("data-subscribed", "true");
    }

    // Unsubscribe (WEB_UI-092 — the §5.1 gap) — control flips back.
    await expect(page.getByTestId("unsubscribe-btn")).toBeVisible();
    await page.getByTestId("unsubscribe-btn").click();
    await expect(control).toHaveAttribute("data-subscribed", "false");
    await expect(page.getByTestId("subscribe-btn")).toBeVisible();
  });
});
