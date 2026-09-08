// SavedViewsMenu (Feature: saved views) — the named-arrangement picker. These
// specs drive App at the integration boundary (like the AgentTokensModal specs)
// so the wiring is proven too: the list splits into My views / Shared, applying
// a row really re-renders the table through resolveColumns, saving sends the
// LIVE overlay, publish flips only the visibility, delete needs a second click,
// and every server refusal is shown verbatim instead of swallowed.
//
// The two invariants worth restating: nothing is auto-applied on load, and an
// applied view can never surface a column the viewer cannot read.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import App from "../App";
import type { ArborClient, SavedViewView } from "../api";
import { loginAs, mockClient } from "../test/fixture";
import type { SheetView } from "../lib/view";

// jsdom leaves ?v= from a previous spec on window.location — clear it so the
// mounted shell starts from the DEFAULT view, not a leaked overlay.
beforeEach(() => window.history.replaceState({}, "", "http://localhost/"));

function savedView(over: Partial<SavedViewView> & { name: string }): SavedViewView {
  return {
    sheet: "S",
    label: over.name,
    author: "A",
    visibility: "private",
    is_mine: true,
    view: { v: 1, hidden: [], order: [] },
    ...over,
  };
}

type Reject = { list?: string; save?: string; del?: string };

// A client with an in-memory saved-view store so save / publish / delete
// round-trip the way the server does (a save is private; a patch writes only the
// fields it was given).
function viewClient(opts?: { rows?: SavedViewView[]; reject?: Reject; snapshot?: ReturnType<typeof loginAs> }) {
  const base = mockClient({ snapshot: opts?.snapshot ?? loginAs("A") });
  const store: SavedViewView[] = (opts?.rows ?? []).map((r) => ({ ...r }));
  const calls: { method: string; args: unknown }[] = [];
  let seq = store.length;
  const client: ArborClient = {
    ...base.client,
    listSheetViews: async (sheet) => {
      calls.push({ method: "list", args: sheet });
      if (opts?.reject?.list) throw new Error(opts.reject.list);
      return store.map((r) => ({ ...r }));
    },
    saveSheetView: async (params) => {
      calls.push({ method: "save", args: params });
      if (opts?.reject?.save) throw new Error(opts.reject.save);
      if (params.name) {
        const row = store.find((r) => r.name === params.name)!;
        if (params.label !== undefined) row.label = params.label;
        if (params.view !== undefined) row.view = params.view;
        if (params.visibility !== undefined) row.visibility = params.visibility;
        return { ...row };
      }
      const row = savedView({
        name: `SV-${++seq}`,
        label: params.label ?? "",
        view: params.view ?? { v: 1, hidden: [], order: [] },
        visibility: params.visibility ?? "private",
      });
      store.push(row);
      return { ...row };
    },
    deleteSheetView: async (name) => {
      calls.push({ method: "delete", args: name });
      if (opts?.reject?.del) throw new Error(opts.reject.del);
      const i = store.findIndex((r) => r.name === name);
      if (i >= 0) store.splice(i, 1);
      return { ok: true };
    },
  };
  return { client, calls, store };
}

// The picker lives in a <details> next to "View"; jsdom keeps the content in the
// DOM either way. The panel mounts SYNCHRONOUSLY but its list arrives from a
// promise, so wait for the loading placeholder to clear — asserting straight
// after the panel appears is the scheduling-dependent flake this repo has been
// bitten by (it passes in isolation and fails in a full run, or vice versa).
async function openPicker() {
  await screen.findByTestId("tree-table");
  fireEvent.click(screen.getByText("Saved views"));
  const menu = within(await screen.findByTestId("saved-views-menu"));
  await waitFor(() => expect(screen.queryByTestId("saved-views-loading")).toBeNull());
  return menu;
}

