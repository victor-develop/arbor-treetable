import { describe, expect, it } from "vitest";
import { buildImportPlan, exportSnapshot, validateImport } from "./io";
import { loginAs } from "../test/fixture";

describe("export", () => {
  it("serializes the exact snapshot shape (WEB_UI-074)", () => {
    const snap = loginAs("A");
    const text = exportSnapshot(snap);
    expect(JSON.parse(text)).toEqual(snap);
  });
});

describe("validateImport", () => {
  it("rejects malformed JSON (WEB_UI-079)", () => {
    expect(validateImport("{not json")).toEqual({ ok: false, error: expect.any(String) });
  });
  it("rejects an unsupported column type (WEB_UI-080)", () => {
    const bad = JSON.stringify({
      columns: [{ field: "x", label: "X", type: "rich-text" }],
      nodes: [],
    });
    const v = validateImport(bad);
    expect(v.ok).toBe(false);
  });
  it("accepts a valid snapshot", () => {
    const v = validateImport(exportSnapshot(loginAs("A")));
    expect(v.ok).toBe(true);
  });
});

describe("buildImportPlan", () => {
  it("plans addColumn then addNode in NestedSet order (WEB_UI-077)", () => {
    const plan = buildImportPlan(loginAs("A"), "S2");
    const actions = plan.map((s) => s.action);
    expect(actions.filter((a) => a === "addColumn").length).toBe(5);
    const nodeSteps = plan.filter((s) => s.action === "addNode");
    expect((nodeSteps[0].params as { parent: unknown }).parent).toBeNull(); // R first
  });

  it("skips columns/nodes that already exist (idempotency, WEB_UI-081)", () => {
    const src = loginAs("A");
    const plan = buildImportPlan(src, "S", src);
    expect(plan.filter((s) => s.action === "addColumn")).toHaveLength(0);
    expect(plan.filter((s) => s.action === "addNode")).toHaveLength(0);
  });
});
