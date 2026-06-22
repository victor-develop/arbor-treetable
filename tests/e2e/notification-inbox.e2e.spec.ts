// Runnable status: NEEDS A RUNNING APP. See ./README.md.
//
// Closes a built-but-unwired gap surfaced by the unwired-surface audit: the
// NotificationItem + acknowledge capability were built + tested but had no
// delivery surface (no list endpoint, never mounted). The notification inbox now
// fetches arbor.list_notifications and dispatches acknowledge from the UI.
//
// Arrange (via API, the parity surface): G subscribes to the sheet with
// requires_ack; A proposes a change (CHANGE_PROPOSED) so the dispatcher files a
// requires_ack notification to the sheet subscriber G. Assert (via UI): G sees
// the notification in the inbox and acknowledges it.
//
// Cases: WEB_UI-090 (acknowledge a requires_ack notification from the Web UI).

import { test, expect, type APIRequestContext } from "@playwright/test";
import { loginAs, resetSeed, openGovernanceTab, SHEET } from "./fixtures";

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

test.describe("notification inbox (e2e)", () => {
  test("a subscriber sees a requires_ack notification and acknowledges it (WEB_UI-090)", async ({
    page,
    request,
  }) => {
    await resetSeed();

    // G subscribes to the sheet with acknowledgement required.
    await call(request, "G", "arbor.subscribe", {
      scope: "sheet",
      target: SHEET,
      event_types: ["CHANGE_PROPOSED", "CHANGE_APPROVED"],
      delivery: "in-app",
      requires_ack: 1,
    });

    // A (no authority on col:budget) proposes a change → CHANGE_PROPOSED → the
    // dispatcher files a requires_ack notification to the subscriber G.
    await call(request, "A", "arbor.execute_action", {
      action_id: "updateCell",
      params: { sheet: SHEET, node: "X", column: "col:budget", value: 777 },
    });

    // G opens the sheet (no re-seed) and sees the notification in the inbox.
    await loginAs(page, "G");
    await page.goto(`/?sheet=${encodeURIComponent(SHEET)}`);
    await expect(page.getByTestId("tree-table")).toBeVisible();

    // Notifications live behind the Governance panel's "Notifications" tab now.
    await openGovernanceTab(page, "Notifications");

    const inbox = page.getByTestId("notification-inbox");
    await expect(inbox).toBeVisible();
    const item = inbox.locator('[data-testid^="notification-"]').first();
    await expect(item).toBeVisible();

    // Acknowledge it → the affordance flips to the acknowledged state.
    await item.getByTestId("ack-btn").click();
    await expect(item.getByTestId("ack-state")).toBeVisible();
  });
});