describe("SavedViewsMenu — listing", () => {
  it("splits the viewer's own views from the ones shared with them", async () => {
    const { client } = viewClient({
      rows: [
        savedView({ name: "SV-mine", label: "Just budget" }),
        savedView({
          name: "SV-shared",
          label: "Team layout",
          author: "D",
          is_mine: false,
          visibility: "sheet",
        }),
      ],
    });
    render(<App client={client} sheetName="S" />);
    const menu = await openPicker();

    expect(menu.getByText("My views")).toBeInTheDocument();
    expect(menu.getByText("Shared")).toBeInTheDocument();
    expect(menu.getByTestId("saved-view-SV-mine")).toHaveTextContent("Just budget");
    const shared = menu.getByTestId("saved-view-SV-shared");
    expect(shared).toHaveTextContent("Team layout");
    expect(shared).toHaveTextContent("by D"); // provenance for someone else's view

    // Someone else's view offers no update / publish / delete — those are the
    // author's affordances (and the server 403s them anyway).
    expect(menu.queryByTestId("saved-view-update-SV-shared")).toBeNull();
    expect(menu.queryByTestId("saved-view-publish-SV-shared")).toBeNull();
    expect(menu.queryByTestId("saved-view-delete-SV-shared")).toBeNull();
  });

  it("shows no Shared section and an empty note when there is nothing saved", async () => {
    const { client } = viewClient({ rows: [] });
    render(<App client={client} sheetName="S" />);
    const menu = await openPicker();
    expect(menu.getByTestId("saved-views-mine-empty")).toBeInTheDocument();
    expect(menu.queryByText("Shared")).toBeNull();
  });
});

describe("SavedViewsMenu — applying", () => {
  it("does NOT auto-apply a saved view on load (the user must pick)", async () => {
    const { client } = viewClient({
      rows: [savedView({ name: "SV-1", label: "No budget", view: { v: 1, hidden: ["col:budget"], order: [] } })],
    });
    render(<App client={client} sheetName="S" />);
    const table = within(await screen.findByTestId("tree-table"));
    await openPicker(); // the list has loaded…
    // …and the table is still the DEFAULT view: every readable column shows.
    expect(table.getByText("Budget")).toBeInTheDocument();
    expect(table.getByText("Status")).toBeInTheDocument();
  });

  it("applying a view hides the columns it names, and Default view restores them", async () => {
    const { client } = viewClient({
      rows: [savedView({ name: "SV-1", label: "No budget", view: { v: 1, hidden: ["col:budget"], order: [] } })],
    });
    render(<App client={client} sheetName="S" />);
    const menu = await openPicker();

    fireEvent.click(menu.getByTestId("saved-view-apply-SV-1"));
    const table = () => within(screen.getByTestId("tree-table"));
    await waitFor(() => expect(table().queryByText("Budget")).not.toBeInTheDocument());
    expect(table().getByText("Status")).toBeInTheDocument();

    fireEvent.click(menu.getByTestId("saved-views-reset"));
    await waitFor(() => expect(table().getByText("Budget")).toBeInTheDocument());
  });

  it("applying a view seeds the collapsed subtrees it carries", async () => {
    const { client } = viewClient({
      rows: [
        savedView({
          name: "SV-1",
          label: "Phases only",
          view: { v: 1, hidden: [], order: [], collapsed: ["P2"] },
        }),
      ],
    });
    render(<App client={client} sheetName="S" />);
    const menu = await openPicker();
    expect(screen.getByTestId("row-Y")).toBeInTheDocument();

    fireEvent.click(menu.getByTestId("saved-view-apply-SV-1"));
    await waitFor(() => expect(screen.queryByTestId("row-Y")).not.toBeInTheDocument());
    expect(screen.getByTestId("row-P2")).toBeInTheDocument();
  });

  it("a shared view can NEVER surface a column the viewer cannot read", async () => {
    // The viewer's snapshot omits col:budget (read-ACL filtered); the shared view
    // still orders and sizes it. resolveColumns starts from the snapshot, so the
    // column is structurally unreachable.
    const snap = loginAs("A");
    const restricted = { ...snap, columns: snap.columns.filter((c) => c.name !== "col:budget") };
    const view: SheetView = {
      v: 1,
      hidden: [],
      order: ["col:budget", "col:status"],
      width: { "col:budget": 500 },
    };
    const { client } = viewClient({
      snapshot: restricted,
      rows: [
        savedView({ name: "SV-x", label: "Everything", author: "D", is_mine: false, visibility: "sheet", view }),
      ],
    });
    render(<App client={client} sheetName="S" />);
    const menu = await openPicker();
    fireEvent.click(menu.getByTestId("saved-view-apply-SV-x"));

    const table = () => within(screen.getByTestId("tree-table"));
    await waitFor(() => expect(table().getByText("Status")).toBeInTheDocument());
    expect(table().queryByText("Budget")).not.toBeInTheDocument();
  });

  it("applying or saving a view issues NO executeAction (presentation only)", async () => {
    const { client, calls } = viewClient({ rows: [savedView({ name: "SV-1", label: "Mine" })] });
    const spy = mockClient({ snapshot: loginAs("A") });
    render(<App client={{ ...client, executeAction: spy.client.executeAction }} sheetName="S" />);
    const menu = await openPicker();
    fireEvent.click(menu.getByTestId("saved-view-apply-SV-1"));
    fireEvent.change(menu.getByTestId("saved-views-name"), { target: { value: "Another" } });
    fireEvent.click(menu.getByTestId("saved-views-save"));
    await waitFor(() => expect(calls.some((c) => c.method === "save")).toBe(true));
    expect(spy.calls).toHaveLength(0);
  });
});

