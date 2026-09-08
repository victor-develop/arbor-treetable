// Feature 2 (shareable views) — App-level wiring tests, written RED before the
// shell parses ?v=. On mount the ConnectedShell reads a ?v= base64url token,
// resolves the visible/ordered columns over the (read-ACL-filtered) snapshot,
// seeds the collapsed set from view.collapsed, and keeps the URL in sync via
// history.replaceState. None of this issues an executeAction.

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

  it("a collapse the user performs lands in the ?v= token (not just the mount seed)", async () => {
    // `collapsed` is a facet OF the view, so the share link carries a collapse
    // the recipient can see. It was write-once for a while — the token only ever
    // echoed what it was seeded with — which also made a saved view store the
    // seed instead of the arrangement on screen.
    const replaceSpy = vi.spyOn(window.history, "replaceState");
    const { client } = mockClient({ snapshot: loginAs("A") });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    fireEvent.click(screen.getByTestId("chevron-P2"));
    await waitFor(() => expect(screen.queryByTestId("row-Y")).not.toBeInTheDocument());

    const lastUrl = String(replaceSpy.mock.calls.at(-1)![2]);
    const token = new URL(lastUrl, "http://localhost").searchParams.get("v");
    expect(decodeView(token!)?.collapsed).toEqual(["P2"]);
  });

  it("does NOT issue an executeAction for any view change (presentation only)", async () => {
    const { client, calls } = mockClient({ snapshot: loginAs("A") });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    // mounting + applying a view performs no mutations.
    expect(calls).toHaveLength(0);
  });
});

// --- the shared column order -------------------------------------------------
// Two layers, deliberately distinct: dragging a header reorders MY view (no
// round-trip, no approval), and an explicit "save order for everyone" promotes
// that arrangement to the SHARED stored order through the ONE governed dispatch.

// The data-column headers the grid renders, left to right.
const headerOrder = (): string[] =>
  Array.from(document.querySelectorAll("thead th[data-testid^='col-head-']")).map(
    (th) => th.getAttribute("data-testid")!.replace("col-head-", ""),
  );

// The order the live ?v= token carries (the view state, independent of render).
const tokenOrder = (): string[] | undefined =>
  decodeView(new URL(window.location.href).searchParams.get("v"))?.order;

async function mountGrid(opts?: Parameters<typeof mockClient>[0]) {
  const mocked = mockClient({ snapshot: loginAs("A"), ...opts });
  render(<App client={mocked.client} sheetName="S" />);
  await screen.findByTestId("tree-table");
  return mocked;
}

// Drag `from`'s header grip onto `to`'s header (ids, as the grid holds them).
function dragHeader(fromField: string, toColumn: string): void {
  fireEvent.dragStart(screen.getByTestId(`col-grip-${fromField}`));
  fireEvent.dragEnter(screen.getByTestId(`col-head-${toColumn}`));
  fireEvent.dragOver(screen.getByTestId(`col-head-${toColumn}`));
  fireEvent.drop(screen.getByTestId(`col-head-${toColumn}`));
}

describe("App — dragging a column header reorders MY view only", () => {
  it("re-renders in the dragged order and issues NO executeAction", async () => {
    const { calls } = await mountGrid();
    expect(headerOrder()).toEqual(["col:status", "col:budget", "col:notes", "col:tags"]);
    dragHeader("notes", "col:status");
    await waitFor(() =>
      expect(headerOrder()).toEqual(["col:notes", "col:status", "col:budget", "col:tags"]),
    );
    // Presentation state: the drag is a view change, never a mutation.
    expect(calls).toHaveLength(0);
    // ...and it is mirrored into the shareable ?v= token like every view change.
    expect(tokenOrder()).toEqual(["col:notes", "col:status", "col:budget", "col:tags"]);
  });

  it("leaves a HIDDEN column's place alone (the drag never saw it)", async () => {
    setSearch(`v=${encodeView({ v: 1, hidden: ["col:budget"], order: [] })}`);
    await mountGrid();
    expect(headerOrder()).toEqual(["col:status", "col:notes", "col:tags"]);
    dragHeader("tags", "col:status");
    // The visible slice permutes; col:budget keeps its slot in the full order,
    // so "save for everyone" would still ship a complete, sane ordering.
    await waitFor(() =>
      expect(tokenOrder()).toEqual(["col:tags", "col:budget", "col:status", "col:notes"]),
    );
  });

  it("a forwarded token can still never reveal an unreadable column after a drag", async () => {
    // REVEAL-IMPOSSIBILITY holds for the new path too: the drag folds its result
    // into the order resolved from the (ACL-filtered) snapshot, so a column the
    // viewer cannot read is not in the render and not in the emitted order.
    setSearch(`v=${encodeView({ v: 1, hidden: [], order: ["col:budget", "col:status"] })}`);
    const snap = loginAs("A");
    const restricted = { ...snap, columns: snap.columns.filter((c) => c.name !== "col:budget") };
    await mountGrid({ snapshot: restricted });
    expect(headerOrder()).not.toContain("col:budget");
    dragHeader("tags", "col:status");
    await waitFor(() => expect(tokenOrder()).not.toContain("col:budget"));
    expect(headerOrder()).not.toContain("col:budget");
  });
});

