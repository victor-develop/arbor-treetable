// Saved views (Feature: saved views) — the whole point of the feature end to
// end: an arrangement saved on one page load is still there on the next.
//
// A component test cannot prove this. The server has to hold the view, the
// listing has to come back on a FRESH page load, and — the rule that makes the
// feature safe — nothing may be applied until the user picks it. So the journey
// deliberately returns to a CLEAN url (no ?v= token, which the shell otherwise
// keeps in sync) and asserts the default arrangement is back before applying the
// saved one.

import { expect, test } from "@playwright/test";

const USER = "views.e2e@example.com";

test("saves the current arrangement, survives a reload, and applies on demand", async ({ page }) => {
  // 1. Sign in and create a sheet with two data columns.
  await page.goto("/");
  await page.getByTestId("login-username").fill(USER);
  await page.getByTestId("login-password").fill("anything");
  await page.getByTestId("login-submit").click();
  await expect(page.getByRole("heading", { name: "Arbor" })).toBeVisible();

  const sheet = `views-${Date.now().toString(36)}`;
  const nameInput = page.getByTestId("new-sheet-name");
  await nameInput.fill(sheet);
  await nameInput.press("Enter");
  await expect(page).toHaveURL(new RegExp(`sheet=${sheet}`));

  // An EMPTY sheet shows only the add-node affordance (the column header "+"
  // lives on the grid), so the first row has to exist before columns can be
  // quick-added.
  await page.getByRole("button", { name: /add node/i }).click();
  await expect(page.getByText("No nodes yet.")).toHaveCount(0);

  const quickAdd = async (opener: string, label: string) => {
    await page.getByTestId(opener).hover();
    await page.getByTestId(opener).click();
    const ghost = page.getByTestId("ghost-col-input");
    await expect(ghost).toBeVisible();
    await ghost.fill(label);
    await ghost.press("Enter");
  };
  const dataHeaders = () => page.locator("table.arbor-tree thead th .arbor-col-head");

  await quickAdd("ghost-col-open-label", "Due Date");
  await expect(dataHeaders()).toHaveText(["Due Date"]);
  await quickAdd("ghost-col-open-due_date", "Owner");
  await expect(dataHeaders()).toHaveText(["Due Date", "Owner"]);

  // 2. Hide one column through the presentation-only View menu.
  await page.getByTestId("view-disclosure").getByText("View", { exact: true }).click();
  // Addressed by its accessible name, not a testid: the ViewMenu row's testid
  // carries the column's stored id (a random docname here), not its field slug.
  await page.getByRole("button", { name: /^Owner — / }).click();
  await expect(dataHeaders()).toHaveText(["Due Date"]);

  // The disclosure is opened by its own <summary> — the panel below repeats the
  // words "Saved views" in its copy, so a text locator would be ambiguous.
  // 3. Save that arrangement by name. A save is PRIVATE — the row lands under
  //    "My views" and is badged as shared only after an explicit publish.
  await page.locator('[data-testid="saved-views-disclosure"] > summary').click();
  const menu = page.getByTestId("saved-views-menu");
  await expect(menu).toBeVisible();
  await menu.getByTestId("saved-views-name").fill("Due only");
  await menu.getByTestId("saved-views-save").click();
  await expect(menu.getByText("Due only")).toBeVisible();
  await expect(menu.getByTestId("saved-views-mine-empty")).toHaveCount(0);

  // 4. Reload at a CLEAN url — no ?v= token, so nothing but the server can
  //    remember the arrangement. The default view must be back (proof that a
  //    saved view is NOT auto-applied) …
  await page.goto(`/?sheet=${sheet}`);
  await expect(dataHeaders()).toHaveText(["Due Date", "Owner"]);

  // 5. … and the saved view must still be listed, from the server.
  await page.locator('[data-testid="saved-views-disclosure"] > summary').click();
  const reloaded = page.getByTestId("saved-views-menu");
  const row = reloaded.getByText("Due only");
  await expect(row).toBeVisible();

  // 6. Applying it restores the arrangement.
  await row.click();
  await expect(dataHeaders()).toHaveText(["Due Date"]);

  // 7. Publishing flips the row to shared (the second, explicit step) …
  await reloaded.getByRole("button", { name: /^Publish Due only/ }).click();
  await expect(reloaded.getByText("shared")).toBeVisible();

  // 8. … and deleting takes the two-step confirm, after which it is gone for
  //    good — including across another reload.
  await reloaded.getByRole("button", { name: /^Delete Due only/ }).click();
  await reloaded.getByText("Confirm delete").click();
  await expect(reloaded.getByTestId("saved-views-mine-empty")).toBeVisible();

  await page.goto(`/?sheet=${sheet}`);
  await page.locator('[data-testid="saved-views-disclosure"] > summary').click();
  await expect(
    page.getByTestId("saved-views-menu").getByTestId("saved-views-mine-empty"),
  ).toBeVisible();
});
