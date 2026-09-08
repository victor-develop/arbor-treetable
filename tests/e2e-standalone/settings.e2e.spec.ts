import { test, expect } from "@playwright/test";

// Settings had NO e2e coverage, which is exactly why a render-phase crash in it
// reached production: every other lane (vitest, tsc, the smoke journey) is happy
// while the modal throws into its ErrorBoundary on open.
test("the Sheet Settings modal opens without crashing", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(e.stack || e.message));

  await page.goto("/");
  await page.getByTestId("login-username").fill("owner@example.com");
  await page.getByTestId("login-password").fill("x");
  await page.getByTestId("login-submit").click();

  const name = `settings-${Date.now().toString(36)}`;
  const nameInput = page.getByTestId("new-sheet-name");
  await nameInput.fill(name);
  await nameInput.press("Enter");
  await expect(page).toHaveURL(new RegExp(`sheet=${name}`));

  // A row FIRST (an empty sheet renders no header, so no ghost opener), then a
  // column, so every tab has something to render.
  await page.getByRole("button", { name: /add node/i }).click();
  await expect(page.getByText("No nodes yet.")).toHaveCount(0);
  await page.getByTestId("ghost-col-open-label").hover();
  await page.getByTestId("ghost-col-open-label").click();
  const ghost = page.getByTestId("ghost-col-input");
  await ghost.fill("Notes");
  await ghost.press("Enter");
  await expect(page.getByRole("columnheader", { name: /notes/i })).toBeVisible();

  // The creator is the structural owner, so the gear renders.
  await page.getByTestId("sheet-settings-button").click();

  // The boundary must NOT catch anything, and the panel must actually render.
  await expect(page.getByTestId("error-boundary-sheet-settings")).toHaveCount(0);
  await expect(page.getByTestId("sheet-settings-modal")).toBeVisible();
  // The seed read must actually land — an open modal whose body never arrives is
  // exactly what a user calls "crashed".
  await expect(page.getByTestId("settings-columns")).toBeVisible();
  await expect(page.getByTestId("settings-loading")).toHaveCount(0);
  await expect(page.getByTestId("settings-error")).toHaveCount(0);
  // Every tab opens without throwing.
  for (const tab of ["Flow", "Delivery"]) {
    await page.getByRole("tab", { name: tab }).click();
    await expect(page.getByTestId("error-boundary-sheet-settings")).toHaveCount(0);
  }

  // Nothing may have thrown: a render-phase crash is caught by the boundary and
  // would otherwise only show up as a red banner nobody asserted on.
  expect(pageErrors).toEqual([]);
});
