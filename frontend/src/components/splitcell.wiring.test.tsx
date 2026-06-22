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

  it("non-owner interacting with the split opens suggest-mode and renders the suggested Outcome (WEB_UI-030)", async () => {
    // E owns nothing → suggest mode; server returns a CR to owner C.
    const { client, calls } = mockClient({
      snapshot: loginAs("E"),
      outcome: { kind: "suggested", change_request: "CR3", resolved_approver: "C" },
    });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    const status = statusCellOf("row-X");
    expect(status).toHaveAttribute("data-mode", "suggest");
    fireEvent.click(within(status).getByTestId("segment-doing"));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].action).toBe("updateCell");
    expect(calls[0].params).toMatchObject({ column: "col:status", value: ["doing"] });

    const banner = await screen.findByTestId("banner");
    expect(banner).toHaveAttribute("data-kind", "suggested");
    expect(banner).toHaveTextContent("Suggestion sent to C");
    // segments revert to the snapshot value (["todo"]) until approval
    expect(within(status).getByTestId("segment-todo")).toHaveClass("is-active");
    expect(within(status).getByTestId("segment-doing")).not.toHaveClass("is-active");
  });
});
