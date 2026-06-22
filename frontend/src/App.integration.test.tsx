import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "./App";
import { loginAs, mockClient } from "./test/fixture";

describe("App — snapshot-driven shell wiring", () => {
  it("loads the snapshot and renders the tree (WEB_UI-001)", async () => {
    const { client } = mockClient({ snapshot: loginAs("B") });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    expect(screen.getByTestId("sheet-name")).toHaveTextContent("Sheet: S");
    expect(screen.getByTestId("row-X")).toBeInTheDocument();
  });

  it("owner edit → executeAction(updateCell) → executed commit, no CR banner (WEB_UI-011)", async () => {
    const { client, calls } = mockClient({ snapshot: loginAs("B"), outcome: { kind: "executed" } });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    // notes cell on X is owned by B (can_edit true)
    const notesCell = screen.getByTestId("row-X").querySelector('[data-column="col:notes"] [data-testid="cell"]')!;
    expect(notesCell).toHaveAttribute("data-mode", "edit");
    fireEvent.doubleClick(notesCell);
    fireEvent.change(screen.getByTestId("cell-input"), { target: { value: "ship by Q3" } });
    fireEvent.blur(screen.getByTestId("cell-input"));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({
      action: "updateCell",
      // Feature 1: dispatch threads the cell's base_version (0 — the fixture cell
      // carries no version) as the opt-in optimistic-concurrency guard.
      params: { sheet: "S", node: "X", column: "col:notes", value: "ship by Q3", base_version: 0 },
    });
    await waitFor(() => expect(screen.getByTestId("banner")).toHaveAttribute("data-kind", "saved"));
  });

  it("non-owner edit → suggested → revert + 'Suggestion sent to C' banner (WEB_UI-014)", async () => {
    const { client, calls } = mockClient({
      snapshot: loginAs("A"), // A owns no columns
      outcome: { kind: "suggested", change_request: "CR1", resolved_approver: "C" },
    });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    const budgetCell = screen.getByTestId("row-X").querySelector('[data-column="col:budget"] [data-testid="cell"]')!;
    expect(budgetCell).toHaveAttribute("data-mode", "suggest");
    fireEvent.doubleClick(budgetCell);
    fireEvent.change(screen.getByTestId("cell-input"), { target: { value: "500" } });
    fireEvent.blur(screen.getByTestId("cell-input"));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].action).toBe("updateCell");
    const banner = await screen.findByTestId("banner");
    expect(banner).toHaveAttribute("data-kind", "suggested");
    expect(banner).toHaveTextContent("Suggestion sent to C");
    expect(screen.getByTestId("banner-cr")).toHaveTextContent("CR1");
    // value reverted to snapshot (1000), optimistic 500 not committed
    expect(budgetCell).toHaveTextContent("1000");
  });

  it("server error code surfaces an error banner, not a silent commit (WEB_UI-023/-085)", async () => {
    // A generic (non-concurrency) server error code still lands the error banner.
    // VERSION_CONFLICT now has its own conflict-banner path (Feature 1), exercised
    // in useSheet.test.ts; here we assert the generic error-code fallback.
    const { client } = mockClient({
      snapshot: loginAs("C"),
      outcome: { kind: "read", error: "INTERNAL_ERROR" },
    });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    const budgetCell = screen.getByTestId("row-X").querySelector('[data-column="col:budget"] [data-testid="cell"]')!;
    fireEvent.doubleClick(budgetCell);
    fireEvent.change(screen.getByTestId("cell-input"), { target: { value: "999" } });
    fireEvent.blur(screen.getByTestId("cell-input"));
    const banner = await screen.findByTestId("banner");
    expect(banner).toHaveAttribute("data-kind", "error");
    expect(banner).toHaveTextContent("INTERNAL_ERROR");
  });

  it("move with one-end authority → suggested banner names co-approver (WEB_UI-041)", async () => {
    const { client, calls } = mockClient({
      snapshot: loginAs("A"),
      outcome: { kind: "suggested", change_request: "CR9", resolved_approver: "D", co_approvers: ["A"] },
    });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    const rowX = screen.getByTestId("row-X");
    const rowP2 = screen.getByTestId("row-P2");
    rowP2.getBoundingClientRect = () =>
      ({ top: 0, height: 90, left: 0, right: 0, bottom: 90, width: 0, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
    fireEvent.dragStart(rowX);
    fireEvent.dragOver(rowP2);
    fireEvent.drop(rowP2, { clientY: 45 });

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].action).toBe("moveNode");
    const banner = await screen.findByTestId("banner");
    expect(banner).toHaveTextContent("Suggestion sent to D");
    expect(banner).toHaveTextContent("co-approver: A");
  });
});
