// DRAFT FLOW (non-owner cell editing) — useSheet hook contract, TESTS FIRST.
//
// Decisions implemented:
//  (1A) ONLY non-owners go through a draft; owners keep real-time direct commit.
//  (2)  drafts are SERVER-PERSISTED in a personal draft box, so they survive a
//       reload / refetch / device change (only a submit or discard clears them).
//
// Contract (per the draft-flow spec):
//  * commitDraft(node,col,value) writes the local draft (value visible at once),
//    captures base_version via baseVersionFor, and persists via saveCellDraft.
//  * the draft value overlays the snapshot in the `nodes` memo (shows live).
//  * draftCount / draftKey / draftList expose the box; drafts hydrate from
//    listCellDrafts on mount AND survive a refetch (re-hydrated, not cleared).
//  * submitDrafts() files ONE multi-change CR: on "suggested" it adds a pending
//    mark per submitted cell (carrying the CR id), clears the local box, refetches.
//  * discardDraft / discardAllDrafts drop the box.
//  * the OWNER updateCell dispatch path stays untouched (no draft is written).

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ArborClient, CellDraft, Outcome, Snapshot } from "../api";
import { cellKey, useSheet } from "./useSheet";

const SHEET = "S";
const NODE = "X";
const COL = "col:budget";

function snapshot(): Snapshot {
  return {
    sheet: { name: SHEET, structural_owner: "A", settings: {} },
    columns: [
      {
        name: COL,
        field: "budget",
        label: "Budget",
        type: "number",
        is_label: false,
        column_owner: "C",
        editors: [],
        // non-owner: the viewer (A) can only suggest → drafts.
        can_edit: false,
      },
    ],
    nodes: [
      {
        name: NODE,
        parent: null,
        lft: 1,
        rgt: 2,
        label: "Task X",
        values: { [COL]: 1000 },
        versions: { [COL]: 3 },
        can_change_structure: false,
      },
    ],
    label_column: null,
    actor: "A",
  };
}

// A client backed by an in-memory draft box so save / list / submit / discard
// behave like the (Phase 1) server endpoints.
function makeClient(
  overrides: Partial<ArborClient> = {},
  seed: CellDraft[] = [],
): {
  client: ArborClient;
  draftCalls: { method: string; params: Record<string, unknown> }[];
  box: () => CellDraft[];
} {
  const draftCalls: { method: string; params: Record<string, unknown> }[] = [];
  const box = new Map<string, CellDraft>();
  for (const d of seed) box.set(cellKey(d.node, d.column), d);
  let seq = box.size;
  const client: ArborClient = {
    executeAction: vi.fn(async () => ({ kind: "executed", data: { version: 4 } }) as Outcome),
    getSheetSnapshot: vi.fn(async () => snapshot()),
    agentChat: vi.fn(async () => {}),
    listCellDrafts: vi.fn(async () => {
      draftCalls.push({ method: "list", params: {} });
      return Array.from(box.values());
    }),
    saveCellDraft: vi.fn(async (_sheet, node, column, value, base_version) => {
      draftCalls.push({ method: "save", params: { node, column, value, base_version } });
      const key = cellKey(node, column);
      const name = box.get(key)?.name ?? `DRAFT-${++seq}`;
      box.set(key, { name, node, column, value, base_version });
      return { name };
    }),
    discardCellDraft: vi.fn(async (_sheet, node, column) => {
      draftCalls.push({ method: "discardOne", params: { node, column } });
      box.delete(cellKey(node, column));
      return { ok: true };
    }),
    discardCellDrafts: vi.fn(async () => {
      draftCalls.push({ method: "discardAll", params: {} });
      const n = box.size;
      box.clear();
      return { discarded: n };
    }),
    submitCellDrafts: vi.fn(async () => {
      draftCalls.push({ method: "submit", params: {} });
      box.clear();
      return { kind: "suggested", change_request: "CR9", resolved_approver: "C" } as Outcome;
    }),
    ...overrides,
  };
  return { client, draftCalls, box: () => Array.from(box.values()) };
}

async function mounted(client: ArborClient) {
  const hook = renderHook(() => useSheet(client, SHEET));
  await waitFor(() => expect(hook.result.current.snapshot).not.toBeNull());
  return hook;
}

