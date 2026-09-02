// Standalone-adapter ADMIN journey — the platform-admin console (Admin modal)
// over the arbor.admin.* face, end to end through the real UI.
//
// Covers the seams a unit test can't: the ARBOR_ADMIN_EMAILS bootstrap actually
// surfaces as viewer.is_admin (the header "Roles" button), the Catalog tab's
// create form round-trips arbor.admin.create_role into the shared role list,
// and the Users tab lists real accounts + persists a set_user toggle — while
// the admin's OWN row stays inert (mirror of the server self-guard).
//
// Journey: seed a second account via the frappe-compat login API → sign in as
// the bootstrap admin → create a sheet (the admin header controls live in the
// sheet view) → open the Admin modal → Catalog: create "e2e-role" and see it
// listed → Users: both accounts present, own-row toggles disabled → promote
// the member and watch the toggle reflect the persisted flag.

import { expect, test } from "@playwright/test";

const ADMIN = "admin@e2e.local"; // ARBOR_ADMIN_EMAILS bootstrap (playwright.config.ts)
const MEMBER = "member@e2e.local";

test("admin signs in, creates a role in the Catalog, manages users", async ({
  page,
  playwright,
  baseURL,
}) => {
  // 0. Seed the second account server-side: dev-login auto-creates the users
  //    row on first sight, so one API login in a throwaway context is enough
  //    (no second browser needed — we never act AS the member here).
  const seed = await playwright.request.newContext({ baseURL });
  const seeded = await seed.post("/api/method/login", {
    data: { usr: MEMBER, pwd: "anything" },
  });
  expect(seeded.ok()).toBeTruthy();
  await seed.dispose();

  // 1. Sign in as the bootstrap admin through the real LoginScreen.
  await page.goto("/");
  await expect(page.getByTestId("login-screen")).toBeVisible();
  await page.getByTestId("login-username").fill(ADMIN);
  await page.getByTestId("login-password").fill("anything");
  await page.getByTestId("login-submit").click();
  await expect(page.getByRole("heading", { name: "Arbor" })).toBeVisible();

  // 2. The admin header controls (incl. the Roles button) render in the SHEET
  //    view — create a sheet to get there.
  const sheetName = `e2e-admin-${Date.now().toString(36)}`;
  const nameInput = page.getByTestId("new-sheet-name");
  await nameInput.fill(sheetName);
  await nameInput.press("Enter");
  await expect(page).toHaveURL(new RegExp(`sheet=${sheetName}`));

  // 3. viewer.is_admin (from the ARBOR_ADMIN_EMAILS bootstrap) surfaces as the
  //    admin-only header button; it opens the Admin modal.
  const rolesButton = page.getByTestId("roles-admin-button");
  await expect(rolesButton).toBeVisible();
  await rolesButton.click();
  await expect(page.getByTestId("roles-modal")).toBeVisible();

  // 4. Catalog tab: create a role and see it appear in the shared role list
  //    (create_role → refreshRoles round-trip).
  await page.getByTestId("admin-tab-catalog").click();
  await page.getByTestId("role-catalog-key").fill("e2e-role");
  await page.getByTestId("role-catalog-label").fill("E2E Role");
  await page.getByTestId("role-catalog-create-submit").click();
  await expect(page.getByTestId("role-catalog-row-e2e-role")).toBeVisible();
  await expect(page.getByTestId("role-catalog-row-e2e-role")).toContainText("E2E Role");

  // 5. Users tab: BOTH accounts appear (list_users is the real account table,
  //    not just the current session).
  await page.getByTestId("admin-tab-users").click();
  await expect(page.getByTestId(`admin-users-row-${ADMIN}`)).toBeVisible();
  await expect(page.getByTestId(`admin-users-row-${MEMBER}`)).toBeVisible();

  // 6. The admin's OWN row is inert — the UI mirrors the server self-guard
  //    (an admin can never demote or disable themselves).
  await expect(page.getByTestId(`admin-users-admin-${ADMIN}`)).toBeDisabled();
  await expect(page.getByTestId(`admin-users-enabled-${ADMIN}`)).toBeDisabled();

  // 7. Promote the member. The checkbox is CONTROLLED (checked = server state),
  //    so it only sticks once set_user persisted and refreshUsers round-tripped.
  const memberAdminToggle = page.getByTestId(`admin-users-admin-${MEMBER}`);
  await expect(memberAdminToggle).toBeEnabled();
  await expect(memberAdminToggle).not.toBeChecked();
  await memberAdminToggle.click();
  await expect(memberAdminToggle).toBeChecked();

  // 8. Belt-and-braces: the flag survives a full close → reopen (fresh
  //    list_users fetch), proving persistence rather than local echo.
  await page.getByTestId("roles-modal-close").click();
  await expect(page.getByTestId("roles-modal")).toHaveCount(0);
  await rolesButton.click();
  await page.getByTestId("admin-tab-users").click();
  await expect(page.getByTestId(`admin-users-admin-${MEMBER}`)).toBeChecked();
});
