// FEATURE 1 (optimistic concurrency) — useSheet hook contract, TESTS FIRST (RED).
//
// Contract (authz_tdd.txt FEATURE 1 + authz_spec.txt FE):
//  * dispatch("updateCell", ...) sends a base_version taken from the snapshot's
//    per-cell versions map (snapshot.versions[node][col]); 0 for an empty cell.
//  * An Outcome with error === "VERSION_CONFLICT" sets a kind:"conflict" banner
//    carrying the server's current_value AND the user's rejected text, and does
//    NOT commit the optimistic value (it is reverted).
//  * resolveConflict("redo") refetches the whole snapshot then clears the
//    conflict so the editor can reopen on the fresh base; resolveConflict
//    ("discard") just clears the conflict + optimistic value.
//  * After a successful executed write, the returned data.version is folded into
//    a local versions map so a SECOND edit to the same cell carries the bumped
//    base (no self-conflict).

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ArborClient, Outcome, Snapshot } from "../api";
import { cellKey, useSheet } from "./useSheet";

const SHEET = "S";
const NODE = "X";
const COL = "col:status";

function snapshotWith(version: number): Snapshot {
  return {
    sheet: { name: SHEET, structural_owner: "A", settings: {} },
    columns: [
      {
        name: COL,
        field: "status",
        label: "Status",
        type: "text",
        is_label: false,
        column_owner: "C",
        editors: ["B"],
        can_edit: true,
      },
    ],
    nodes: [
      {
        name: NODE,
        parent: null,
        lft: 1,
        rgt: 2,
        label: "Task X",
        values: { [COL]: "todo" },
        // the per-cell version map the FE threads as base_version
        versions: { [COL]: version },
        can_change_structure: false,
      },
    ],
    label_column: null,
    actor: "B",
  };
}

function makeClient(overrides: Partial<ArborClient> = {}): {
  client: ArborClient;
  calls: { action: string; params: Record<string, unknown> }[];
} {
  const calls: { action: string; params: Record<string, unknown> }[] = [];
  const client: ArborClient = {
    executeAction: vi.fn(async (action, params) => {
      calls.push({ action, params });
      return { kind: "executed", data: { version: 2 } } as Outcome;
    }),
    getSheetSnapshot: vi.fn(async () => snapshotWith(1)),
    agentChat: vi.fn(async () => {}),
    ...overrides,
  };
  return { client, calls };
}

async function mounted(client: ArborClient) {
  const hook = renderHook(() => useSheet(client, SHEET));
  await waitFor(() => expect(hook.result.current.snapshot).not.toBeNull());
  return hook;
}

describe("useSheet — optimistic concurrency (Feature 1)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("threads base_version from snapshot.versions into an updateCell dispatch", async () => {
    const { client, calls } = makeClient();
    const { result } = await mounted(client);

    await act(async () => {
      await result.current.dispatch(
        "updateCell",
        { sheet: SHEET, node: NODE, column: COL, value: "doing" },
        { optimisticKey: cellKey(NODE, COL), optimisticValue: "doing" },
      );
    });

    const call = calls.find((c) => c.action === "updateCell");
    expect(call).toBeDefined();
    expect(call!.params.base_version).toBe(1);
  });

  it("VERSION_CONFLICT sets a conflict banner with current_value + rejected text and reverts the optimistic value", async () => {
    const conflict: Outcome = {
      kind: "read",
      error: "VERSION_CONFLICT",
      data: { node: NODE, column: COL, current_version: 5, current_value: "done" },
    };
    const { client } = makeClient({
      executeAction: vi.fn(async () => conflict),
    });
    const { result } = await mounted(client);

    await act(async () => {
      await result.current.dispatch(
        "updateCell",
        { sheet: SHEET, node: NODE, column: COL, value: "doing" },
        { optimisticKey: cellKey(NODE, COL), optimisticValue: "doing" },
      );
    });

    expect(result.current.banner?.kind).toBe("conflict");
    // banner must surface BOTH the authoritative current value and the rejected edit
    expect(result.current.banner?.current_value).toBe("done");
    expect(result.current.banner?.rejected).toBe("doing");
    // the optimistic value must NOT have been committed — the rendered cell is unchanged
    const node = result.current.nodes.find((n) => n.name === NODE)!;
    expect(node.values[COL]).toBe("todo");
  });

  it("resolveConflict('redo') refetches the snapshot and clears the conflict", async () => {
    const conflict: Outcome = {
      kind: "read",
      error: "VERSION_CONFLICT",
      data: { node: NODE, column: COL, current_version: 5, current_value: "done" },
    };
    const getSheetSnapshot = vi
      .fn<ArborClient["getSheetSnapshot"]>()
      .mockResolvedValueOnce(snapshotWith(1)) // initial mount
      .mockResolvedValueOnce(snapshotWith(5)); // the redo refetch
    const { client } = makeClient({
      executeAction: vi.fn(async () => conflict),
      getSheetSnapshot,
    });
    const { result } = await mounted(client);

    await act(async () => {
      await result.current.dispatch(
        "updateCell",
        { sheet: SHEET, node: NODE, column: COL, value: "doing" },
        { optimisticKey: cellKey(NODE, COL), optimisticValue: "doing" },
      );
    });
    expect(result.current.banner?.kind).toBe("conflict");

    await act(async () => {
      await result.current.resolveConflict("redo");
    });

    // a fresh snapshot was pulled and the conflict banner cleared
    expect(getSheetSnapshot).toHaveBeenCalledTimes(2);
    expect(result.current.banner?.kind).not.toBe("conflict");
  });

  it("folds the returned version so a second same-cell edit carries the bumped base", async () => {
    let nextVersion = 2;
    const calls: { params: Record<string, unknown> }[] = [];
    const client: ArborClient = {
      executeAction: vi.fn(async (_action, params) => {
        calls.push({ params });
        return { kind: "executed", data: { version: nextVersion++ } } as Outcome;
      }),
      getSheetSnapshot: vi.fn(async () => snapshotWith(1)),
      agentChat: vi.fn(async () => {}),
    };
    const { result } = await mounted(client);

    await act(async () => {
      await result.current.dispatch(
        "updateCell",
        { sheet: SHEET, node: NODE, column: COL, value: "a" },
        { optimisticKey: cellKey(NODE, COL), optimisticValue: "a" },
      );
    });
    // first edit used the snapshot base (1) and got back version 2
    expect(calls[0].params.base_version).toBe(1);

    await act(async () => {
      await result.current.dispatch(
        "updateCell",
        { sheet: SHEET, node: NODE, column: COL, value: "b" },
        { optimisticKey: cellKey(NODE, COL), optimisticValue: "b" },
      );
    });
    // second edit must carry the FOLDED version (2), not the stale snapshot base (1)
    expect(calls[1].params.base_version).toBe(2);
  });
});
