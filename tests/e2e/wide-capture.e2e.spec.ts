// Capture the WIDE "Product Feature Matrix" sheet (16 columns, several
// paragraph-length, 5 role-owners, 29 nodes) at DESKTOP and MOBILE viewports,
// for the information-density UX review. Seed first: demo/wide/seed.py.
//
// Personas come from /tmp/wide_keys.json (PM sees everything). No reset.

import { readFileSync } from "node:fs";
import { test, expect, type Page } from "@playwright/test";

const KEYS: Record<string, string> = JSON.parse(readFileSync("/tmp/wide_keys.json", "utf8"));
const SHEET = "WIDE";
const DIR = "docs/demo/wide";

async function loginAs(page: Page, role: string): Promise<void> {
  await page.addInitScript((token) => {
    (window as unknown as { __ARBOR_AUTH__?: string }).__ARBOR_AUTH__ = token;
  }, `token ${KEYS[role]}`);
}

async function open(page: Page): Promise<void> {
  await page.goto(`/?sheet=${SHEET}`);
  await expect(page.getByTestId("tree-table")).toBeVisible();
}

test("desktop — wide matrix (1440×900)", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginAs(page, "PM");
  await open(page);
  // All 15 data columns render (16 incl. the frozen FEATURE label column) and
  // the table OVERFLOWS its viewport horizontally (so scroll, not clip — D1).
  await expect(page.locator('[data-testid^="col-head-"]')).toHaveCount(15);
  const overflow = await page
    .getByTestId("table-viewport")
    .evaluate((el) => el.scrollWidth > el.clientWidth + 8);
  expect(overflow).toBe(true);
  await page.screenshot({ path: `${DIR}/desktop-1440.png` });
  await page.screenshot({ path: `${DIR}/desktop-1440-full.png`, fullPage: true });
});

test("desktop wide — large monitor (1920×1080)", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await loginAs(page, "PM");
  await open(page);
  await page.screenshot({ path: `${DIR}/desktop-1920.png` });
});

test("desktop — scrolled right (far columns reachable, FEATURE frozen)", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginAs(page, "PM");
  await open(page);
  // Scroll the horizontal viewport to the end — proves all 16 columns are
  // reachable and the FEATURE column stays frozen on the left.
  await page.getByTestId("table-viewport").evaluate((el) => {
    el.scrollLeft = el.scrollWidth;
  });
  await page.waitForTimeout(150);
  // The FEATURE label column is frozen: its first label cell stays in the
  // viewport even after scrolling to the far-right columns (D4).
  await expect(page.locator(".arbor-label").first()).toBeInViewport();
  await page.screenshot({ path: `${DIR}/desktop-1440-scrolled.png` });
});

test("mobile — wide matrix (390×844, iPhone-ish)", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAs(page, "PM");
  await open(page);
  await page.screenshot({ path: `${DIR}/mobile-390.png` });
  await page.screenshot({ path: `${DIR}/mobile-390-full.png`, fullPage: true });
  // Table owns the screen by default (agent rail NOT open), then the FAB opens
  // the agent drawer — proves the agent is reachable without covering the table.
  const rail = page.locator(".arbor-rail");
  await expect(rail).not.toHaveClass(/is-open/);
  await page.getByTestId("agent-fab").click();
  await expect(rail).toHaveClass(/is-open/);
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${DIR}/mobile-390-agent-open.png` });
});
