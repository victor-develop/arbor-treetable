// Standalone-adapter smoke journey — the FE↔BE seams a curl test can't see.
//
// Regression anchor for two real production bugs (2026-08-29):
// 1. the home page rendered for GUESTS, so "create sheet" 401'd behind a
//    misleading duplicate-name error (no auth gate on the home route);
// 2. <LoginScreen> POSTs the frappe-native /api/method/login, which the
//    standalone backend didn't serve — the login screen couldn't log in.
//
// Journey: guest → auth gate shows LoginScreen → sign in (dev mode: email as
// username, any password) → sheet list renders → create a sheet → the grid
// route loads with the creator as owner (can add columns).

import { expect, test } from "@playwright/test";

const USER = "victor.e2e@example.com";

test("guest is gated, signs in, creates a sheet, lands in the grid", async ({ page }) => {
  // 1. Guest: the home route must NOT render the sheet-list UI.
  await page.goto("/");
  await expect(page.getByTestId("login-screen")).toBeVisible();

  // 2. Sign in through the real LoginScreen (frappe-compat /api/method/login).
  await page.getByTestId("login-username").fill(USER);
  await page.getByTestId("login-password").fill("anything");
  await page.getByTestId("login-submit").click();

  // 3. Authenticated: the sheet list replaces the gate.
  await expect(page.getByRole("heading", { name: "Arbor" })).toBeVisible();
  await expect(page.getByTestId("login-screen")).toHaveCount(0);

  // 4. Create a sheet via the new-sheet form.
  const name = `e2e-${Date.now().toString(36)}`;
  const nameInput = page.getByTestId("new-sheet-name");
  await nameInput.fill(name);
  await nameInput.press("Enter");

  // 5. The app navigates to ?sheet=<name> and the grid mounts; the creator owns
  //    the sheet, so the OWNER affordances (Add column / Add node) render.
  await expect(page).toHaveURL(new RegExp(`sheet=${name}`));
  await expect(page.getByText("No nodes yet.")).toBeVisible();
  await expect(page.getByRole("button", { name: /add column/i })).toBeVisible();

  // 6. Add the first row — the empty state yields to the grid (a governed write
  //    that must come back "executed" for the owner, not "suggested").
  await page.getByRole("button", { name: /add node/i }).click();
  await expect(page.getByText("No nodes yet.")).toHaveCount(0);
});

test("a wrong login shows an inline error, not a white screen", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("login-screen")).toBeVisible();
  await page.getByTestId("login-username").fill("not an email");
  await page.getByTestId("login-password").fill("x");
  await page.getByTestId("login-submit").click();
  // Standalone rejects a non-email usr with 401 → the inline error renders.
  await expect(page.getByTestId("login-error")).toBeVisible();
  await expect(page.getByTestId("login-screen")).toBeVisible();
});
