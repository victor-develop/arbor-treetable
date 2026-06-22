// The canonical sheet S, expressed as the snapshot shape the server returns
// (mirrors tests/TEST-PLAN.md §2 and the core serialize_snapshot output). A
// `loginAs(persona)` helper sets the `viewer` ACL hints so component tests can
// assert edit-vs-suggest affordances without re-running ACL — exactly the
// boundary web-ui.md describes.

import type { ArborClient, Outcome, Snapshot, SnapshotColumn, SnapshotNode } from "../api";

// NestedSet layout (lft/rgt) for: R[P1[X], P2[Y,Z]]
//   R 1..12 | P1 2..5 | X 3..4 | P2 6..11 | Y 7..8 | Z 9..10
const NODES: Omit<SnapshotNode, "can_change_structure">[] = [
  { name: "R", parent: null, lft: 1, rgt: 12, is_group: true, idx: 0, label: "Root", values: {} },
  { name: "P1", parent: "R", lft: 2, rgt: 5, is_group: true, idx: 0, label: "Phase 1", values: {} },
  { name: "X", parent: "P1", lft: 3, rgt: 4, is_group: false, idx: 0, label: "Task X", values: {} },
  { name: "P2", parent: "R", lft: 6, rgt: 11, is_group: true, idx: 1, label: "Phase 2", values: {} },
  { name: "Y", parent: "P2", lft: 7, rgt: 8, is_group: false, idx: 0, label: "Task Y", values: {} },
  { name: "Z", parent: "P2", lft: 9, rgt: 10, is_group: false, idx: 1, label: "Task Z", values: {} },
];

const VALUES: Record<string, Record<string, unknown>> = {
  R: { "col:name": "Root" },
  P1: { "col:name": "Phase 1" },
  X: { "col:name": "Task X", "col:status": ["todo"], "col:budget": 1000, "col:notes": "v1" },
  P2: { "col:name": "Phase 2" },
  Y: { "col:name": "Task Y", "col:budget": 5000 },
  Z: { "col:name": "Task Z", "col:budget": 12000 },
};

type Persona = "A" | "B" | "C" | "D" | "E" | "F" | "G";

function columns(): SnapshotColumn[] {
  return [
    {
      name: "col:name",
      field: "name",
      label: "Name",
      type: "text",
      is_label: true,
      column_owner: "B",
      editors: [],
      can_edit: false,
    },
    {
      name: "col:status",
      field: "status",
      label: "Status",
      type: "single-select-split",
      is_label: false,
      column_owner: "C",
      editors: ["B"],
      can_edit: false,
      options: { groups: [{ label: "Stage", options: ["todo", "doing", "done"] }] },
    },
    {
      name: "col:budget",
      field: "budget",
      label: "Budget",
      type: "number",
      is_label: false,
      column_owner: "C",
      editors: [],
      can_edit: false,
    },
    {
      name: "col:notes",
      field: "notes",
      label: "Notes",
      type: "multiline-text",
      is_label: false,
      column_owner: "B",
      editors: [],
      can_edit: false,
    },
    {
      name: "col:tags",
      field: "tags",
      label: "Tags",
      type: "multi-select-split",
      is_label: false,
      column_owner: "C",
      editors: [],
      can_edit: false,
      options: { groups: [{ label: "Tags", options: ["urgent", "backend", "frontend"] }] },
    },
  ];
}

// Per-persona column edit rights (Axis 2) per PERMISSIONS §2.
const COLUMN_RIGHTS: Record<Persona, Set<string>> = {
  A: new Set(),
  B: new Set(["col:name", "col:notes", "col:status"]), // owner of name/notes, editor on status
  C: new Set(["col:status", "col:budget", "col:tags"]),
  D: new Set(),
  E: new Set(),
  F: new Set(),
  G: new Set(),
};

// Per-persona structural rights (Axis 1): A owns R/P1/X; D owns P2/Y/Z.
const STRUCT_RIGHTS: Record<Persona, Set<string>> = {
  A: new Set(["R", "P1", "X"]),
  B: new Set(),
  C: new Set(),
  D: new Set(["P2", "Y", "Z"]),
  E: new Set(),
  F: new Set(),
  G: new Set(),
};

export function loginAs(persona: Persona, overrides?: Partial<Snapshot>): Snapshot {
  const cols = columns().map((c) => ({
    ...c,
    can_edit: COLUMN_RIGHTS[persona].has(c.name),
  }));
  const nodes: SnapshotNode[] = NODES.map((n) => ({
    ...n,
    values: VALUES[n.name] ?? {},
    can_change_structure: STRUCT_RIGHTS[persona].has(n.name),
  }));
  return {
    sheet: { name: "S", structural_owner: "A", settings: {} },
    columns: cols,
    nodes,
    label_column: "col:name",
    actor: persona,
    viewer: { can_add_column: persona === "A" },
    ...overrides,
  };
}

// A mock client whose executeAction / getSheetSnapshot / agentChat are spies the
// test controls. Mirrors the Vitest mock boundary described in web-ui.md.
export function mockClient(opts?: {
  snapshot?: Snapshot;
  outcome?: Outcome | ((action: string, params: Record<string, unknown>) => Outcome);
  frames?: import("../api").AgentFrame[];
}): {
  client: ArborClient;
  calls: { action: string; params: Record<string, unknown> }[];
  snapshotCalls: string[];
  chatCalls: { sheet: string; message: string }[];
} {
  const calls: { action: string; params: Record<string, unknown> }[] = [];
  const snapshotCalls: string[] = [];
  const chatCalls: { sheet: string; message: string }[] = [];
  const snap = opts?.snapshot ?? loginAs("A");

  const client: ArborClient = {
    executeAction: async (action, params) => {
      calls.push({ action, params });
      const o = opts?.outcome;
      if (typeof o === "function") return o(action, params);
      return o ?? { kind: "executed" };
    },
    getSheetSnapshot: async (sheet) => {
      snapshotCalls.push(sheet);
      return snap;
    },
    agentChat: async (sheet, message, onFrame) => {
      chatCalls.push({ sheet, message });
      for (const f of opts?.frames ?? []) onFrame(f);
    },
  };
  return { client, calls, snapshotCalls, chatCalls };
}