describe("SavedViewsMenu — saving, updating, publishing, deleting", () => {
  it("saves the LIVE overlay under the typed name, privately, and clears the field", async () => {
    const { client, calls, store } = viewClient({ rows: [] });
    render(<App client={client} sheetName="S" />);
    const menu = await openPicker();

    // Hide a column through the existing View menu, so the live overlay is
    // non-trivial and we can prove the save carries THAT, not an empty view.
    fireEvent.click(screen.getByTestId("view-toggle-col:budget"));
    const nameField = menu.getByTestId("saved-views-name");
    fireEvent.change(nameField, { target: { value: "No budget" } });
    fireEvent.click(menu.getByTestId("saved-views-save"));

    await waitFor(() => expect(store).toHaveLength(1));
    const sent = calls.find((c) => c.method === "save")!.args as Record<string, unknown>;
    expect(sent.sheet).toBe("S");
    expect(sent.label).toBe("No budget");
    expect((sent.view as SheetView).hidden).toEqual(["col:budget"]);
    expect(sent.visibility).toBeUndefined(); // a save is private; publishing is explicit
    expect(store[0].visibility).toBe("private");
    await waitFor(() => expect((nameField as HTMLInputElement).value).toBe(""));
  });

  it("Save is disabled without a name (an unnamed view is not a view)", async () => {
    const { client } = viewClient({ rows: [] });
    render(<App client={client} sheetName="S" />);
    const menu = await openPicker();
    expect(menu.getByTestId("saved-views-save")).toBeDisabled();
    fireEvent.change(menu.getByTestId("saved-views-name"), { target: { value: "  " } });
    expect(menu.getByTestId("saved-views-save")).toBeDisabled();
  });

  it("Update overwrites one view with the current arrangement, keeping its name", async () => {
    const { client, calls, store } = viewClient({
      rows: [savedView({ name: "SV-1", label: "Mine" })],
    });
    render(<App client={client} sheetName="S" />);
    const menu = await openPicker();

    fireEvent.click(screen.getByTestId("view-toggle-col:notes"));
    fireEvent.click(menu.getByTestId("saved-view-update-SV-1"));

    await waitFor(() => expect(store[0].view.hidden).toEqual(["col:notes"]));
    const sent = calls.filter((c) => c.method === "save").at(-1)!.args as Record<string, unknown>;
    expect(sent.name).toBe("SV-1");
    expect(sent.label).toBeUndefined(); // the name is not touched by an update
    expect(store[0].label).toBe("Mine");
  });

  it("Publish flips visibility ONLY — the stored arrangement is not replaced", async () => {
    const stored: SheetView = { v: 1, hidden: ["col:status"], order: [] };
    const { client, calls, store } = viewClient({
      rows: [savedView({ name: "SV-1", label: "Mine", view: stored })],
    });
    render(<App client={client} sheetName="S" />);
    const menu = await openPicker();

    // Change the LIVE overlay first: a publish must not smuggle it into the row.
    fireEvent.click(screen.getByTestId("view-toggle-col:notes"));
    fireEvent.click(menu.getByTestId("saved-view-publish-SV-1"));

    await waitFor(() => expect(store[0].visibility).toBe("sheet"));
    const sent = calls.filter((c) => c.method === "save").at(-1)!.args as Record<string, unknown>;
    expect(sent).toEqual({ name: "SV-1", visibility: "sheet" });
    expect(store[0].view).toEqual(stored);

    // Published rows offer the reverse, and the row is badged as shared.
    await waitFor(() => expect(menu.getByTestId("saved-view-shared-SV-1")).toBeInTheDocument());
    fireEvent.click(menu.getByTestId("saved-view-publish-SV-1"));
    await waitFor(() => expect(store[0].visibility).toBe("private"));
  });

  it("Delete takes two clicks (the confirm pattern), and Cancel backs out", async () => {
    const { client, calls, store } = viewClient({
      rows: [savedView({ name: "SV-1", label: "Mine" })],
    });
    render(<App client={client} sheetName="S" />);
    const menu = await openPicker();

    fireEvent.click(menu.getByTestId("saved-view-delete-SV-1"));
    expect(calls.some((c) => c.method === "delete")).toBe(false);
    fireEvent.click(menu.getByText("Cancel"));
    expect(menu.queryByTestId("saved-view-delete-confirm-SV-1")).toBeNull();
    expect(store).toHaveLength(1);

    fireEvent.click(menu.getByTestId("saved-view-delete-SV-1"));
    fireEvent.click(menu.getByTestId("saved-view-delete-confirm-SV-1"));
    await waitFor(() => expect(store).toHaveLength(0));
    await waitFor(() => expect(menu.getByTestId("saved-views-mine-empty")).toBeInTheDocument());
  });
});