describe("useSheet — draft flow (non-owner cell editing)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("commitDraft persists via saveCellDraft, shows the value LOCALLY, and bumps draftCount — NOT executeAction", async () => {
    const { client } = makeClient();
    const { result } = await mounted(client);

    await act(async () => {
      await result.current.commitDraft(NODE, COL, 500);
    });

    // saveCellDraft was called with the cell's base_version (3 from the snapshot).
    expect(client.saveCellDraft).toHaveBeenCalledWith(SHEET, NODE, COL, 500, 3);
    // NO instant CR — the owner direct-commit path is never taken.
    expect(client.executeAction).not.toHaveBeenCalled();
    // the value shows live (overlaid in the nodes memo) + the box reflects it.
    expect(result.current.draftCount).toBe(1);
    expect(result.current.draftKey(NODE, COL)).toBe(true);
    const node = result.current.nodes.find((n) => n.name === NODE)!;
    expect(node.values[COL]).toBe(500);
  });

  it("reverts the local draft + raises an error banner when saveCellDraft fails", async () => {
    const { client } = makeClient({
      saveCellDraft: vi.fn(async () => {
        throw new Error("offline");
      }),
    });
    const { result } = await mounted(client);

    await act(async () => {
      await result.current.commitDraft(NODE, COL, 500);
    });

    expect(result.current.draftCount).toBe(0);
    expect(result.current.banner?.kind).toBe("error");
    // value reverted to the snapshot's authoritative value.
    const node = result.current.nodes.find((n) => n.name === NODE)!;
    expect(node.values[COL]).toBe(1000);
  });

  it("hydrates the draft box from listCellDrafts on mount (drafts survive reload)", async () => {
    const { client } = makeClient({}, [
      { name: "D1", node: NODE, column: COL, value: 777, base_version: 3 },
    ]);
    const { result } = await mounted(client);

    await waitFor(() => expect(result.current.draftCount).toBe(1));
    expect(result.current.draftKey(NODE, COL)).toBe(true);
    // the hydrated draft value overlays the snapshot.
    const node = result.current.nodes.find((n) => n.name === NODE)!;
    expect(node.values[COL]).toBe(777);
  });

  it("drafts SURVIVE a refetch (re-hydrated from the server, not cleared)", async () => {
    const { client } = makeClient();
    const { result } = await mounted(client);
    await act(async () => {
      await result.current.commitDraft(NODE, COL, 500);
    });
    expect(result.current.draftCount).toBe(1);

    await act(async () => {
      await result.current.refetch();
    });
    // still there — listCellDrafts returned the persisted row.
    expect(result.current.draftCount).toBe(1);
    expect(result.current.nodes.find((n) => n.name === NODE)!.values[COL]).toBe(500);
  });

  it("submitDrafts → suggested banner + a pending mark per submitted cell + box cleared", async () => {
    const { client } = makeClient();
    const { result } = await mounted(client);
    await act(async () => {
      await result.current.commitDraft(NODE, COL, 500);
    });

    let outcome: Outcome | undefined;
    await act(async () => {
      outcome = await result.current.submitDrafts();
    });

    expect(client.submitCellDrafts).toHaveBeenCalledWith(SHEET);
    expect(outcome?.kind).toBe("suggested");
    // a pending-approval mark now targets the submitted cell, carrying the CR id.
    const mark = result.current.pending.find((p) => p.key === cellKey(NODE, COL));
    expect(mark).toBeDefined();
    expect(mark!.change_request).toBe("CR9");
    // the suggested banner names the approver + carries the CR.
    expect(result.current.banner?.kind).toBe("suggested");
    expect(result.current.banner?.change_request).toBe("CR9");
    // local box cleared (the server deleted the rows; refetch re-hydrates empty).
    expect(result.current.draftCount).toBe(0);
  });

  it("discardDraft drops a single draft; discardAllDrafts empties the box", async () => {
    const { client } = makeClient();
    const { result } = await mounted(client);
    await act(async () => {
      await result.current.commitDraft(NODE, COL, 500);
    });
    expect(result.current.draftCount).toBe(1);

    await act(async () => {
      await result.current.discardDraft(NODE, COL);
    });
    expect(client.discardCellDraft).toHaveBeenCalledWith(SHEET, NODE, COL);
    expect(result.current.draftCount).toBe(0);

    // add two then discard all.
    await act(async () => {
      await result.current.commitDraft(NODE, COL, 600);
      await result.current.commitDraft(NODE, "col:other", 1);
    });
    await act(async () => {
      await result.current.discardAllDrafts();
    });
    expect(client.discardCellDrafts).toHaveBeenCalledWith(SHEET);
    expect(result.current.draftCount).toBe(0);
  });
});
