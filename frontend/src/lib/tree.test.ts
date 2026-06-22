import { describe, expect, it } from "vitest";
import {
  buildVisibleRows,
  computeMove,
  dropZonesFor,
  isDescendantOrSelf,
} from "./tree";
import { loginAs } from "../test/fixture";

const nodes = loginAs("A").nodes;
const byName = (n: string) => nodes.find((x) => x.name === n)!;

describe("buildVisibleRows", () => {
  it("orders rows depth-first in NestedSet order with correct depth (WEB_UI-001)", () => {
    const rows = buildVisibleRows(nodes, new Set());
    expect(rows.map((r) => r.node.name)).toEqual(["R", "P1", "X", "P2", "Y", "Z"]);
    expect(rows.map((r) => r.depth)).toEqual([0, 1, 2, 1, 2, 2]);
  });

  it("collapsing a node hides its entire subtree (WEB_UI-004)", () => {
    const rows = buildVisibleRows(nodes, new Set(["P2"]));
    expect(rows.map((r) => r.node.name)).toEqual(["R", "P1", "X", "P2"]);
  });

  it("collapsing P1 hides X (WEB_UI-004)", () => {
    const rows = buildVisibleRows(nodes, new Set(["P1"]));
    expect(rows.map((r) => r.node.name)).toEqual(["R", "P1", "P2", "Y", "Z"]);
  });

  it("marks groups with children and leaves without (WEB_UI-003)", () => {
    const rows = buildVisibleRows(nodes, new Set());
    expect(rows.find((r) => r.node.name === "R")!.hasChildren).toBe(true);
    expect(rows.find((r) => r.node.name === "X")!.hasChildren).toBe(false);
  });
});

describe("dropZonesFor", () => {
  it("offers before/inside/after for groups and before/after for leaves (WEB_UI-039)", () => {
    const rows = buildVisibleRows(nodes, new Set());
    const p2 = rows.find((r) => r.node.name === "P2")!;
    const x = rows.find((r) => r.node.name === "X")!;
    expect(dropZonesFor(p2)).toEqual(["before", "inside", "after"]);
    expect(dropZonesFor(x)).toEqual(["before", "after"]);
  });
});

describe("computeMove", () => {
  it("inside sets new_parent=target, after=null (WEB_UI-036)", () => {
    expect(computeMove(byName("X"), byName("P2"), "inside", nodes)).toEqual({
      node: "X",
      new_parent: "P2",
      after: null,
    });
  });

  it("before first sibling sets after=null (head) (WEB_UI-037)", () => {
    expect(computeMove(byName("Z"), byName("Y"), "before", nodes)).toEqual({
      node: "Z",
      new_parent: "P2",
      after: null,
    });
  });

  it("after a sibling sets after=that sibling (WEB_UI-038)", () => {
    expect(computeMove(byName("Y"), byName("Z"), "after", nodes)).toEqual({
      node: "Y",
      new_parent: "P2",
      after: "Z",
    });
  });

  it("rejects moving a node onto its own descendant (WEB_UI-044)", () => {
    expect(computeMove(byName("P2"), byName("Z"), "inside", nodes)).toBeNull();
    expect(isDescendantOrSelf(byName("P2"), byName("Z"))).toBe(true);
  });

  it("rejects dropping a node onto itself (WEB_UI-045)", () => {
    expect(computeMove(byName("X"), byName("X"), "inside", nodes)).toBeNull();
  });
});
