// Per-cell pending-suggestion marker is SERVER-sourced, so it (a) survives a
// refresh for the suggester and (b) is visible to OTHER viewers — the exact
// "Dev A suggested, Dev B should see it" requirement. Drives the seeded ECOM
// sheet: DEV suggests an edit on a PM-owned Definition cell (→ Change Request),
// then a THIRD party (MKT) and the suggester (DEV, after reload) both see the
// pending marker on that cell. No reset_e2e (uses the ECOM seed as-is).

import { readFileSync } from "node:fs";
import { test, expect, type Page, type Locator } from "@playwright/test";

const KEYS: Record<string, string> = JSON.parse(readFileSync("/tmp/ecom_keys.json", "utf8"));
const SHEET = "ECOM";
const API_BASE = process.env.ARBOR_E2E_API_BASE ?? "http://localhost:8000";

async function loginAs(page: Page, role: "PM" | "DEV" | "MKT"): Promise<void> {
  await page.addInitScript((token) => {
    (window as unknown as { __ARBOR_AUTH__?: string }).__ARBOR_AUTH__ = token;
  }, `token ${KEYS[role]}`);
}

async function suggestAs(role: "PM" | "DEV" | "MKT", params: Record<string, unknown>) {
  const res = await fetch(`${API_BASE}/api/method/arbor.execute_action`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `token ${KEYS[role]}` },
    body: JSON.stringify({ action_id: "updateCell", params }),
  });
  if (!res.ok) throw new Error(`updateCell ${res.status} ${await res.text()}`);
  return (await res.json()).message;
}

function defCell(page: Page, label: string): Locator {
  return page
    .locator("tr.arbor-row")
    .filter({ has: page.locator(".arbor-label").filter({ hasText: new RegExp(`^${label}$`) }) })
    .locator(`[data-column="col:definition"] [data-testid="cell"]`);
}

test.use({ viewport: { width: 1440, height: 900 } });

test.describe.serial("per-cell pending suggestion visible across users", () => {
  test("DEV suggests on a PM Definition → routed to a Change Request", async () => {
    // DEV does not own col:definition (PM does) → this is a suggestion, not a write.
    const out = await suggestAs("DEV", {
      sheet: SHEET,
      node: "Search",
      column: "col:definition",
      value: "DEV proposes: full-text search across catalog + synonyms",
    });
    expect(out.kind).toBe("suggested");
    expect(out.change_request).toBeTruthy();
  });

  test("MKT (a third party) sees the pending marker on that cell", async ({ page }) => {
    await loginAs(page, "MKT");
    await page.goto(`/?sheet=${SHEET}`);
    await expect(page.getByTestId("tree-table")).toBeVisible();
    const cell = defCell(page, "Search");
    await expect(cell).toHaveAttribute("data-pending", "true");
    await expect(cell.getByTestId("pending-marker")).toBeVisible();
    // tooltip carries requester + proposed value (server-sourced mark)
    await expect(cell.getByTestId("pending-marker")).toHaveAttribute(
      "title",
      /dev@arbor\.example/,
    );
    await page.screenshot({ path: "docs/demo/ecom/12-pending-visible-to-others.png" });
  });

  test("the marker SURVIVES a refresh for the suggester (DEV)", async ({ page }) => {
    await loginAs(page, "DEV");
    await page.goto(`/?sheet=${SHEET}`);
    await expect(defCell(page, "Search")).toHaveAttribute("data-pending", "true");
    await page.reload();
    await expect(page.getByTestId("tree-table")).toBeVisible();
    await expect(defCell(page, "Search").getByTestId("pending-marker")).toBeVisible();
  });

  test("a SECOND suggester aggregates → the marker shows a count badge (2)", async ({
    page,
  }) => {
    // MKT also suggests on the SAME PM-owned cell → two open CRs on one cell.
    const out = await suggestAs("MKT", {
      sheet: SHEET,
      node: "Search",
      column: "col:definition",
      value: "MKT proposes: align with the Search marketing landing page",
    });
    expect(out.kind).toBe("suggested");
    // Any viewer sees the aggregated count, not just one suggestion.
    await loginAs(page, "PM");
    await page.goto(`/?sheet=${SHEET}`);
    const marker = defCell(page, "Search").getByTestId("pending-marker");
    await expect(marker).toHaveAttribute("data-count", "2");
    await expect(marker).toHaveText("2");
    await expect(marker).toHaveAttribute(
      "title",
      /2 pending.*dev@arbor\.example.*marketing@arbor\.example/s,
    );
    await page.screenshot({ path: "docs/demo/ecom/13-multi-pending-badge.png" });
  });
});
