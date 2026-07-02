// Exhaustive unit spec for the pure DAG helpers (graph.ts). These MIRROR the
// server-side validate_rules; if the two diverge the LLM path could persist a
// deadlocking rule set the client blessed (or vice-versa). Covered here:
//   * buildGraph — rules -> {nodes, edges}, "and" fan-out, row vs column trigger,
//     label resolution + redaction, rule_key minting.
//   * reachableFromStart / hasCycle / wouldCreateCycle — self-loop, simple cycle,
//     multi-hop cycle, diamond (not a cycle), orphan reachability.
//   * validate — self-loop, duplicate edge, cycle errors; unreachable warning;
//     clean DAG passes.
//   * layout — determinism, layered x by longest-path, START at layer 0.
//   * packRules — round-trip rules<->edges, "and" grouping, self-loop dropped.

import { describe, expect, it } from "vitest";
import {
  START_ID,
  buildGraph,
  hasCycle,
  layout,
  packRules,
  reachableFromStart,
  validate,
  wouldCreateCycle,
  type GraphEdge,
} from "./graph";
import type { ProcessRuleInput, ProcessRuleView } from "../../api";

function edge(from: string, to: string, over: Partial<GraphEdge> = {}): GraphEdge {
  return {
    rule_key: `${from}->${to}`,
    from,
    to,
    within_seconds: 0,
    trigger_op: "created-or-updated",
    notify_on_expect: true,
    join: "any",
    ...over,
  };
}

function view(over: Partial<ProcessRuleView>): ProcessRuleView {
  return {
    rule_key: "r0",
    idx: 0,
    trigger_kind: "row",
    trigger_column: null,
    trigger_column_label: null,
    trigger_columns: [],
    trigger_labels: [],
    trigger_join: "any",
    trigger_op: "created-or-updated",
    expected_columns: ["a"],
    expected_labels: ["A"],
    within_seconds: 0,
    notify_on_expect: true,
    label: null,
    ...over,
  };
}

