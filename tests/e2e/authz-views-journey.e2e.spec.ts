// Runnable status: NEEDS A RUNNING APP (Vite frontend + Frappe backend with the
// canonical seed). See ./README.md.
//
// CAPTURE spec (no AI-agent steps) for the authz + shareable-views feature set.
// Like journey-capture, each test drives a surface into a representative state and
// writes a full-page PNG. It continues the journey numbering (17..20) and mirrors
// every shot into docs/ux-review/authz-views/ for the UX pass. The behaviors:
//
//   17  optimistic-concurrency conflict — a cell whose stored version moved out
//       from under the loaded snapshot is rejected on save with the structured
//       VERSION_CONFLICT banner (no silent lost update).
//   18  shareable view — hide a column + reorder + width tweak + a collapsed node,
//       copy the live ?v= token, reopen it in a FRESH context → same view.
//   19  public column — a read_level=public column stays visible to a non-owner.
//   20  read-restricted column — an owner-only column is ABSENT (headers AND
//       cells) from an unauthorized viewer, and the SAME ?v= link forwarded to
//       that viewer still cannot reveal it (reveal-impossibility).
//
// Personas: B owns col:notes (executes its edits → version bumps); C owns
// col:budget (sets its read_level); G is a plain member (unauthorized viewer).

import { test, expect, type APIRequestContext, type Page } from "@playwright/test";
import { loginAs, openSheet, resetSeed, cell, row, SHEET } from "./fixtures";

const DIR = "docs/journey";
const UX = "docs/ux-review/authz-views";
const KEY = (p: string) => process.env[`ARBOR_E2E_${p}_KEY`];

// Fire a capability call straight at the backend as a given persona (out-of-band
// relative to the browser), so a test can move state the loaded snapshot can't see.
async function api(
  request: APIRequestContext,
  persona: string,
  method: string,
  body: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const key = KEY(persona);
  const res = await request.post(`/api/method/${method}`, {
    headers: key ? { Authorization: `token ${key}` } : {},
    data: body,
  });
  if (!res.ok()) throw new Error(`${method} as ${persona} failed: ${res.status()}`);
  const json = (await res.json()) as { message?: Record<string, unknown> };
  return json.message ?? {};
}

// Save a full-page PNG into BOTH the journey sequence and the UX-review folder.
async function shoot(page: Page, journeyFile: string, uxFile: string): Promise<void> {
  await page.screenshot({ path: `${DIR}/${journeyFile}`, fullPage: true });
  await page.screenshot({ path: `${UX}/${uxFile}`, fullPage: true });
}

test("journey 17 — optimistic-concurrency conflict (VERSION_CONFLICT)", async ({ page, request }) => {
  // B owns col:notes, so B's edits EXECUTE and bump the per-cell version. The
  // browser loads the snapshot (FE base_version = 0 for the empty cell); then we
  // bump the SAME cell twice out-of-band via the API as B (v1, v2). When B then
  // edits in the UI on the now-stale base, the server returns the structured
  // HTTP-200 VERSION_CONFLICT Outcome and the FE raises the conflict banner.
  await loginAs(page, "B");
  await openSheet(page); // resets the seed, loads the snapshot at version 0

  // Out-of-band: advance the stored cell past the loaded snapshot's base.
  await api(request, "B", "arbor.update_cell", {
    sheet: SHEET, node: "X", column: "col:notes", value: "v1 from another tab", base_version: 0,
  });
  const second = await api(request, "B", "arbor.update_cell", {
    sheet: SHEET, node: "X", column: "col:notes", value: "v2 authoritative", base_version: 1,
  });
  expect((second.data as { version?: number }).version).toBe(2);

  // Now edit in the browser — the FE still holds base_version 0 → conflict.
  const notes = cell(page, "X", "col:notes");
  await notes.dblclick();
  const input = page.getByTestId("cell-input");
  await input.fill("my stale edit");
  await input.blur();

  const banner = page.getByTestId("banner");
  await expect(banner).toHaveAttribute("data-kind", "conflict");
  await shoot(page, "17-version-conflict.png", "ux-17-version-conflict.png");
});

