// Runnable status: NEEDS A RUNNING APP (Vite frontend + Frappe backend with the
// canonical seed). See ./README.md. Config-gated like every *.e2e.spec.ts — the
// Playwright runner collects it (testMatch /.*\.e2e\.spec\.ts/); it is never run by
// vitest/pytest and drives a real browser, so there is no live server here.
//
// The notification-webhooks admin journey (Feature: webhooks, Area 3, WS-A3c): a
// sheet admin / structural owner opens the header-launched Webhooks modal,
// registers an endpoint for a set of notification sources, sees the write-once
// signing secret surfaced ONCE, confirms the endpoint is listed, then deletes it.
// The modal reuses the shared .arbor-modal shell and funnels every write through
// the admin-gated + SSRF-validated register/list/delete shims (the server is the
// authority; this shell re-derives no authority).
//
// Cases: WEB_UI webhooks register + list + delete (e2e).

import { test, expect, type Page } from "@playwright/test";
import { loginAs, openSheet } from "./fixtures";

// A public, non-loopback URL that passes the server SSRF deny-list (8.8.8.8 is used
// by the backend SSRF bench as an allowed public host).
const HOOK_URL = "https://8.8.8.8/arbor-e2e";

async function openWebhooks(page: Page): Promise<void> {
  await page.getByTestId("webhook-config-button").click();
  await expect(page.getByTestId("webhook-modal")).toBeVisible();
}

test.describe("notification webhooks panel (e2e)", () => {
  test("owner registers a webhook, sees the write-once secret, then deletes it", async ({
    page,
  }) => {
    // A is the sheet structural owner — the Webhooks button mounts on the same gate
    // as Process (canConfigProcess).
    await loginAs(page, "A");
    await openSheet(page);
    await openWebhooks(page);

    // Empty state before any registration.
    await expect(page.getByTestId("webhook-empty")).toBeVisible();

    // Fill the register form: URL + a label + two notification sources.
    await page.getByTestId("webhook-url").fill(HOOK_URL);
    await page.getByTestId("webhook-label").fill("CI receiver");
    // "process" is checked by default; add "sla".
    await page.getByTestId("webhook-source-sla").check();
    await expect(page.getByTestId("webhook-source-process")).toBeChecked();
    await expect(page.getByTestId("webhook-source-sla")).toBeChecked();

    await page.getByTestId("webhook-register").click();

    // The signing secret is surfaced ONCE right after register.
    const secret = page.getByTestId("webhook-secret");
    await expect(secret).toBeVisible();
    await expect(secret.locator("code")).not.toBeEmpty();

    // No SSRF/admin error surfaced for the valid public URL.
    await expect(page.getByTestId("webhook-error")).toHaveCount(0);

    // The endpoint is now listed (label rendered, sources tagged), empty-state gone.
    const list = page.getByTestId("webhook-list");
    await expect(list).toBeVisible();
    await expect(page.getByTestId("webhook-empty")).toHaveCount(0);
    const row = list.locator('[data-testid^="webhook-row-"]').first();
    await expect(row).toBeVisible();
    await expect(row).toContainText("CI receiver");

    // Delete it → the row drops out and the empty-state returns.
    await row.locator('[data-testid^="webhook-delete-"]').click();
    await expect(list.locator('[data-testid^="webhook-row-"]')).toHaveCount(0);
    await expect(page.getByTestId("webhook-empty")).toBeVisible();

    // Close the modal.
    await page.getByTestId("webhook-close").click();
    await expect(page.getByTestId("webhook-modal")).toHaveCount(0);
  });

  test("a loopback URL is rejected by the server SSRF guard and surfaces an inline error", async ({
    page,
  }) => {
    await loginAs(page, "A");
    await openSheet(page);
    await openWebhooks(page);

    // A loopback URL must be rejected server-side; the panel surfaces the error
    // inline (aria-live) and registers nothing.
    await page.getByTestId("webhook-url").fill("http://127.0.0.1/evil");
    await page.getByTestId("webhook-register").click();

    await expect(page.getByTestId("webhook-error")).toBeVisible();
    // Nothing was registered — the empty-state remains.
    await expect(page.getByTestId("webhook-empty")).toBeVisible();
  });
});