describe("buildGraph", () => {
  it("maps a row rule to a START edge and a column rule to a column edge", () => {
    const rules: ProcessRuleView[] = [
      view({ rule_key: "r0", trigger_kind: "row", trigger_column: null, expected_columns: ["a"] }),
      view({ rule_key: "r1", trigger_kind: "column", trigger_column: "a", expected_columns: ["b"] }),
    ];
    const g = buildGraph(rules, (c) => c.toUpperCase());
    expect(g.edges).toHaveLength(2);
    expect(g.edges[0]).toMatchObject({ from: START_ID, to: "a", rule_key: "r0" });
    expect(g.edges[1]).toMatchObject({ from: "a", to: "b", rule_key: "r1" });
    // START node + a + b.
    expect(g.nodes.map((n) => n.id).sort()).toEqual([START_ID, "a", "b"]);
    // START carries a fixed label; columns resolve via labelOf.
    expect(g.nodes.find((n) => n.id === START_ID)?.kind).toBe("start");
    expect(g.nodes.find((n) => n.id === "a")?.label).toBe("A");
  });

  it("fans an 'and' rule (N expected columns) into N edges sharing one rule_key", () => {
    const rules: ProcessRuleView[] = [
      view({ rule_key: "and1", trigger_kind: "column", trigger_column: "a", expected_columns: ["b", "c"], within_seconds: 3600 }),
    ];
    const g = buildGraph(rules);
    expect(g.edges).toHaveLength(2);
    expect(g.edges.every((e) => e.rule_key === "and1")).toBe(true);
    expect(g.edges.every((e) => e.within_seconds === 3600)).toBe(true);
    expect(g.edges.map((e) => e.to).sort()).toEqual(["b", "c"]);
  });

  it("renders a redacted (null-label) column without leaking the field key", () => {
    const rules: ProcessRuleView[] = [view({ expected_columns: ["secret"] })];
    // labelOf returns null for an unreadable column.
    const g = buildGraph(rules, () => null);
    const node = g.nodes.find((n) => n.id === "secret");
    expect(node?.label).toBeNull();
  });

  it("mints a positional rule_key for a write-shape rule that omits one", () => {
    const rules: ProcessRuleInput[] = [
      { trigger_kind: "row", trigger_op: "created", expected_columns: ["a"] },
    ];
    const g = buildGraph(rules);
    expect(g.edges[0].rule_key).toBe("r0");
    expect(g.edges[0].notify_on_expect).toBe(true); // defaults to notify
  });

  it("tags a plain (single/row) rule's edges join='any'", () => {
    const g = buildGraph([
      view({ rule_key: "r0", trigger_kind: "row", expected_columns: ["a"] }),
      view({ rule_key: "r1", trigger_kind: "column", trigger_column: "a", expected_columns: ["b"] }),
    ]);
    expect(g.edges.every((e) => e.join === "any")).toBe(true);
  });

  it("treats trigger_column (singular) as a one-entry set", () => {
    const g = buildGraph([
      view({ rule_key: "r1", trigger_kind: "column", trigger_column: "a", trigger_columns: [], expected_columns: ["b"] }),
    ]);
    expect(g.edges).toHaveLength(1);
    expect(g.edges[0]).toMatchObject({ from: "a", to: "b", join: "any" });
  });

  it("fans an ALL-join (N triggers x M expected) into N*M edges sharing one rule_key + join='all'", () => {
    const g = buildGraph([
      view({
        rule_key: "j1",
        trigger_kind: "column",
        trigger_column: "a",
        trigger_columns: ["a", "b"],
        trigger_join: "all",
        expected_columns: ["c", "d"],
        within_seconds: 60,
      }),
    ]);
    // 2 triggers x 2 expected = 4 edges.
    expect(g.edges).toHaveLength(4);
    expect(g.edges.every((e) => e.rule_key === "j1")).toBe(true);
    expect(g.edges.every((e) => e.join === "all")).toBe(true);
    expect(g.edges.every((e) => e.within_seconds === 60)).toBe(true);
    const pairs = g.edges.map((e) => `${e.from}->${e.to}`).sort();
    expect(pairs).toEqual(["a->c", "a->d", "b->c", "b->d"]);
    // every trigger + expected column is a node.
    expect(g.nodes.map((n) => n.id).sort()).toEqual([START_ID, "a", "b", "c", "d"]);
  });

  it("prefers trigger_columns over the singular alias when both are present", () => {
    const g = buildGraph([
      view({
        rule_key: "j",
        trigger_kind: "column",
        trigger_column: "a",
        trigger_columns: ["a", "b"],
        trigger_join: "all",
        expected_columns: ["c"],
      }),
    ]);
    expect(g.edges.map((e) => e.from).sort()).toEqual(["a", "b"]);
  });
});

describe("reachableFromStart", () => {
  it("returns START plus everything on a path from it", () => {
    const edges = [edge(START_ID, "a"), edge("a", "b"), edge("b", "c")];
    expect([...reachableFromStart(edges)].sort()).toEqual([START_ID, "a", "b", "c"]);
  });

  it("omits an orphan column with no inbound path from START", () => {
    const edges = [edge(START_ID, "a"), edge("x", "y")]; // x,y disconnected
    const r = reachableFromStart(edges);
    expect(r.has("a")).toBe(true);
    expect(r.has("x")).toBe(false);
    expect(r.has("y")).toBe(false);
  });
});

describe("hasCycle / wouldCreateCycle", () => {
  it("a linear chain is acyclic", () => {
    expect(hasCycle([edge(START_ID, "a"), edge("a", "b")])).toBe(false);
  });

  it("a diamond (two paths converging) is acyclic", () => {
    const edges = [
      edge(START_ID, "a"),
      edge("a", "b"),
      edge("a", "c"),
      edge("b", "d"),
      edge("c", "d"),
    ];
    expect(hasCycle(edges)).toBe(false);
  });

  it("detects a 2-node back-edge cycle", () => {
    expect(hasCycle([edge("a", "b"), edge("b", "a")])).toBe(true);
  });

  it("detects a multi-hop cycle", () => {
    expect(hasCycle([edge("a", "b"), edge("b", "c"), edge("c", "a")])).toBe(true);
  });

  it("wouldCreateCycle rejects a self-loop", () => {
    expect(wouldCreateCycle([], { from: "a", to: "a" })).toBe(true);
  });

  it("wouldCreateCycle rejects an edge that closes a loop but allows a forward edge", () => {
    const existing = [edge(START_ID, "a"), edge("a", "b")];
    // b -> a would close a cycle a->b->a.
    expect(wouldCreateCycle(existing, { from: "b", to: "a" })).toBe(true);
    // b -> c is a fresh forward edge — fine.
    expect(wouldCreateCycle(existing, { from: "b", to: "c" })).toBe(false);
  });
});

