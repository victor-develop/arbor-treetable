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
  // (Column-add now lives on the grid itself — hover "+" on the last header —
  //  so an empty sheet shows only the Add-node affordance.)

  // 6. Add the first row — the empty state yields to the grid (a governed write
  //    that must come back "executed" for the owner, not "suggested").
  await page.getByRole("button", { name: /add node/i }).click();
  await expect(page.getByText("No nodes yet.")).toHaveCount(0);

  // 6b. Quick-add a column via the ghost flow: hovering a column header reveals
  //     a "+"; clicking it opens the transient inline editor and a label-only
  //     Enter creates the column with defaults (field slug, type text, owner =
  //     creator). No blank column is reserved while idle. With no data columns
  //     yet, the label header's "+" is the entry point and appends.
  const quickAdd = async (opener: string, label: string) => {
    await page.getByTestId(opener).hover();
    await page.getByTestId(opener).click();
    const ghostInput = page.getByTestId("ghost-col-input");
    await expect(ghostInput).toBeVisible();
    await ghostInput.fill(label);
    await ghostInput.press("Enter");
  };
  // Data column labels in rendered left-to-right order (the label column's
  // header carries no .arbor-col-head span, so this is the data axis only).
  const dataHeaders = () => page.locator("table.arbor-tree thead th .arbor-col-head");

  await quickAdd("ghost-col-open-label", "Due Date");
  await expect(dataHeaders()).toHaveText(["Due Date"]);
  await quickAdd("ghost-col-open-due_date", "Owner");
  await expect(dataHeaders()).toHaveText(["Due Date", "Owner"]);

  // 6c. Insert BETWEEN the two: the "+" on the FIRST data column's header means
  //     "insert to the right of THIS one", so the new column must land second —
  //     and a reload proves the order came back from the SERVER (one stored
  //     order everyone reads), not from local state.
  await quickAdd("ghost-col-open-due_date", "Stage");
  await expect(dataHeaders()).toHaveText(["Due Date", "Stage", "Owner"]);
  await page.reload();
  await expect(dataHeaders()).toHaveText(["Due Date", "Stage", "Owner"]);

  // 6d. Rearrange MY view by dragging a column header's grip, then promote that
  //     arrangement to the SHARED stored order. The drag alone is presentation
  //     state (no round-trip); "Save order for everyone" is the one governed
  //     write. Drag "Owner" onto "Due Date" → Owner leads.
  await page.getByTestId("col-grip-due_date").hover();
  await page.getByTestId("col-grip-owner").dragTo(page.getByTestId("col-grip-due_date"));
  await expect(dataHeaders()).toHaveText(["Owner", "Due Date", "Stage"]);

  await page.getByTestId("view-disclosure").getByText("View").click();
  await page.getByTestId("view-save-order").click();
  await expect(page.getByTestId("banner")).toContainText("Saved");

  //     The proof: reload WITHOUT the ?v= token. A local override lives in that
  //     token (and would survive a plain reload), so dropping it is what makes
  //     this an assertion about the SERVER's stored order.
  await page.goto(`/?sheet=${name}`);
  await expect(dataHeaders()).toHaveText(["Owner", "Due Date", "Stage"]);

  // 7. Toggle Live (the app lands in Proposed) — the editable grid must render,
  //    not the tree-table error boundary. Regression: a loosely-shaped select
  //    options value crashed exactly this view (flattenOptions on undefined groups).
  await page.getByRole("button", { name: "Live", exact: true }).click();
  await expect(page.getByTestId("error-boundary-tree-table")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /add node/i })).toBeVisible();
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