describe("SavedViewsMenu — errors are visible, never swallowed", () => {
  it("shows the server's reason when the list cannot be loaded", async () => {
    const { client } = viewClient({ reject: { list: "No such sheet S (404)" } });
    render(<App client={client} sheetName="S" />);
    const menu = await openPicker();
    expect(await menu.findByTestId("saved-views-error")).toHaveTextContent("No such sheet S (404)");
  });

  it("shows a rejected save verbatim (a duplicate name is not a silent no-op)", async () => {
    const { client, store } = viewClient({
      rows: [],
      reject: { save: "You already have a view named Mine (409)" },
    });
    render(<App client={client} sheetName="S" />);
    const menu = await openPicker();

    fireEvent.change(menu.getByTestId("saved-views-name"), { target: { value: "Mine" } });
    fireEvent.click(menu.getByTestId("saved-views-save"));
    expect(await menu.findByTestId("saved-views-error")).toHaveTextContent(
      "You already have a view named Mine (409)",
    );
    expect(store).toHaveLength(0);
  });

  it("shows a rejected delete verbatim and keeps the row (403 ≠ gone)", async () => {
    const { client, store } = viewClient({
      rows: [savedView({ name: "SV-1", label: "Mine" })],
      reject: { del: "Only the view's author may delete it (403)" },
    });
    render(<App client={client} sheetName="S" />);
    const menu = await openPicker();

    fireEvent.click(menu.getByTestId("saved-view-delete-SV-1"));
    fireEvent.click(menu.getByTestId("saved-view-delete-confirm-SV-1"));
    expect(await menu.findByTestId("saved-views-error")).toHaveTextContent(
      "Only the view's author may delete it (403)",
    );
    expect(store).toHaveLength(1);
    expect(menu.getByTestId("saved-view-SV-1")).toBeInTheDocument();
  });

  it("degrades to an empty list (not a crash) on a client without the endpoints", async () => {
    const { client } = mockClient({ snapshot: loginAs("A") });
    render(<App client={client} sheetName="S" />);
    const menu = await openPicker();
    expect(menu.getByTestId("saved-views-mine-empty")).toBeInTheDocument();
    expect(menu.queryByTestId("saved-views-error")).toBeNull();
  });
});