test("journey 18 — shareable view round-trips in a fresh context", async ({ page, context }) => {
  // Build a non-trivial view (hide col:budget, move col:notes up, widen
  // col:status) through the presentation-only ViewMenu — these dimensions are
  // mirrored live into ?v= via replaceState. Collapse is a load-time SEED in this
  // build (chevron toggles drive the runtime collapsed Set but are not yet
  // written back into the URL token), so we fold collapsed:["P2"] into the copied
  // token to forward the FULL view (hidden + order + width + collapsed). Reopen it
  // in a brand-new context (no shared state) to prove it reproduces. A (sheet
  // owner) sees every column, so the link is unconstrained by read-ACL.
  await loginAs(page, "A");
  await openSheet(page);

  // Open the "View" disclosure and apply the overlay.
  await page.getByTestId("view-disclosure").getByText("View", { exact: true }).click();
  await expect(page.getByTestId("view-menu")).toBeVisible();
  await page.getByTestId("view-toggle-col:budget").click(); // hide budget
  await page.getByTestId("view-up-col:notes").click(); // reorder notes earlier
  await page.getByTestId("view-width-col:status").fill("220"); // width tweak

  // Also collapse P2 in the live tree so the BUILT screenshot shows the same
  // state we forward (Y/Z hidden under the collapsed P2).
  await page.getByTestId("chevron-P2").click();
  await expect(page.getByTestId("row-Y")).toHaveCount(0);

  // The hook mirrors the live view into ?v= via replaceState; budget must now be
  // gone from the rendered headers.
  await expect(page.getByTestId("col-head-col:budget")).toHaveCount(0);
  await expect(page.getByTestId("col-head-col:notes")).toBeVisible();
  await page.screenshot({ path: `${DIR}/18-shareable-view-built.png`, fullPage: true });
  await page.screenshot({ path: `${UX}/ux-18a-view-built.png`, fullPage: true });

  // Copy the live ?v= token (carries hidden + order + width), decode it, fold the
  // collapsed node in, and re-encode — the shared link a user would forward.
  const sharedUrl = await page.evaluate(() => window.location.href);
  expect(sharedUrl).toContain("v=");
  const liveToken = new URL(sharedUrl).searchParams.get("v")!;
  const liveView = JSON.parse(
    Buffer.from(liveToken.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8"),
  ) as Record<string, unknown>;
  liveView.collapsed = ["P2"];
  const sharedToken = Buffer.from(JSON.stringify(liveView), "utf8")
    .toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const forwardUrl = `/?sheet=${encodeURIComponent(SHEET)}&v=${sharedToken}`;

  const fresh = await context.browser()!.newContext();
  const freshPage = await fresh.newPage();
  // Re-inject A's auth into the fresh context (the init-script hook in fixtures
  // only ran on the original page).
  const aKey = KEY("A");
  if (aKey) {
    await freshPage.addInitScript((token) => {
      (window as unknown as { __ARBOR_AUTH__?: string }).__ARBOR_AUTH__ = token;
    }, `token ${aKey}`);
  }
  await freshPage.goto(forwardUrl);
  await expect(freshPage.getByTestId("tree-table")).toBeVisible();
  // The forwarded view reproduces: budget hidden, notes present, P2 collapsed.
  await expect(freshPage.getByTestId("col-head-col:budget")).toHaveCount(0);
  await expect(freshPage.getByTestId("col-head-col:notes")).toBeVisible();
  await expect(freshPage.getByTestId("row-Y")).toHaveCount(0); // P2's child hidden by the seeded collapse
  await freshPage.screenshot({ path: `${DIR}/18b-shareable-view-reproduced.png`, fullPage: true });
  await freshPage.screenshot({ path: `${UX}/ux-18b-view-reproduced.png`, fullPage: true });
  await fresh.close();
});

test("journey 19 — public column visible to a non-owner", async ({ page }) => {
  // col:budget defaults to read_level=public, so a plain member (G, who owns
  // nothing) still sees the budget column + its cells. This is the baseline the
  // restricted case (20) departs from.
  await loginAs(page, "G");
  await openSheet(page);
  await expect(page.getByTestId("col-head-col:budget")).toBeVisible();
  await expect(cell(page, "X", "col:budget")).toBeVisible();
  await shoot(page, "19-public-column-visible.png", "ux-19-public-visible.png");
});

test("journey 20 — read-restricted column hidden from an unauthorized viewer", async ({ page, request, context }) => {
  // C (owner of col:budget) restricts it to read_level=owner-only out-of-band.
  // G then loads the sheet: the column must be ABSENT from headers AND every
  // cell (no value leak). Then the SAME ?v= link A built (which references
  // col:budget) is forwarded to G — reveal is structurally impossible because
  // resolveColumns starts from the read-ACL-filtered snapshot, so the column
  // stays gone for G even with a token that names it.
  await resetSeed();
  await api(request, "C", "arbor.update_column", {
    sheet: SHEET, column: "col:budget", patch: { read_level: "owner-only" },
  });

  // Sanity: the owner C still sees budget (capture as the "owner view").
  await loginAs(page, "C");
  await page.goto(`/?sheet=${encodeURIComponent(SHEET)}`);
  await expect(page.getByTestId("tree-table")).toBeVisible();
  await expect(page.getByTestId("col-head-col:budget")).toBeVisible();

  // A builds a ?v= token that explicitly references col:budget (e.g. a width
  // override) — this is the "forwarded link" payload. We craft it directly to
  // guarantee it names the restricted column, then hand it to G.
  const craftedView = { v: 1 as const, hidden: [], order: ["col:budget", "col:status"], width: { "col:budget": 300 } };
  const token = Buffer.from(JSON.stringify(craftedView), "utf8")
    .toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

  // G opens the sheet plainly first → budget absent from headers AND cells.
  const gKey = KEY("G");
  const gCtx = await context.browser()!.newContext();
  const gPage = await gCtx.newPage();
  if (gKey) {
    await gPage.addInitScript((t) => {
      (window as unknown as { __ARBOR_AUTH__?: string }).__ARBOR_AUTH__ = t;
    }, `token ${gKey}`);
  }
  await gPage.goto(`/?sheet=${encodeURIComponent(SHEET)}`);
  await expect(gPage.getByTestId("tree-table")).toBeVisible();
  await expect(gPage.getByTestId("col-head-col:budget")).toHaveCount(0);
  // No budget cell anywhere in the grid for G.
  await expect(gPage.locator('[data-column="col:budget"]')).toHaveCount(0);
  await gPage.screenshot({ path: `${DIR}/20-restricted-hidden.png`, fullPage: true });
  await gPage.screenshot({ path: `${UX}/ux-20a-restricted-hidden.png`, fullPage: true });

  // The forwarded ?v= token that NAMES col:budget still cannot reveal it for G.
  await gPage.goto(`/?sheet=${encodeURIComponent(SHEET)}&v=${token}`);
  await expect(gPage.getByTestId("tree-table")).toBeVisible();
  await expect(gPage.getByTestId("col-head-col:budget")).toHaveCount(0);
  await expect(gPage.locator('[data-column="col:budget"]')).toHaveCount(0);
  await gPage.screenshot({ path: `${DIR}/20b-restricted-link-no-reveal.png`, fullPage: true });
  await gPage.screenshot({ path: `${UX}/ux-20b-link-no-reveal.png`, fullPage: true });
  await gCtx.close();
});