describe("App — save order for everyone", () => {
  const saveOrder = async () => {
    // The View disclosure holds the action (the same surface that owns hide /
    // reorder / resize). The click is flushed inside act because the dispatch is
    // promise-chained: the banner / refetch / override-drop all land a tick
    // later, and asserting synchronously would be scheduling-dependent.
    const button = await screen.findByTestId("view-save-order");
    await act(async () => {
      fireEvent.click(button);
    });
  };

  it("dispatches setColumnOrder with the exact resolved order", async () => {
    const { calls } = await mountGrid();
    dragHeader("notes", "col:status");
    await waitFor(() => expect(screen.queryByTestId("view-save-order")).toBeInTheDocument());
    await saveOrder();
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].action).toBe("setColumnOrder");
    expect(calls[0].params).toEqual({
      sheet: "S",
      order: ["col:notes", "col:status", "col:budget", "col:tags"],
    });
  });

  it("is not offered until the local order differs from the shared one", async () => {
    await mountGrid();
    expect(screen.queryByTestId("view-save-order")).not.toBeInTheDocument();
  });

  it("on EXECUTED it drops the local override (so a later shared change is not masked)", async () => {
    const { snapshotCalls } = await mountGrid({ outcome: { kind: "executed" } });
    dragHeader("notes", "col:status");
    await waitFor(() => expect(tokenOrder()).toEqual([
      "col:notes",
      "col:status",
      "col:budget",
      "col:tags",
    ]));
    const before = snapshotCalls.length;
    await saveOrder();
    // The override is gone: the view no longer pins an order, and the grid falls
    // back to the (mock) snapshot order. Against the real server the snapshot
    // WOULD come back reordered; keeping the override would hide the next
    // person's reorder behind our own stale copy.
    await waitFor(() => expect(tokenOrder()).toEqual([]));
    await waitFor(() => expect(snapshotCalls.length).toBeGreaterThan(before));
    expect(headerOrder()).toEqual(["col:status", "col:budget", "col:notes", "col:tags"]);
    // Nothing left to save.
    await waitFor(() => expect(screen.queryByTestId("view-save-order")).not.toBeInTheDocument());
  });

  it("on SUGGESTED it KEEPS the local override and surfaces the existing CR banner", async () => {
    await mountGrid({
      outcome: { kind: "suggested", change_request: "CR-1", resolved_approver: "A" },
    });
    dragHeader("notes", "col:status");
    await waitFor(() => expect(screen.queryByTestId("view-save-order")).toBeInTheDocument());
    await saveOrder();
    // The requester goes on seeing their own arrangement while the CR is open.
    await waitFor(() => expect(screen.getByTestId("banner")).toHaveTextContent(/Suggestion sent/));
    expect(tokenOrder()).toEqual(["col:notes", "col:status", "col:budget", "col:tags"]);
    expect(headerOrder()).toEqual(["col:notes", "col:status", "col:budget", "col:tags"]);
    expect(screen.getByTestId("view-save-order")).toBeInTheDocument();
  });
});

// The affordance is withdrawn for a read-filtered viewer, because setColumnOrder
// demands the COMPLETE non-label set and a filtered snapshot cannot express it.
// The victim of the old behaviour was the STRUCTURAL OWNER: the one actor with
// the authority to make the write got a button that 400'd every time, forever,
// with no in-UI way to recover.
describe("App — save order is withdrawn when the snapshot was read-filtered", () => {
  const filteredOwnerSnapshot = () => {
    const snap = loginAs("A");
    return {
      ...snap,
      // col:budget is owner-only and A is not its owner: dropped server-side.
      columns: snap.columns.filter((c) => c.name !== "col:budget"),
      viewer: { ...snap.viewer, columns_filtered: true },
    };
  };

  it("offers no button after a drag, and dispatches nothing", async () => {
    const { calls } = await mountGrid({ snapshot: filteredOwnerSnapshot() });
    dragHeader("notes", "col:status");
    // The drag itself still works — the arrangement is simply personal.
    await waitFor(() => expect(headerOrder()).toEqual(["col:notes", "col:status", "col:tags"]));
    expect(screen.queryByTestId("view-save-order")).not.toBeInTheDocument();
    expect(screen.getByTestId("view-save-order-blocked")).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it("still offers it when the server says nothing was filtered", async () => {
    const snap = loginAs("A");
    await mountGrid({
      snapshot: { ...snap, viewer: { ...snap.viewer, columns_filtered: false } },
    });
    dragHeader("notes", "col:status");
    await waitFor(() => expect(screen.getByTestId("view-save-order")).toBeInTheDocument());
  });

  it("the blocked note reveals nothing about the hidden column", async () => {
    await mountGrid({ snapshot: filteredOwnerSnapshot() });
    dragHeader("notes", "col:status");
    const note = await screen.findByTestId("view-save-order-blocked");
    expect(note.textContent).not.toMatch(/budget/i);
  });
});
