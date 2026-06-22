// Runnable: bench-free (vitest + jsdom; no Frappe, no running app).
//
// Snapshot-driven edit-vs-suggest affordances and the authoritative-Outcome
// contract (web-ui.md "Outcome-rendering contract"). The UI NEVER re-derives
// ACL: it renders the affordance from snapshot `can_edit` hints, but commits or
// reverts strictly on the returned Outcome — the server wins over any
// client-side prediction. These fill the gaps left by Cell.test / the App
// integration suite.
//
// Case IDs:
//   WEB_UI-013  Editor (not owner) on a column edits directly
//   WEB_UI-015  Non-owned cell is visually distinguished before interaction
//   WEB_UI-020  suggested arrives despite UI predicting executed → optimistic rollback
//   WEB_UI-021  owners_must_use_change_requests → owner edit yields suggested
//   WEB_UI-025  two rapid commits on the same cell are serialized, not interleaved
//   WEB_UI-086  executed arrives where UI rendered suggest → commit wins (symmetric to -020)
//   WEB_UI-087  network failure reverts optimistic state and offers retry

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "../App";
import { Cell } from "./cells/Cell";
import { loginAs, mockClient } from "../test/fixture";
import type { SnapshotColumn } from "../api";

const statusCol = (canEdit: boolean): SnapshotColumn => ({
  name: "col:status",
  field: "status",
  label: "Status",
  type: "single-select-split",
  is_label: false,
  column_owner: "C",
  editors: ["B"],
  can_edit: canEdit,
  options: { groups: [{ label: "Stage", options: ["todo", "doing", "done"] }] },
});

function budgetCellOf(rowTestId: string): HTMLElement {
  return screen
    .getByTestId(rowTestId)
    .querySelector('[data-column="col:budget"] [data-testid="cell"]') as HTMLElement;
}

describe("edit-vs-suggest affordance derivation (WEB_UI-013/-015)", () => {
  it("an editor (can_edit hint true) renders the column in edit mode, not suggest (WEB_UI-013)", () => {
    // B is an editor on col:status → snapshot supplies can_edit=true.
    render(<Cell column={statusCol(true)} value={["todo"]} onCommit={() => {}} />);
    expect(screen.getByTestId("split-cell")).toHaveAttribute("data-mode", "edit");
  });

  it("a non-owner sees suggest affordance on every editable cell (WEB_UI-015)", async () => {
    // E owns nothing → all per_column_can_edit false.
    const { client } = mockClient({ snapshot: loginAs("E") });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    const notes = screen
      .getByTestId("row-X")
      .querySelector('[data-column="col:notes"] [data-testid="cell"]')!;
    const status = screen.getByTestId("row-X").querySelector('[data-column="col:status"] [data-testid="cell"]')!;
    expect(notes).toHaveAttribute("data-mode", "suggest");
    expect(status).toHaveAttribute("data-mode", "suggest");
  });
});

