// Feature 2 (shareable views) — PURE, SECURITY-CRITICAL tests, written RED
// before src/lib/view.ts exists. A SheetView is a presentation-only overlay
// (hidden / order / width / collapsed) encoded into a ?v= base64url token. The
// load-bearing invariant is REVEAL-IMPOSSIBILITY: resolveColumns starts from the
// already-read-ACL-filtered snapshot.columns, so a column the recipient cannot
// read can NEVER be surfaced even if the shared token's order/hidden/width names
// it (the Feature-2 ∩ Feature-3 forwarded-link test). The view layer issues ZERO
// mutations and touches no backend.

import { describe, expect, it } from "vitest";
import {
  encodeView,
  decodeView,
  resolveColumnOrder,
  resolveColumns,
  type SheetView,
} from "./view";
import type { SnapshotColumn } from "../api";

// A minimal snapshot column factory — only the fields resolveColumns/ViewMenu read.
function col(
  name: string,
  opts: Partial<SnapshotColumn> = {},
): SnapshotColumn {
  return {
    name,
    field: name.replace(/^col:/, ""),
    label: name.replace(/^col:/, ""),
    type: "text",
    is_label: false,
    column_owner: "A",
    editors: [],
    can_edit: false,
    ...opts,
  };
}

// Canonical readable set in snapshot order: label first, then status/budget/notes.
const LABEL = col("col:name", { is_label: true, label: "Name" });
const STATUS = col("col:status", { label: "Status" });
const BUDGET = col("col:budget", { label: "Budget" });
const NOTES = col("col:notes", { label: "Notes" });
const SNAPSHOT_COLS: SnapshotColumn[] = [LABEL, STATUS, BUDGET, NOTES];

const names = (cs: SnapshotColumn[]) => cs.map((c) => c.name);

describe("encodeView / decodeView round-trip", () => {
  it("round-trips {hidden, order, width, collapsed} through base64url", () => {
    const view: SheetView = {
      v: 1,
      hidden: ["col:budget"],
      order: ["col:notes", "col:status"],
      width: { "col:notes": 320, "col:status": 120 },
      collapsed: ["P2"],
    };
    const token = encodeView(view);
    // base64url is URL-safe: no +,/,= characters.
    expect(token).not.toMatch(/[+/=]/);
    expect(decodeView(token)).toEqual(view);
  });

  it("round-trips a minimal view (only hidden+order, width/collapsed absent)", () => {
    const view: SheetView = { v: 1, hidden: [], order: ["col:status"] };
    expect(decodeView(encodeView(view))).toEqual(view);
  });
});

describe("decodeView — malformed / oversize / unknown-version → null", () => {
  it("returns null for a non-base64url / garbage token", () => {
    expect(decodeView("!!!not-base64!!!")).toBeNull();
  });

  it("returns null for valid base64url that is not JSON", () => {
    // 'not json at all' base64url-encoded is still not parseable JSON.
    const garbage = btoa("not json at all").replace(/=+$/, "");
    expect(decodeView(garbage)).toBeNull();
  });

  it("returns null for an unknown/future version", () => {
    const token = encodeView({ v: 1, hidden: [], order: [] });
    // hand-mutate the version field by re-encoding a v:2 shape.
    const v2 = btoa(JSON.stringify({ v: 2, hidden: [], order: [] }))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
    expect(decodeView(v2)).toBeNull();
    // sanity: the v:1 token still decodes.
    expect(decodeView(token)).not.toBeNull();
  });

  it("returns null for an oversize token (> 4KB)", () => {
    const huge: SheetView = {
      v: 1,
      hidden: Array.from({ length: 5000 }, (_, i) => `col:${i}`),
      order: [],
    };
    const token = encodeView(huge);
    expect(token.length).toBeGreaterThan(4096);
    expect(decodeView(token)).toBeNull();
  });

  it("returns null for an empty token", () => {
    expect(decodeView("")).toBeNull();
  });
});

describe("resolveColumns — REVEAL-IMPOSSIBILITY (Feature 2 ∩ Feature 3)", () => {
  it("can NEVER surface a column absent from the snapshot even if order names it", () => {
    // Recipient's snapshot is missing the forbidden col:budget (read-ACL filtered
    // it out server-side). A forwarded link from a privileged sender lists it in
    // order/hidden/width — resolveColumns must drop it entirely.
    const recipientCols = [LABEL, STATUS, NOTES]; // NO col:budget
    const sharedView: SheetView = {
      v: 1,
      hidden: [],
      order: ["col:budget", "col:status", "col:notes"],
      width: { "col:budget": 400 },
    };
    const resolved = resolveColumns(recipientCols, sharedView);
    expect(names(resolved)).not.toContain("col:budget");
    // only readable columns survive, ordered by the (filtered) order.
    expect(names(resolved)).toEqual(["col:name", "col:status", "col:notes"]);
  });

  it("a forbidden column named in hidden cannot leak either (visible = present MINUS hidden)", () => {
    const recipientCols = [LABEL, STATUS]; // NO col:budget
    const view: SheetView = { v: 1, hidden: ["col:budget"], order: [] };
    const resolved = resolveColumns(recipientCols, view);
    expect(names(resolved)).toEqual(["col:name", "col:status"]);
  });
});

