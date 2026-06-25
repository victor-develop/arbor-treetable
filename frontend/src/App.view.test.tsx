// Feature 2 (shareable views) — App-level wiring tests, written RED before the
// shell parses ?v=. On mount the ConnectedShell reads a ?v= base64url token,
// resolves the visible/ordered columns over the (read-ACL-filtered) snapshot,
// seeds the collapsed set from view.collapsed, and keeps the URL in sync via
// history.replaceState. None of this issues an executeAction.

import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { loginAs, mockClient } from "./test/fixture";
import { encodeView, decodeView, type SheetView } from "./lib/view";

// Drive window.location.search without navigating (jsdom).
function setSearch(search: string): void {
  const url = `http://localhost/${search ? `?${search.replace(/^\?/, "")}` : ""}`;
  window.history.replaceState({}, "", url);
}

beforeEach(() => setSearch(""));
afterEach(() => {
  setSearch("");
  vi.restoreAllMocks();
});

describe("App — applies ?v= on mount", () => {
  it("hides a column named in the ?v= token (visibility from the link)", async () => {
    const view: SheetView = { v: 1, hidden: ["col:budget"], order: [] };
    setSearch(`v=${encodeView(view)}`);
    const { client } = mockClient({ snapshot: loginAs("A") });
    render(<App client={client} sheetName="S" />);
    const table = within(await screen.findByTestId("tree-table"));
    // budget header is hidden by the shared view; status remains. Scope to the
    // table so these resolve to the column headers only, not the ViewMenu toggle.
    expect(table.queryByText("Budget")).not.toBeInTheDocument();
    expect(table.getByText("Status")).toBeInTheDocument();
  });

  it("a forwarded link can NEVER reveal a column the recipient cannot read", async () => {
    // Recipient's snapshot omits col:budget (read-ACL filtered). The token still
    // orders/sizes it — it must never appear (Feature 2 ∩ Feature 3).
    const view: SheetView = {
      v: 1,
      hidden: [],
      order: ["col:budget", "col:status"],
      width: { "col:budget": 500 },
    };
    setSearch(`v=${encodeView(view)}`);
    const snap = loginAs("A");
    const restricted = {
      ...snap,
      columns: snap.columns.filter((c) => c.name !== "col:budget"),
    };
    const { client } = mockClient({ snapshot: restricted });
    render(<App client={client} sheetName="S" />);
    const table = within(await screen.findByTestId("tree-table"));
    expect(table.queryByText("Budget")).not.toBeInTheDocument();
  });

  it("seeds the collapsed set from view.collapsed (P2 subtree hidden on mount)", async () => {
    const view: SheetView = { v: 1, hidden: [], order: [], collapsed: ["P2"] };
    setSearch(`v=${encodeView(view)}`);
    const { client } = mockClient({ snapshot: loginAs("A") });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    // P2 is collapsed, so its children Y and Z are not rendered as rows.
    expect(screen.getByTestId("row-P2")).toBeInTheDocument();
    expect(screen.queryByTestId("row-Y")).not.toBeInTheDocument();
    expect(screen.queryByTestId("row-Z")).not.toBeInTheDocument();
  });

  it("a malformed ?v= token falls back to the default view (all readable columns)", async () => {
    setSearch("v=!!!garbage!!!");
    const { client } = mockClient({ snapshot: loginAs("A") });
    render(<App client={client} sheetName="S" />);
    const table = within(await screen.findByTestId("tree-table"));
    // default view shows every readable data column (scope to the table so these
    // resolve to the column headers, not the ViewMenu toggles).
    expect(table.getByText("Status")).toBeInTheDocument();
    expect(table.getByText("Budget")).toBeInTheDocument();
    expect(table.getByText("Notes")).toBeInTheDocument();
  });
});

describe("App — keeps the URL in sync via replaceState", () => {
  it("writes a decodable ?v= token to the URL after mount", async () => {
    const replaceSpy = vi.spyOn(window.history, "replaceState");
    const { client } = mockClient({ snapshot: loginAs("A") });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await waitFor(() => expect(replaceSpy).toHaveBeenCalled());
    // the last replaceState URL carries a v= token that decodes to a SheetView.
    const lastUrl = String(replaceSpy.mock.calls.at(-1)![2]);
    const token = new URL(lastUrl, "http://localhost").searchParams.get("v");
    expect(token).toBeTruthy();
    expect(decodeView(token!)).not.toBeNull();
  });

  it("does NOT issue an executeAction for any view change (presentation only)", async () => {
    const { client, calls } = mockClient({ snapshot: loginAs("A") });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    // mounting + applying a view performs no mutations.
    expect(calls).toHaveLength(0);
  });
});
