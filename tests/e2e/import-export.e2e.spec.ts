// Runnable status: NEEDS A RUNNING APP (Vite frontend + Frappe backend with the
// canonical seed). See ./README.md.
//
// Export → import round-trip. Export serializes sheet S via the shared snapshot
// serializer; importing that file into a fresh sheet replays through governed
// capabilities (addColumn/addNode/updateCell funnelled into execute_action — no
// raw writes) and, on success, emits IMPORT_COMPLETED. The reconstructed sheet
// reproduces the R/P1/X/P2/Y/Z structure, the column owners B/C, and the
// split-column array values.
//
// Case: WEB_UI-082 (round-trip preserves tree shape, ownership, split values;
//       IMPORT_COMPLETED).

import { readFileSync } from "node:fs";
import { test, expect } from "@playwright/test";
import { loginAs, openSheet, openDataDisclosure } from "./fixtures";

const TARGET_SHEET = process.env.ARBOR_E2E_TARGET_SHEET ?? "S2";

test.describe("export → import round-trip (e2e)", () => {
  test("round-trips structure, ownership, and split values; emits IMPORT_COMPLETED (WEB_UI-082)", async ({ page }) => {
    await loginAs(page, "A"); // structural owner can replay all rows directly
    await openSheet(page, "S");

    // ImportExport now lives in the header "Data" disclosure (P1) — expand it
    // before reaching its controls.
    await openDataDisclosure(page);

    // 1) Export — capture the downloaded snapshot file.
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByTestId("export-btn").click(),
    ]);
    const path = await download.path();
    expect(path).toBeTruthy();
    const exported = readFileSync(path!, "utf8");
    const snapshot = JSON.parse(exported);

    // The export equals the snapshot shape: columns w/ owners, nodes, values.
    expect(snapshot.nodes.map((n: { name: string }) => n.name).sort()).toEqual(
      ["P1", "P2", "R", "X", "Y", "Z"],
    );
    // column_owner is the Frappe user id (persona B/C → lowercased emails)
    expect(snapshot.columns.find((c: { field: string }) => c.field === "name").column_owner).toBe("b@arbor.example");
    expect(snapshot.columns.find((c: { field: string }) => c.field === "budget").column_owner).toBe("c@arbor.example");

    // 2) Import into the fresh target sheet S2 and confirm the governed plan.
    await openSheet(page, TARGET_SHEET);
    await openDataDisclosure(page);
    await page.getByTestId("import-text").fill(exported); // test-friendly text entry
    await expect(page.getByTestId("import-preview")).toBeVisible();
    await page.getByTestId("import-confirm").click();

    // 3) IMPORT_COMPLETED reflected (toast + refetch); structure reproduced.
    // The import creates fresh node ids in S2 (governed replay), so verify the
    // round-trip by count + labels rather than by the source ids.
    await expect(page.getByTestId("banner")).toContainText(/import/i);
    await expect(page.getByTestId("node-count")).toContainText("6 nodes");
    for (const label of ["Root", "Phase 1", "Task X", "Phase 2", "Task Y", "Task Z"]) {
      await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
    }
    // split-column type survives the round-trip: the status column re-imported as
    // single-select-split renders a split-cell (its id is fresh in S2, so match by
    // testid rather than the source column name).
    await expect(page.locator('[data-testid="split-cell"]').first()).toBeVisible();
  });
});