describe("resolveColumns — hidden / order / width semantics", () => {
  it("hides a present, readable column when listed in hidden", () => {
    const view: SheetView = { v: 1, hidden: ["col:budget"], order: [] };
    const resolved = resolveColumns(SNAPSHOT_COLS, view);
    expect(names(resolved)).not.toContain("col:budget");
    expect(names(resolved)).toContain("col:status");
  });

  it("orders by view.order ∩ visible, then appends remaining in snapshot order", () => {
    // order names notes,status (and an unknown). budget is unnamed → appended in
    // snapshot order after the ordered ones. label always kept (see below).
    const view: SheetView = {
      v: 1,
      hidden: [],
      order: ["col:notes", "col:status", "col:ghost"],
    };
    const resolved = resolveColumns(SNAPSHOT_COLS, view);
    // label kept; ordered notes,status; then snapshot-order remainder (budget).
    expect(names(resolved)).toEqual([
      "col:name",
      "col:notes",
      "col:status",
      "col:budget",
    ]);
  });

  it("appends a readable column NOT in order in snapshot order (new-column-since-link)", () => {
    // The link was made before col:notes existed; order omits it. It must appear,
    // appended in snapshot order, never dropped.
    const view: SheetView = { v: 1, hidden: [], order: ["col:budget", "col:status"] };
    const resolved = resolveColumns(SNAPSHOT_COLS, view);
    expect(names(resolved)).toEqual([
      "col:name",
      "col:budget",
      "col:status",
      "col:notes",
    ]);
  });

  it("drops UNKNOWN / DELETED column names in order silently", () => {
    const view: SheetView = {
      v: 1,
      hidden: [],
      order: ["col:deleted", "col:status"],
    };
    const resolved = resolveColumns(SNAPSHOT_COLS, view);
    expect(names(resolved)).not.toContain("col:deleted");
    expect(names(resolved)).toContain("col:status");
  });

  it("drops UNKNOWN / DELETED column names in hidden silently (no throw)", () => {
    const view: SheetView = { v: 1, hidden: ["col:deleted"], order: [] };
    expect(() => resolveColumns(SNAPSHOT_COLS, view)).not.toThrow();
    expect(names(resolveColumns(SNAPSHOT_COLS, view))).toEqual(
      names(SNAPSHOT_COLS),
    );
  });

  it("applies width from the view onto the resolved columns", () => {
    const view: SheetView = {
      v: 1,
      hidden: [],
      order: [],
      width: { "col:status": 240 },
    };
    const resolved = resolveColumns(SNAPSHOT_COLS, view);
    const status = resolved.find((c) => c.name === "col:status")!;
    expect(status.width).toBe(240);
    // unspecified widths are left untouched (undefined here).
    const budget = resolved.find((c) => c.name === "col:budget")!;
    expect(budget.width).toBeUndefined();
  });

  it("width for an absent/forbidden column is ignored (no phantom column)", () => {
    const recipientCols = [LABEL, STATUS]; // NO col:budget
    const view: SheetView = { v: 1, hidden: [], order: [], width: { "col:budget": 999 } };
    const resolved = resolveColumns(recipientCols, view);
    expect(names(resolved)).toEqual(["col:name", "col:status"]);
  });
});

describe("resolveColumns — LABEL always kept / never hideable", () => {
  it("keeps the label column even when hidden names it", () => {
    const view: SheetView = { v: 1, hidden: ["col:name"], order: [] };
    const resolved = resolveColumns(SNAPSHOT_COLS, view);
    expect(names(resolved)).toContain("col:name");
  });

  it("keeps the label column even when order omits it (label leads)", () => {
    const view: SheetView = { v: 1, hidden: [], order: ["col:budget"] };
    const resolved = resolveColumns(SNAPSHOT_COLS, view);
    expect(resolved[0].name).toBe("col:name");
  });
});

describe("resolveColumns — default view (no token / null view)", () => {
  it("a null view yields all readable columns in snapshot order", () => {
    // decode(malformed) → null → resolveColumns must accept null as the default.
    const resolved = resolveColumns(SNAPSHOT_COLS, null);
    expect(names(resolved)).toEqual(names(SNAPSHOT_COLS));
  });

  it("an empty view (no hidden/order/width) yields all readable columns in snapshot order", () => {
    const resolved = resolveColumns(SNAPSHOT_COLS, { v: 1, hidden: [], order: [] });
    expect(names(resolved)).toEqual(names(SNAPSHOT_COLS));
  });
});