describe("validate", () => {
  it("passes a clean linear DAG with no warnings", () => {
    const g = buildGraph([
      view({ rule_key: "r0", trigger_kind: "row", expected_columns: ["a"] }),
      view({ rule_key: "r1", trigger_kind: "column", trigger_column: "a", expected_columns: ["b"] }),
    ]);
    const res = validate(g);
    expect(res.errors).toEqual([]);
    expect(res.warnings).toEqual([]);
  });

  it("flags a self-loop as an error", () => {
    const g = { nodes: [{ id: "a", label: "A", kind: "column" as const }], edges: [edge("a", "a")] };
    const res = validate(g);
    expect(res.errors.some((e) => /itself/i.test(e))).toBe(true);
  });

  it("flags a duplicate edge as an error", () => {
    const g = {
      nodes: [
        { id: START_ID, label: "Start", kind: "start" as const },
        { id: "a", label: "A", kind: "column" as const },
      ],
      edges: [edge(START_ID, "a"), edge(START_ID, "a")],
    };
    const res = validate(g);
    expect(res.errors.some((e) => /duplicate/i.test(e))).toBe(true);
  });

  it("flags a cycle as an error", () => {
    const g = {
      nodes: [
        { id: "a", label: "A", kind: "column" as const },
        { id: "b", label: "B", kind: "column" as const },
      ],
      edges: [edge("a", "b"), edge("b", "a")],
    };
    const res = validate(g);
    expect(res.errors.some((e) => /cycle/i.test(e))).toBe(true);
  });

  it("warns (does not error) on a column unreachable from START", () => {
    const g = {
      nodes: [
        { id: START_ID, label: "Start", kind: "start" as const },
        { id: "a", label: "A", kind: "column" as const },
        { id: "x", label: "X", kind: "column" as const },
        { id: "y", label: "Y", kind: "column" as const },
      ],
      edges: [edge(START_ID, "a"), edge("x", "y")],
    };
    const res = validate(g);
    expect(res.errors).toEqual([]);
    // x is orphaned (nothing feeds x); y is reachable from x but not START.
    expect(res.warnings.some((w) => /not reachable/i.test(w))).toBe(true);
  });

  it("uses a generic placeholder (never the field key) for a redacted node", () => {
    const g = {
      nodes: [{ id: "secret_field", label: null, kind: "column" as const }],
      edges: [edge("secret_field", "secret_field")],
    };
    const res = validate(g);
    expect(res.errors.join(" ")).not.toContain("secret_field");
    expect(res.errors.join(" ")).toMatch(/hidden column/i);
  });
});

describe("layout", () => {
  it("places START at layer 0 and pushes each downstream column one layer right", () => {
    const g = buildGraph([
      view({ rule_key: "r0", trigger_kind: "row", expected_columns: ["a"] }),
      view({ rule_key: "r1", trigger_kind: "column", trigger_column: "a", expected_columns: ["b"] }),
    ]);
    const l = layout(g);
    const byId = new Map(l.nodes.map((n) => [n.id, n]));
    expect(byId.get(START_ID)?.layer).toBe(0);
    expect(byId.get("a")?.layer).toBe(1);
    expect(byId.get("b")?.layer).toBe(2);
    // x increases with layer.
    expect(byId.get("a")!.x).toBeGreaterThan(byId.get(START_ID)!.x);
    expect(byId.get("b")!.x).toBeGreaterThan(byId.get("a")!.x);
  });

  it("is deterministic (same input -> identical positions)", () => {
    const g = buildGraph([
      view({ rule_key: "r0", trigger_kind: "row", expected_columns: ["a", "b"] }),
    ]);
    expect(layout(g)).toEqual(layout(g));
  });

  it("uses the LONGEST path for a diamond convergence node", () => {
    // START->a->b->d and START->a->d : d must sit at the deeper (b) layer + 1.
    const g = buildGraph([
      view({ rule_key: "r0", trigger_kind: "row", expected_columns: ["a"] }),
      view({ rule_key: "r1", trigger_kind: "column", trigger_column: "a", expected_columns: ["b", "d"] }),
      view({ rule_key: "r2", trigger_kind: "column", trigger_column: "b", expected_columns: ["d"] }),
    ]);
    const byId = new Map(layout(g).nodes.map((n) => [n.id, n]));
    // a=1, b=2, d=max(a+1=2 via a->d, b+1=3 via b->d) = 3.
    expect(byId.get("d")?.layer).toBe(3);
  });
});

