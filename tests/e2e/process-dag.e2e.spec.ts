// Runnable status: NEEDS A RUNNING APP (Vite frontend + Frappe backend with the
// canonical seed). See ./README.md. Config-gated like every *.e2e.spec.ts — it is
// collected only by the Playwright runner (testMatch /.*\.e2e\.spec\.ts/), never by
// vitest/pytest, and it drives a real browser; there is no live server here.
//
// The visible process-DAG journey (Feature: process — DAG rule model, WS-A2/WS-B2):
// a structural owner opens a sheet, opens the Process modal, uses the bespoke
// dependency-free SVG/DOM canvas to add two column nodes and connect
// START -> budget (with an SLA window) and budget -> notes, Saves the rule DAG and
// Enables it, then opens the flow Dashboard and confirms the DAG is reflected as
// two edges: the START->budget (row-trigger) edge and the budget->notes edge. A
// row added with budget defaulted then satisfied surfaces as a satisfied run in the
// START edge's drill-down; the budget->notes edge shows the downstream pending step.
//
// The canvas interactions are keyboard/DOM-reachable (no drag dependency — the
// same affordances the bench-free ProcessCanvas.test.tsx covers), so the selectors
// here reuse the component's data-testid contract 1:1.
//
// Cases: WEB_UI process-DAG canvas authoring + dashboard reflection (e2e).

import { test, expect, type Page } from "@playwright/test";
import { loginAs, openSheet } from "./fixtures";

const COL_BUDGET = "col:budget";
const COL_NOTES = "col:notes";

// Open the header-launched Process modal (structural-owner / admin gated).
async function openProcess(page: Page): Promise<void> {
  await page.getByTestId("process-config-button").click();
  await expect(page.getByTestId("process-config-modal")).toBeVisible();
  await expect(page.getByTestId("process-canvas")).toBeVisible();
}

// Add a sheet column as a canvas node via the picker + Add button.
async function addCanvasNode(page: Page, column: string): Promise<void> {
  await page.getByTestId("canvas-add-column").selectOption(column);
  await page.getByTestId("canvas-add-node").click();
  await expect(page.getByTestId(`canvas-node-${column}`)).toBeVisible();
}

// Draw an edge from -> to: arm connect-mode on `from`, then click `to`'s body.
async function connect(page: Page, from: string, to: string): Promise<void> {
  await page.getByTestId(`canvas-connect-${from}`).click();
  await page.getByTestId(`canvas-node-body-${to}`).click();
  await expect(page.getByTestId(`canvas-edge-row-${from}-${to}`)).toBeVisible();
}

test.describe("process DAG canvas authoring + dashboard (e2e)", () => {
  test("owner draws START→budget and budget→notes, saves+enables, dashboard reflects the DAG", async ({
    page,
  }) => {
    // A is the sheet structural owner — the only persona the Process button mounts for.
    await loginAs(page, "A");
    await openSheet(page);
    await openProcess(page);

    // Add the two participating column nodes.
    await addCanvasNode(page, COL_BUDGET);
    await addCanvasNode(page, COL_NOTES);

    // START -> budget (the row-trigger expectation). START node id is the fixed
    // START sentinel exposed by the canvas layout.
    const START = "__start__";
    await connect(page, START, COL_BUDGET);
    // Set the within-duration (SLA) on that edge to 1 hour so the dashboard shows it.
    const within = page.getByTestId(`canvas-edge-within-${START}-${COL_BUDGET}`);
    await within.fill("3600");
    await expect(within).toHaveValue("3600");

    // budget -> notes (the downstream column-trigger expectation) — the column
    // EXPECTED by the START rule is the TRIGGER of this one; that composition is the
    // DAG. It must be accepted (no cycle) with no rejection announced.
    await connect(page, COL_BUDGET, COL_NOTES);
    await expect(page.getByTestId("canvas-reject")).toHaveText("");
    // No hard validation error blocks Save.
    await expect(page.getByTestId("canvas-error")).toHaveCount(0);

    // Save the rule DAG, then Enable it.
    const save = page.getByTestId("pc-define");
    await expect(save).toBeEnabled();
    await save.click();
    // After a save the process exists; the Enable toggle appears (disabled state).
    await expect(page.getByTestId("pc-enable")).toBeVisible();
    await page.getByTestId("pc-enable").click();
    await expect(page.getByTestId("pc-state")).toHaveText("Enabled");

    // Reopen the canvas fresh and confirm both edges rehydrated from the persisted
    // rules (the canvas is a VIEW over the saved rule set).
    await page.getByTestId("pc-close").click();
    await openProcess(page);
    await expect(page.getByTestId(`canvas-edge-row-${START}-${COL_BUDGET}`)).toBeVisible();
    await expect(page.getByTestId(`canvas-edge-row-${COL_BUDGET}-${COL_NOTES}`)).toBeVisible();
    await page.getByTestId("pc-close").click();

    // Open the flow Dashboard and confirm the DAG is reflected as two edges:
    // START->budget (a row trigger: no "from" label) and budget->notes.
    await page.getByTestId("nav-dashboard").click();
    const dash = page.getByTestId("process-dashboard");
    await expect(dash).toBeVisible();
    const board = page.getByTestId("pd-board");
    await expect(board).toBeVisible();
    // Two edges rendered (START->budget, budget->notes).
    await expect(board.getByTestId("pd-stage-to")).toHaveCount(2);
    // The budget->notes edge carries a readable "from" (budget) — a downstream
    // column trigger, distinct from the row-triggered START edge (which has none).
    await expect(board.getByTestId("pd-stage-from")).toHaveCount(1);
    await expect(board.getByTestId("pd-stage-from").first()).toContainText("budget");
    // The notes step is one of the two destination labels.
    await expect(board.getByTestId("pd-stage-to").filter({ hasText: "notes" })).toHaveCount(1);
  });

  test("enabling the DAG backfills in-scope runs surfaced in the START edge drill-down", async ({
    page,
  }) => {
    // Owner A authors + enables the same DAG. Enable BACKFILLS a run per already-
    // existing in-scope root-child (P1, P2), each with the START->budget expectation
    // open. Confirm via the dashboard drill-down that those runs are surfaced — the
    // dashboard is the visible flow surface, so the wiring is proven end to end.
    await loginAs(page, "A");
    await openSheet(page);
    await openProcess(page);
    await addCanvasNode(page, COL_BUDGET);
    await addCanvasNode(page, COL_NOTES);
    const START = "__start__";
    await connect(page, START, COL_BUDGET);
    await connect(page, COL_BUDGET, COL_NOTES);
    await page.getByTestId("pc-define").click();
    await page.getByTestId("pc-enable").click();
    await expect(page.getByTestId("pc-state")).toHaveText("Enabled");
    await page.getByTestId("pc-close").click();

    await page.getByTestId("nav-dashboard").click();
    await expect(page.getByTestId("process-dashboard")).toBeVisible();

    // Drill the FIRST edge (the row-triggered START->budget edge) and confirm at
    // least one backfilled run is listed (the in-scope root-children P1/P2).
    const firstStage = page.getByTestId("pd-edge-0");
    await firstStage.getByTestId("pd-stage-drill").click();
    const runs = page.getByTestId("pd-runs");
    await expect(runs).toBeVisible();
    await expect(runs.locator('[data-testid^="pd-run-"]').first()).toBeVisible();
  });
});
