// Runnable: bench-free (vitest + jsdom; no Frappe, no running app).
//
// Split-column cells wired through the App shell (the SelectSplitCell unit suite
// covers the control in isolation; this asserts the executeAction call shape and
// the Outcome contract end-to-end). Single-select-split commits a 1-element
// array; a non-owned split opens suggest-mode and renders the suggested Outcome.
//
// Case IDs:
//   WEB_UI-027  single-select-split commit sends a single-element array value
//   WEB_UI-030  split control on a non-owned column is suggest-only → CR to owner

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "../App";
import { loginAs, mockClient } from "../test/fixture";

function statusCellOf(rowTestId: string): HTMLElement {
  return screen
    .getByTestId(rowTestId)
    .querySelector('[data-column="col:status"] [data-testid="split-cell"]') as HTMLElement;
}

describe("split-column cells through the App shell", () => {
  it("owner selecting an option sends updateCell with a single-element array (WEB_UI-027)", async () => {
    // C owns col:status → edit mode, executed.
    const { client, calls } = mockClient({ snapshot: loginAs("C"), outcome: { kind: "executed" } });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    const status = statusCellOf("row-X");
    expect(status).toHaveAttribute("data-mode", "edit");
    fireEvent.click(within(status).getByTestId("segment-done"));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({
      action: "updateCell",
      // Feature 1: dispatch threads the cell's base_version (0 — the fixture cell
      // carries no version) as the opt-in optimistic-concurrency guard.
      params: { sheet: "S", node: "X", column: "col:status", value: ["done"], base_version: 0 },
    });
  });

  it("non-owner interacting with the split stages a DRAFT (suggest mode), shows it locally, no instant CR (WEB_UI-030, draft flow)", async () => {
    // E owns nothing → suggest mode. Decision 1A: a non-owner selection no longer
    // fires an instant suggestChanges CR + reverts. It stages a server-persisted
    // draft — the selected segment STAYS active locally, the cell is tagged a
    // draft, and no suggested banner appears until the user submits the box.
    const { client, calls, draftCalls } = mockClient({ snapshot: loginAs("E"), drafts: [] });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    const statusCell = screen
      .getByTestId("row-X")
      .querySelector('[data-column="col:status"] [data-testid="cell"]') as HTMLElement;
    expect(statusCell).toHaveAttribute("data-mode", "suggest");
    fireEvent.click(within(statusCell).getByTestId("segment-doing"));

    await waitFor(() => expect(draftCalls.some((c) => c.method === "save")).toBe(true));
    // staged with the chosen single-element array value; never an executeAction.
    const save = draftCalls.find((c) => c.method === "save")!;
    expect(save.params).toMatchObject({ column: "col:status", value: ["doing"] });
    expect(calls.find((c) => c.action === "updateCell")).toBeUndefined();
    // the draft value wins locally (doing active, not reverted to todo) + tagged.
    expect(within(statusCell).getByTestId("segment-doing")).toHaveClass("is-active");
    expect(statusCell).toHaveAttribute("data-draft", "true");
    expect(screen.queryByTestId("banner")).not.toBeInTheDocument();
  });
});