describe("packRules", () => {
  it("round-trips a single-expected rule set through buildGraph -> packRules", () => {
    const rules: ProcessRuleInput[] = [
      { rule_key: "r0", trigger_kind: "row", trigger_column: null, trigger_op: "created-or-updated", expected_columns: ["a"], within_seconds: 0, notify_on_expect: true },
      { rule_key: "r1", trigger_kind: "column", trigger_column: "a", trigger_op: "created-or-updated", expected_columns: ["b"], within_seconds: 3600, notify_on_expect: true },
    ];
    const packed = packRules(buildGraph(rules).edges);
    expect(packed).toEqual([
      { trigger_kind: "row", trigger_column: null, trigger_op: "created-or-updated", expected_columns: ["a"], within_seconds: 0, notify_on_expect: true },
      { trigger_kind: "column", trigger_column: "a", trigger_op: "created-or-updated", expected_columns: ["b"], within_seconds: 3600, notify_on_expect: true },
    ]);
  });

  it("groups edges sharing (trigger, window, op, notify) into one 'and' rule", () => {
    const edges = [
      edge("a", "b", { within_seconds: 3600, rule_key: "x" }),
      edge("a", "c", { within_seconds: 3600, rule_key: "y" }),
    ];
    const packed = packRules(edges);
    expect(packed).toHaveLength(1);
    expect(packed[0]).toMatchObject({
      trigger_kind: "column",
      trigger_column: "a",
      expected_columns: ["b", "c"],
      within_seconds: 3600,
    });
  });

  it("keeps edges from the same trigger but DIFFERENT windows as separate rules", () => {
    const edges = [
      edge("a", "b", { within_seconds: 60 }),
      edge("a", "c", { within_seconds: 3600 }),
    ];
    const packed = packRules(edges);
    expect(packed).toHaveLength(2);
  });

  it("drops a self-loop edge (never persisted)", () => {
    const packed = packRules([edge("a", "a"), edge(START_ID, "a")]);
    expect(packed).toHaveLength(1);
    expect(packed[0].trigger_kind).toBe("row");
  });

  it("maps a START-origin edge to a row-trigger rule", () => {
    const packed = packRules([edge(START_ID, "a")]);
    expect(packed[0]).toMatchObject({ trigger_kind: "row", trigger_column: null });
  });
});