describe("authoritative Outcome wins over client prediction (WEB_UI-020/-021/-086)", () => {
  it("suggested arrives where the UI optimistically committed → value rolls back (WEB_UI-020)", async () => {
    // B owns col:notes (UI predicts executed) but the server returns suggested.
    const { client } = mockClient({
      snapshot: loginAs("B"),
      outcome: { kind: "suggested", change_request: "CR7", resolved_approver: "B" },
    });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    const notes = screen
      .getByTestId("row-X")
      .querySelector('[data-column="col:notes"] [data-testid="cell"]')!;
    expect(notes).toHaveAttribute("data-mode", "edit"); // optimistic prediction
    fireEvent.doubleClick(notes);
    fireEvent.change(screen.getByTestId("cell-input"), { target: { value: "rolled-back text" } });
    fireEvent.blur(screen.getByTestId("cell-input"));

    const banner = await screen.findByTestId("banner");
    expect(banner).toHaveAttribute("data-kind", "suggested");
    // optimistic value rolled back to the snapshot value "v1"
    expect(notes).toHaveTextContent("v1");
    expect(notes).not.toHaveTextContent("rolled-back text");
  });

  it("owners_must_use_change_requests → an owner's edit renders suggested to self (WEB_UI-021)", async () => {
    const snap = loginAs("B", { sheet: { name: "S", structural_owner: "A", settings: { owners_must_use_change_requests: true } } });
    const { client } = mockClient({
      snapshot: snap,
      outcome: { kind: "suggested", change_request: "CR8", resolved_approver: "B" },
    });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    const notes = screen
      .getByTestId("row-X")
      .querySelector('[data-column="col:notes"] [data-testid="cell"]')!;
    fireEvent.doubleClick(notes);
    fireEvent.change(screen.getByTestId("cell-input"), { target: { value: "owner edit" } });
    fireEvent.blur(screen.getByTestId("cell-input"));
    const banner = await screen.findByTestId("banner");
    expect(banner).toHaveAttribute("data-kind", "suggested");
    expect(banner).toHaveTextContent("Suggestion sent to B");
    expect(notes).toHaveTextContent("v1"); // not committed
  });

  it("executed arrives where the UI rendered suggest → value commits (WEB_UI-086)", async () => {
    // Stale viewer flag: col:budget can_edit=false but the server returns executed.
    const { client } = mockClient({
      snapshot: loginAs("A"),
      outcome: { kind: "executed" },
    });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    const budget = budgetCellOf("row-X");
    expect(budget).toHaveAttribute("data-mode", "suggest"); // UI predicted suggest
    fireEvent.doubleClick(budget);
    fireEvent.change(screen.getByTestId("cell-input"), { target: { value: "777" } });
    fireEvent.blur(screen.getByTestId("cell-input"));
    await waitFor(() => expect(screen.getByTestId("banner")).toHaveAttribute("data-kind", "saved"));
    // authoritative executed → optimistic value stays committed
    expect(budget).toHaveTextContent("777");
  });
});

describe("network failure & serialization (WEB_UI-025/-087)", () => {
  it("a network error reverts the optimistic value and shows an error banner (WEB_UI-087)", async () => {
    const client = {
      executeAction: async () => {
        throw new Error("network down");
      },
      getSheetSnapshot: async () => loginAs("B"),
      agentChat: async () => {},
    };
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    const notes = screen
      .getByTestId("row-X")
      .querySelector('[data-column="col:notes"] [data-testid="cell"]')!;
    fireEvent.doubleClick(notes);
    fireEvent.change(screen.getByTestId("cell-input"), { target: { value: "will fail" } });
    fireEvent.blur(screen.getByTestId("cell-input"));
    const banner = await screen.findByTestId("banner");
    expect(banner).toHaveAttribute("data-kind", "error");
    expect(banner).toHaveTextContent("network down");
    // reverted to snapshot value, not the failed optimistic write
    expect(notes).toHaveTextContent("v1");
    expect(notes).not.toHaveTextContent("will fail");
  });

  it("two rapid commits on the same cell are serialized, last write wins (WEB_UI-025)", async () => {
    const order: string[] = [];
    let unblockFirst!: () => void;
    const firstGate = new Promise<void>((res) => (unblockFirst = res));
    let n = 0;
    const client = {
      executeAction: async (_action: string, params: Record<string, unknown>) => {
        const call = ++n;
        order.push(`start:${String(params.value)}`);
        if (call === 1) await firstGate; // hold the first in flight
        order.push(`end:${String(params.value)}`);
        return { kind: "executed" as const };
      },
      getSheetSnapshot: async () => loginAs("B"),
      agentChat: async () => {},
    };
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    const notes = screen
      .getByTestId("row-X")
      .querySelector('[data-column="col:notes"] [data-testid="cell"]') as HTMLElement;

    // commit "a"
    fireEvent.doubleClick(notes);
    fireEvent.change(screen.getByTestId("cell-input"), { target: { value: "a" } });
    fireEvent.blur(screen.getByTestId("cell-input"));
    await waitFor(() => expect(order).toContain("start:a"));

    // commit "ab" before "a" resolves
    fireEvent.doubleClick(notes);
    fireEvent.change(screen.getByTestId("cell-input"), { target: { value: "ab" } });
    fireEvent.blur(screen.getByTestId("cell-input"));

    // the second must NOT start until the first ends (serialized via the tail chain)
    unblockFirst();
    await waitFor(() => expect(order).toContain("end:ab"));
    expect(order.indexOf("end:a")).toBeLessThan(order.indexOf("start:ab"));
    // final committed cell shows the last write
    expect(within(notes).getByText("ab")).toBeInTheDocument();
  });
});
