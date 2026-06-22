// Runnable status: NEEDS A RUNNING APP. See ./README.md.
//
// Closes a built-but-unwired gap surfaced by the unwired-surface audit: the
// delegateBranch / revokeDelegation capabilities were backend + agent reachable
// but had no Web-UI surface (no way to see, grant, or revoke a branch
// delegation). The DelegationControl now lists active grants (from the snapshot)
// and dispatches delegate / revoke through executeAction.
//
// Cases: WEB_UI-084 (delegate a branch + revoke from the Web UI).

import { test, expect } from "@playwright/test";
import { loginAs, openSheet, openGovernanceTab } from "./fixtures";

test.describe("branch delegation control (e2e)", () => {
  test("the sheet owner delegates a branch then revokes it (WEB_UI-084)", async ({ page }) => {
    // A is the sheet structural owner: owns R/P1/X and may revoke the seeded
    // P2 → D grant. The seed already has one active grant (P2 → d@arbor.example).
    await loginAs(page, "A");
    await openSheet(page);

    // DelegationControl lives behind the Governance panel's "Delegations" tab.
    await openGovernanceTab(page, "Delegations");

    const control = page.getByTestId("delegation-control");
    await expect(control).toBeVisible();
    // Seeded grant is listed and revocable by A.
    await expect(control.locator('[data-grantee="d@arbor.example"]')).toBeVisible();

    // Delegate the P1 branch to F → executed; a new grant appears for f@.
    await control.getByTestId("delegate-branch").selectOption("P1");
    await control.getByTestId("delegate-grantee").fill("f@arbor.example");
    await control.getByTestId("delegate-submit").click();

    const newGrant = control.locator('[data-grantee="f@arbor.example"]');
    await expect(newGrant).toBeVisible();

    // Revoke the new grant → it drops out of the list.
    await newGrant.getByRole("button", { name: "Revoke" }).click();
    await expect(control.locator('[data-grantee="f@arbor.example"]')).toHaveCount(0);
  });
});