describe("packRules — AND-join (fan-in)", () => {
  it("packs edges sharing a rule_key with join='all' into ONE rule (trigger_columns=all sources)", () => {
    // a->c and b->c share rule_key 'j' and join='all' => expect c when a AND b.
    const edges = [
      edge("a", "c", { rule_key: "j", join: "all", within_seconds: 60 }),
      edge("b", "c", { rule_key: "j", join: "all", within_seconds: 60 }),
    ];
    const packed = packRules(edges);
    expect(packed).toHaveLength(1);
    expect(packed[0]).toMatchObject({
      trigger_kind: "column",
      trigger_join: "all",
      trigger_columns: ["a", "b"],
      trigger_column: "a", // back-compat alias == trigger_columns[0]
      expected_columns: ["c"],
      within_seconds: 60,
    });
  });

  it("groups an ALL-join's multiple expected columns into one rule (N*M edges)", () => {
    const edges = [
      edge("a", "c", { rule_key: "j", join: "all" }),
      edge("a", "d", { rule_key: "j", join: "all" }),
      edge("b", "c", { rule_key: "j", join: "all" }),
      edge("b", "d", { rule_key: "j", join: "all" }),
    ];
    const packed = packRules(edges);
    expect(packed).toHaveLength(1);
    expect(packed[0].trigger_columns).toEqual(["a", "b"]);
    expect(packed[0].expected_columns).toEqual(["c", "d"]);
    expect(packed[0].trigger_join).toBe("all");
  });

  it("round-trips an ALL-join through buildGraph -> packRules", () => {
    const rules: ProcessRuleInput[] = [
      {
        rule_key: "j1",
        trigger_kind: "column",
        trigger_columns: ["a", "b"],
        trigger_join: "all",
        trigger_op: "created-or-updated",
        expected_columns: ["c"],
        within_seconds: 120,
        notify_on_expect: true,
      },
    ];
    const packed = packRules(buildGraph(rules).edges);
    expect(packed).toEqual([
      {
        trigger_kind: "column",
        trigger_join: "all",
        trigger_columns: ["a", "b"],
        trigger_column: "a",
        trigger_op: "created-or-updated",
        expected_columns: ["c"],
        within_seconds: 120,
        notify_on_expect: true,
      },
    ]);
  });

  it("keeps two separate any-join rules distinct from an all-join into the same target", () => {
    // Two INDEPENDENT any triggers into c (separate rule_keys) stay two rules.
    const edges = [
      edge("a", "c", { rule_key: "ra", join: "any" }),
      edge("b", "c", { rule_key: "rb", join: "any" }),
    ];
    const packed = packRules(edges);
    expect(packed).toHaveLength(2);
    expect(packed.every((r) => r.trigger_join === undefined || r.trigger_join === "any")).toBe(true);
  });

  it("does NOT merge two all-joins that carry DIFFERENT rule_keys", () => {
    const edges = [
      edge("a", "c", { rule_key: "j1", join: "all" }),
      edge("b", "c", { rule_key: "j2", join: "all" }),
    ];
    const packed = packRules(edges);
    expect(packed).toHaveLength(2);
  });
});

describe("validate — AND-join", () => {
  it("passes a clean all-join (a AND b -> c) with no errors", () => {
    const g = buildGraph([
      view({ rule_key: "r0", trigger_kind: "row", expected_columns: ["a", "b"] }),
      view({
        rule_key: "j",
        trigger_kind: "column",
        trigger_columns: ["a", "b"],
        trigger_join: "all",
        expected_columns: ["c"],
      }),
    ]);
    const res = validate(g);
    expect(res.errors).toEqual([]);
    expect(res.warnings).toEqual([]);
  });

  it("errors when an all-join trigger column is also its own expected column (self-loop)", () => {
    const g = buildGraph([
      view({
        rule_key: "j",
        trigger_kind: "column",
        trigger_columns: ["a", "b"],
        trigger_join: "all",
        expected_columns: ["b", "c"], // b triggers AND is expected -> self-loop
      }),
    ]);
    const res = validate(g);
    expect(res.errors.some((e) => /itself/i.test(e))).toBe(true);
  });

  it("warns (does not error) when an all-join trigger column is unreachable from START", () => {
    // a is fed from START; b is orphaned. The join a AND b -> c can never complete.
    const g = buildGraph(
      [
        view({ rule_key: "r0", trigger_kind: "row", expected_columns: ["a"] }),
        view({
          rule_key: "j",
          trigger_kind: "column",
          trigger_columns: ["a", "b"],
          trigger_join: "all",
          expected_columns: ["c"],
        }),
      ],
      (c) => c.toUpperCase(),
    );
    const res = validate(g);
    expect(res.errors).toEqual([]);
    expect(res.warnings.some((w) => /not reachable/i.test(w) && /B/.test(w))).toBe(true);
  });

  it("errors on a cycle formed through an all-join leg", () => {
    // c is expected by the join (a AND b -> c); c then triggers b -> cycle b->c->b.
    const g = buildGraph([
      view({
        rule_key: "j",
        trigger_kind: "column",
        trigger_columns: ["a", "b"],
        trigger_join: "all",
        expected_columns: ["c"],
      }),
      view({ rule_key: "r1", trigger_kind: "column", trigger_column: "c", expected_columns: ["b"] }),
    ]);
    const res = validate(g);
    expect(res.errors.some((e) => /cycle/i.test(e))).toBe(true);
  });
});