// resolveColumnOrder — the COMPLETE non-label ordering. Shared by three callers:
// the View menu's list, the grid header drag's fold, and the order handed to
// setColumnOrder ("save for everyone"), which requires every non-label column
// exactly once.
describe("resolveColumnOrder — the complete non-label ordering", () => {
  it("a null/empty view yields the snapshot order, label excluded", () => {
    expect(resolveColumnOrder(SNAPSHOT_COLS, null)).toEqual([
      "col:status",
      "col:budget",
      "col:notes",
    ]);
    expect(resolveColumnOrder(SNAPSHOT_COLS, { v: 1, hidden: [], order: [] })).toEqual([
      "col:status",
      "col:budget",
      "col:notes",
    ]);
  });

  it("follows the view order, then appends columns the view never named", () => {
    const view: SheetView = { v: 1, hidden: [], order: ["col:notes", "col:status"] };
    expect(resolveColumnOrder(SNAPSHOT_COLS, view)).toEqual([
      "col:notes",
      "col:status",
      "col:budget",
    ]);
  });

  it("INCLUDES hidden columns — the shared order is a property of the sheet", () => {
    // What this viewer hides is their business; the stored order still has to
    // name every non-label column, or setColumnOrder rejects it as incomplete.
    const view: SheetView = { v: 1, hidden: ["col:budget"], order: [] };
    expect(resolveColumnOrder(SNAPSHOT_COLS, view)).toContain("col:budget");
  });

  it("drops a name the snapshot does not carry (REVEAL-IMPOSSIBILITY)", () => {
    // A forwarded token can name a column this viewer cannot read. It must not
    // appear here either — otherwise "save for everyone" would ship a column id
    // the viewer was never allowed to see.
    const view: SheetView = { v: 1, hidden: [], order: ["col:secret", "col:notes"] };
    const order = resolveColumnOrder(SNAPSHOT_COLS, view);
    expect(order).not.toContain("col:secret");
    expect(order).toEqual(["col:notes", "col:status", "col:budget"]);
  });

  it("ignores a duplicate in the view order (each column appears exactly once)", () => {
    const view: SheetView = {
      v: 1,
      hidden: [],
      order: ["col:notes", "col:notes", "col:status"],
    };
    const order = resolveColumnOrder(SNAPSHOT_COLS, view);
    expect(order).toEqual(["col:notes", "col:status", "col:budget"]);
  });

  it("never contains the label column even when the view order names it", () => {
    const view: SheetView = { v: 1, hidden: [], order: ["col:name", "col:notes"] };
    expect(resolveColumnOrder(SNAPSHOT_COLS, view)).not.toContain("col:name");
  });
});

// App.reorderColumnsInView folds a drag over the VISIBLE headers back into the
// COMPLETE order by walking `resolveColumnOrder` and handing every visible slot
// the next dragged name. That positional walk is only correct because
// resolveColumns' visible non-label sequence is always a SUBSEQUENCE of
// resolveColumnOrder's — an invariant that lives implicitly in two separate
// functions, so a future edit to either part-1/part-2 split could break the fold
// with nothing failing. Pin it here.
describe("view — resolveColumns' visible order is a subsequence of resolveColumnOrder", () => {
  const isSubsequence = (sub: string[], full: string[]): boolean => {
    let k = 0;
    for (const name of full) if (k < sub.length && sub[k] === name) k++;
    return k === sub.length;
  };

  const VIEWS: SheetView[] = [
    { v: 1, hidden: [], order: [] },
    { v: 1, hidden: [], order: ["col:notes", "col:status", "col:budget"] },
    { v: 1, hidden: ["col:budget"], order: [] },
    { v: 1, hidden: ["col:budget"], order: ["col:notes", "col:budget", "col:status"] },
    { v: 1, hidden: ["col:status", "col:notes"], order: ["col:notes", "col:budget"] },
    // A forwarded token naming a column this viewer cannot read, plus the label.
    { v: 1, hidden: [], order: ["col:secret", "col:name", "col:notes"] },
    // Every non-label column hidden — the degenerate end of the range.
    { v: 1, hidden: ["col:status", "col:budget", "col:notes"], order: ["col:budget"] },
  ];

  it.each(VIEWS.map((v, i) => [i, v] as const))("holds for view %i", (_i, view) => {
    const complete = resolveColumnOrder(SNAPSHOT_COLS, view);
    const visible = resolveColumns(SNAPSHOT_COLS, view)
      .filter((c) => !c.is_label)
      .map((c) => c.name);
    expect(isSubsequence(visible, complete)).toBe(true);
    // The fold's other precondition: every visible name is present in the
    // complete order, so the walk consumes exactly `visible.length` slots.
    expect(complete.filter((n) => visible.includes(n))).toEqual(visible);
  });
});
