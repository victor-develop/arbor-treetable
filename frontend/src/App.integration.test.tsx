import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "./App";
import { loginAs, mockClient } from "./test/fixture";
import type {
  ArborClient,
  CellComment,
  ChangeRequestView,
  ProcessDef,
  Snapshot,
  Whoami,
} from "./api";

// Wrap the base mock client with a listChangeRequests that returns fixed CRs, so
// the Proposed overlay's MOVE path is exercised end-to-end through the shell.
function clientWithCRs(snapshot: Snapshot, crs: ChangeRequestView[]): ArborClient {
  const { client } = mockClient({ snapshot });
  return { ...client, listChangeRequests: async () => crs };
}

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
    fireEvent.click(notesCell);
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

  it("non-owner edit no longer files an instant CR / reverts / toasts — it stages a draft (WEB_UI-014, superseded by draft flow)", async () => {
    // Decision 1A SUPERSEDES the old WEB_UI-014 behavior (instant suggestChanges
    // CR + revert-to-snapshot + "Suggestion sent" toast + dot — the "weird" flow).
    // A non-owner edit now writes to the draft box: the value STAYS visible, no CR
    // is filed yet, and no suggested banner appears until the user submits.
    const { client, calls, draftCalls } = mockClient({
      snapshot: loginAs("A"), // A owns no columns
      drafts: [],
    });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    const budgetCell = screen.getByTestId("row-X").querySelector('[data-column="col:budget"] [data-testid="cell"]')!;
    expect(budgetCell).toHaveAttribute("data-mode", "suggest");
    fireEvent.click(budgetCell);
    fireEvent.change(screen.getByTestId("cell-input"), { target: { value: "500" } });
    fireEvent.blur(screen.getByTestId("cell-input"));

    await waitFor(() => expect(draftCalls.some((c) => c.method === "save")).toBe(true));
    // no instant suggestChanges via executeAction, no suggested banner, no revert.
    expect(calls.find((c) => c.action === "updateCell")).toBeUndefined();
    expect(screen.queryByTestId("banner")).not.toBeInTheDocument();
    expect(budgetCell).toHaveTextContent("500");
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
    fireEvent.click(budgetCell);
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

    const rowP2 = screen.getByTestId("row-P2");
    rowP2.getBoundingClientRect = () =>
      ({ top: 0, height: 90, left: 0, right: 0, bottom: 90, width: 0, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
    // Drag starts from the explicit grip handle (cells now single-click to edit).
    fireEvent.dragStart(screen.getByTestId("drag-handle-X"));
    fireEvent.dragOver(rowP2);
    fireEvent.drop(rowP2, { clientY: 45 });

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].action).toBe("moveNode");
    const banner = await screen.findByTestId("banner");
    expect(banner).toHaveTextContent("Suggestion sent to D");
    expect(banner).toHaveTextContent("co-approver: A");
  });

  it("non-owner edit (draft flow) → saveCellDraft, shows the value locally, shows the bar, NO executeAction/CR", async () => {
    // A owns no columns → col:budget (owner C) goes through the DRAFT box.
    const { client, calls, draftCalls } = mockClient({ snapshot: loginAs("A"), drafts: [] });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    const budgetCell = screen
      .getByTestId("row-X")
      .querySelector('[data-column="col:budget"] [data-testid="cell"]')!;
    expect(budgetCell).toHaveAttribute("data-mode", "suggest");
    fireEvent.click(budgetCell);
    fireEvent.change(screen.getByTestId("cell-input"), { target: { value: "500" } });
    fireEvent.blur(screen.getByTestId("cell-input"));

    // a draft was persisted — NOT an instant CR via executeAction.
    await waitFor(() => expect(draftCalls.some((c) => c.method === "save")).toBe(true));
    expect(calls.find((c) => c.action === "updateCell")).toBeUndefined();
    // the value shows locally + the cell is tagged as a draft.
    expect(budgetCell).toHaveTextContent("500");
    expect(budgetCell).toHaveAttribute("data-draft", "true");
    // no "Suggestion sent" banner (the old weird behavior is gone).
    expect(screen.queryByTestId("banner")).not.toBeInTheDocument();
    // the Review bar appears with the count.
    expect(await screen.findByTestId("draft-bar")).toHaveTextContent("Review 1 change");
  });

  it("owner edit still dispatches updateCell directly and shows NO draft bar", async () => {
    // B owns col:notes (can_edit true) → real-time direct commit, no draft.
    const { client, calls, draftCalls } = mockClient({
      snapshot: loginAs("B"),
      drafts: [],
      outcome: { kind: "executed" },
    });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    const notesCell = screen
      .getByTestId("row-X")
      .querySelector('[data-column="col:notes"] [data-testid="cell"]')!;
    expect(notesCell).toHaveAttribute("data-mode", "edit");
    fireEvent.click(notesCell);
    fireEvent.change(screen.getByTestId("cell-input"), { target: { value: "ship Q3" } });
    fireEvent.blur(screen.getByTestId("cell-input"));

    await waitFor(() => expect(calls.some((c) => c.action === "updateCell")).toBe(true));
    // owners never touch the draft box, never see the bar.
    expect(draftCalls.some((c) => c.method === "save")).toBe(false);
    expect(screen.queryByTestId("draft-bar")).not.toBeInTheDocument();
  });

  it("review modal: submit → ONE multi-change CR, drafts cleared, pending mark on the cell", async () => {
    const { client, draftCalls } = mockClient({
      snapshot: loginAs("A"),
      drafts: [],
      submitOutcome: { kind: "suggested", change_request: "CR-D1", resolved_approver: "C" },
    });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    // stage a draft on col:budget.
    const budgetCell = screen
      .getByTestId("row-X")
      .querySelector('[data-column="col:budget"] [data-testid="cell"]')!;
    fireEvent.click(budgetCell);
    fireEvent.change(screen.getByTestId("cell-input"), { target: { value: "500" } });
    fireEvent.blur(screen.getByTestId("cell-input"));

    // open the review modal from the bar.
    fireEvent.click(await screen.findByTestId("draft-bar-review"));
    const modal = await screen.findByTestId("draft-modal");
    // grouped under approver C with the old → new diff.
    expect(within(modal).getByTestId("draft-group-C")).toBeInTheDocument();
    expect(within(modal).getByTestId("draft-old")).toHaveTextContent("1000");
    expect(within(modal).getByTestId("draft-new")).toHaveTextContent("500");

    fireEvent.click(within(modal).getByTestId("draft-submit"));

    // submit filed ONE CR; the modal closes + the bar clears.
    await waitFor(() => expect(draftCalls.some((c) => c.method === "submit")).toBe(true));
    await waitFor(() => expect(screen.queryByTestId("draft-modal")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.queryByTestId("draft-bar")).not.toBeInTheDocument());
    // the suggested banner names the approver + carries the CR.
    const banner = await screen.findByTestId("banner");
    expect(banner).toHaveAttribute("data-kind", "suggested");
    expect(banner).toHaveTextContent("Suggestion sent to C");
    // the cell now shows the pending-approval marker (carrying the CR).
    expect(within(screen.getByTestId("row-X")).getByTestId("pending-marker")).toBeInTheDocument();
  });

  it("hydrates a server-persisted draft on mount: the bar shows + the value overlays without an edit", async () => {
    const { client } = mockClient({
      snapshot: loginAs("A"),
      drafts: [{ name: "D1", node: "X", column: "col:budget", value: 777, base_version: 0 }],
    });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    expect(await screen.findByTestId("draft-bar")).toHaveTextContent("Review 1 change");
    const budgetCell = screen
      .getByTestId("row-X")
      .querySelector('[data-column="col:budget"] [data-testid="cell"]')!;
    expect(budgetCell).toHaveTextContent("777");
    expect(budgetCell).toHaveAttribute("data-draft", "true");
  });

  it("row-density toggle sets data-density on the tree card (UX D2)", async () => {
    const { client } = mockClient({ snapshot: loginAs("B") });
    const { container } = render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    const card = container.querySelector(".arbor-tree-card") as HTMLElement;
    expect(card).toHaveAttribute("data-density", "comfortable"); // default = Cozy
    fireEvent.click(screen.getByTestId("density-compact"));
    expect(card).toHaveAttribute("data-density", "compact");
    expect(screen.getByTestId("density-compact")).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByTestId("density-expand"));
    expect(card).toHaveAttribute("data-density", "expand");
  });

  it("renders a back-to-sheets link pointing at the list home (no ?sheet=)", async () => {
    const { client } = mockClient({ snapshot: loginAs("B") });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    const back = screen.getByTestId("back-to-sheets");
    // Home = the same path with the query dropped (index.tsx renders SheetList then).
    expect(back.getAttribute("href")).toBe(window.location.pathname);
  });

  it("view-mode toggle flips Live ↔ Proposed with aria-pressed", async () => {
    const { client } = mockClient({ snapshot: loginAs("B") });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    const toggle = screen.getByTestId("view-mode-toggle");
    expect(toggle).toBeInTheDocument();
    // Default = Live.
    expect(screen.getByTestId("view-mode-live")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("view-mode-proposed")).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByTestId("view-mode-proposed"));
    expect(screen.getByTestId("view-mode-proposed")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("view-mode-live")).toHaveAttribute("aria-pressed", "false");
  });

  it("proposed mode shows the PROPOSED cell value (not the real one) with the proposed style", async () => {
    // Seed a pending suggestion on X's budget (real 1000 → proposed 500).
    const snap = loginAs("B");
    snap.nodes = snap.nodes.map((n) =>
      n.name === "X"
        ? { ...n, pending: { "col:budget": [{ value: 500, requester: "c", change_request: "CR1" }] } }
        : n,
    );
    const { client } = mockClient({ snapshot: snap });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    const budgetSel = () =>
      screen.getByTestId("row-X").querySelector('[data-column="col:budget"] [data-testid="cell"]')!;
    // Live: real value.
    expect(budgetSel()).toHaveTextContent("1000");

    fireEvent.click(screen.getByTestId("view-mode-proposed"));
    const proposed = budgetSel();
    expect(proposed).toHaveTextContent("500");
    expect(proposed).not.toHaveTextContent("1000");
    expect(proposed).toHaveAttribute("data-proposed", "true");
    expect(within(screen.getByTestId("row-X")).getByTestId("proposed-marker")).toBeInTheDocument();
    // The pending dot still shows in preview.
    expect(within(screen.getByTestId("row-X")).getByTestId("pending-marker")).toBeInTheDocument();
  });

  it("preview disables editing (click a cell → no input) and hides the drag handle", async () => {
    const { client, calls } = mockClient({ snapshot: loginAs("B"), outcome: { kind: "executed" } });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    fireEvent.click(screen.getByTestId("view-mode-proposed"));

    const notesCell = screen.getByTestId("row-X").querySelector('[data-column="col:notes"] [data-testid="cell"]')!;
    expect(notesCell).toHaveAttribute("data-mode", "preview");
    fireEvent.click(notesCell);
    expect(screen.queryByTestId("cell-input")).toBeNull();
    expect(calls.find((c) => c.action === "updateCell")).toBeUndefined();
    // No drag handles in preview.
    expect(screen.queryByTestId("drag-handle-X")).toBeNull();
  });

  it("toggling back to Live restores the real value + editability", async () => {
    const snap = loginAs("B");
    snap.nodes = snap.nodes.map((n) =>
      n.name === "X" ? { ...n, pending: { "col:notes": [{ value: "PROPOSED" }] } } : n,
    );
    const { client } = mockClient({ snapshot: snap, outcome: { kind: "executed" } });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    const notesSel = () =>
      screen.getByTestId("row-X").querySelector('[data-column="col:notes"] [data-testid="cell"]')!;
    fireEvent.click(screen.getByTestId("view-mode-proposed"));
    expect(notesSel()).toHaveTextContent("PROPOSED");
    expect(notesSel()).toHaveAttribute("data-mode", "preview");

    // Back to Live: real value + editable again.
    fireEvent.click(screen.getByTestId("view-mode-live"));
    const live = notesSel();
    expect(live).toHaveTextContent("v1"); // fixture real value
    expect(live).not.toHaveTextContent("PROPOSED");
    expect(live).toHaveAttribute("data-mode", "edit"); // B owns col:notes
    // Editing works again.
    fireEvent.click(live);
    expect(screen.getByTestId("cell-input")).toBeInTheDocument();
    // Drag handle is back.
    expect(screen.getByTestId("drag-handle-X")).toBeInTheDocument();
  });

  it("proposed mode relocates a moved node from an open move CR and tags the row", async () => {
    // Move X (under P1) into P2 as the first child.
    const crs: ChangeRequestView[] = [
      {
        name: "CR-MOVE",
        requester: "a",
        resolved_approver: "d",
        status: "proposed",
        operation: "move",
        target_kind: "node-structure",
        payload: { sheet: "S", node: "X", new_parent: "P2", after: null, _action_id: "moveNode" },
      },
    ];
    const client = clientWithCRs(loginAs("A"), crs);
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    // Live order.
    const liveOrder = screen.getAllByTestId(/^row-/).map((r) => r.getAttribute("data-testid"));
    expect(liveOrder).toEqual(["row-R", "row-P1", "row-X", "row-P2", "row-Y", "row-Z"]);

    fireEvent.click(screen.getByTestId("view-mode-proposed"));
    await waitFor(() =>
      expect(screen.getAllByTestId(/^row-/).map((r) => r.getAttribute("data-testid"))).toEqual([
        "row-R",
        "row-P1",
        "row-P2",
        "row-X",
        "row-Y",
        "row-Z",
      ]),
    );
    // The relocated row carries the moved tag.
    expect(screen.getByTestId("moved-X")).toBeInTheDocument();
  });

  it("agent bubble toggles the floating popup open/closed (UX M1)", async () => {
    const { client } = mockClient({ snapshot: loginAs("B") });
    const { container } = render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    const dock = container.querySelector(".arbor-agent-dock") as HTMLElement;
    const fab = screen.getByTestId("agent-fab");
    // Default closed: the table keeps full width, only the bubble shows.
    expect(dock).not.toHaveClass("is-open");
    expect(fab).toHaveAttribute("aria-expanded", "false");
    expect(fab).toHaveAttribute("aria-label", "Ask the agent");
    fireEvent.click(fab);
    expect(dock).toHaveClass("is-open");
    expect(fab).toHaveAttribute("aria-expanded", "true");
    expect(fab).toHaveAttribute("aria-label", "Close agent");
    fireEvent.click(fab);
    expect(dock).not.toHaveClass("is-open");
  });
});

// ---- Wave 4 shell integration (auth gate / impersonation / comments / process) --

// A comment thread the mocked listCellComments resolves to (a single root).
function thread(): CellComment[] {
  return [
    {
      name: "CMT-1",
      thread_root: null,
      parent_comment: null,
      author: "c",
      body: "Is this figure final?",
      mentions: [],
      resolved: false,
      resolved_by: null,
      resolved_at: null,
      timestamp: "2026-06-20T10:00:00Z",
      can_resolve: true,
      can_delete: true,
    },
  ];
}

describe("App — auth gate (Feature: act-as)", () => {
  it("unauthenticated whoami renders the LoginScreen INSTEAD of the sheet", async () => {
    const { client } = mockClient({ snapshot: loginAs("B") });
    const gated: ArborClient = {
      ...client,
      whoami: async (): Promise<Whoami> => ({ user: "Guest", authenticated: false }),
    };
    render(<App client={gated} sheetName="S" />);
    expect(await screen.findByTestId("login-screen")).toBeInTheDocument();
    // The sheet is NOT rendered while unauthenticated.
    expect(screen.queryByTestId("tree-table")).toBeNull();
  });

  it("authenticated whoami renders the sheet (passthrough)", async () => {
    const { client } = mockClient({ snapshot: loginAs("B") });
    const gated: ArborClient = {
      ...client,
      whoami: async (): Promise<Whoami> => ({ user: "B", authenticated: true }),
    };
    render(<App client={gated} sheetName="S" />);
    await screen.findByTestId("tree-table");
    expect(screen.queryByTestId("login-screen")).toBeNull();
  });

  it("a client WITHOUT whoami is a passthrough (no gate) — legacy tests keep rendering", async () => {
    const { client } = mockClient({ snapshot: loginAs("B") });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    expect(screen.queryByTestId("login-screen")).toBeNull();
  });
});

describe("App — ImpersonationBar wiring (Feature: act-as)", () => {
  it("admin sees the Act-as picker; begin calls the client + refetches whoami + sheet", async () => {
    // Admin snapshot (is_admin) whose whoami reports a plain authenticated admin.
    const snap = loginAs("B", { viewer: { is_admin: true } });
    const { client, snapshotCalls } = mockClient({ snapshot: snap });
    const beginCalls: { user: string; reason?: string }[] = [];
    let whoamiCalls = 0;
    const admin: ArborClient = {
      ...client,
      whoami: async (): Promise<Whoami> => {
        whoamiCalls += 1;
        return { user: "admin", authenticated: true };
      },
      beginImpersonation: async (user, reason) => {
        beginCalls.push({ user, reason });
        return { kind: "executed" };
      },
    };
    render(<App client={admin} sheetName="S" />);
    await screen.findByTestId("tree-table");
    const snapsBefore = snapshotCalls.length;
    const whoamiBefore = whoamiCalls;

    // The picker is shown (admin, not impersonating).
    fireEvent.change(screen.getByTestId("impersonation-user"), { target: { value: "owner@x" } });
    fireEvent.click(screen.getByTestId("impersonation-begin"));

    await waitFor(() => expect(beginCalls).toEqual([{ user: "owner@x", reason: undefined }]));
    // begin refetched BOTH whoami and the sheet.
    await waitFor(() => expect(whoamiCalls).toBeGreaterThan(whoamiBefore));
    await waitFor(() => expect(snapshotCalls.length).toBeGreaterThan(snapsBefore));
  });

  it("impersonating whoami shows the banner + Stop calls endImpersonation", async () => {
    const { client, snapshotCalls } = mockClient({ snapshot: loginAs("B") });
    let end = 0;
    const impersonated: ArborClient = {
      ...client,
      whoami: async (): Promise<Whoami> => ({
        user: "owner",
        real_user: "admin",
        impersonating: true,
        authenticated: true,
      }),
      endImpersonation: async () => {
        end += 1;
        return { kind: "executed" };
      },
    };
    render(<App client={impersonated} sheetName="S" />);
    await screen.findByTestId("tree-table");
    // The banner is visible naming both identities.
    const banner = await screen.findByTestId("impersonation-banner");
    expect(banner).toHaveTextContent("owner");
    expect(banner).toHaveTextContent("admin");
    const snapsBefore = snapshotCalls.length;
    fireEvent.click(screen.getByTestId("impersonation-stop"));
    await waitFor(() => expect(end).toBe(1));
    await waitFor(() => expect(snapshotCalls.length).toBeGreaterThan(snapsBefore));
  });

  it("a non-admin (not impersonating) sees no impersonation bar", async () => {
    const { client } = mockClient({ snapshot: loginAs("A") });
    const nonAdmin: ArborClient = {
      ...client,
      whoami: async (): Promise<Whoami> => ({ user: "A", authenticated: true }),
    };
    render(<App client={nonAdmin} sheetName="S" />);
    await screen.findByTestId("tree-table");
    expect(screen.queryByTestId("impersonation-bar")).toBeNull();
  });
});

describe("App — CommentDrawer wiring (Feature: comments)", () => {
  // Seed a comment summary on X's notes cell so the glyph renders.
  function snapWithComment(): Snapshot {
    const snap = loginAs("B");
    snap.nodes = snap.nodes.map((n) =>
      n.name === "X" ? { ...n, comments: { "col:notes": { open: 1, resolved: 0, unread: 0 } } } : n,
    );
    return snap;
  }
  function commentClient(snapshot: Snapshot) {
    const { client } = mockClient({ snapshot });
    const listCalls: { node: string; column: string }[] = [];
    const addCalls: { body: string; parent?: string }[] = [];
    const c: ArborClient = {
      ...client,
      listCellComments: async (_s, node, column) => {
        listCalls.push({ node, column });
        return thread();
      },
      addCellComment: async (_s, _n, _c, body, opts) => {
        addCalls.push({ body, parent: opts?.parent_comment });
        return { name: "CMT-2", thread_root: "CMT-1", mentions: [] };
      },
      resolveCellComment: async () => ({ ok: true }),
    };
    return { client: c, listCalls, addCalls };
  }

  it("clicking a cell's comment glyph opens the drawer with the loaded thread", async () => {
    const { client, listCalls } = commentClient(snapWithComment());
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    const notesCell = screen.getByTestId("row-X").querySelector('[data-column="col:notes"]')!;
    fireEvent.click(within(notesCell as HTMLElement).getByTestId("comment-glyph"));

    // The drawer opens and loads the cell's thread.
    const drawer = await screen.findByTestId("comment-drawer");
    await waitFor(() =>
      expect(listCalls).toContainEqual({ node: "X", column: "col:notes" }),
    );
    expect(within(drawer).getByText("Is this figure final?")).toBeInTheDocument();
  });

  it("posting a comment calls addCellComment", async () => {
    const { client, addCalls } = commentClient(snapWithComment());
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    const notesCell = screen.getByTestId("row-X").querySelector('[data-column="col:notes"]')!;
    fireEvent.click(within(notesCell as HTMLElement).getByTestId("comment-glyph"));
    await screen.findByTestId("comment-drawer");

    fireEvent.change(screen.getByTestId("comment-composer"), { target: { value: "Confirmed." } });
    fireEvent.click(screen.getByTestId("comment-post"));
    await waitFor(() => expect(addCalls).toEqual([{ body: "Confirmed.", parent: undefined }]));
  });

  it("the drawer is inert in Proposed preview (no glyph, no composer)", async () => {
    const { client } = commentClient(snapWithComment());
    render(<App client={client} sheetName="S" initialViewMode="proposed" />);
    await screen.findByTestId("tree-table");
    // In preview the Cell withholds the glyph, so the drawer is unreachable.
    expect(screen.queryByTestId("comment-glyph")).toBeNull();
    expect(screen.queryByTestId("comment-drawer")).toBeNull();
  });

  it("the drawer sits below the agent popup (z-order: agent dock present alongside)", async () => {
    const { client } = commentClient(snapWithComment());
    const { container } = render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    const notesCell = screen.getByTestId("row-X").querySelector('[data-column="col:notes"]')!;
    fireEvent.click(within(notesCell as HTMLElement).getByTestId("comment-glyph"));
    await screen.findByTestId("comment-drawer");
    // Both surfaces are mounted; the drawer coexists with the agent dock.
    expect(container.querySelector(".arbor-agent-dock")).toBeInTheDocument();
    expect(container.querySelector(".arbor-comment-drawer")).toBeInTheDocument();
  });
});

describe("App — ProcessConfigPanel wiring (Feature: process)", () => {
  function processClient(opts?: { def?: ProcessDef | null }) {
    // A owns sheet S (structural_owner == "A"), so A may configure the process.
    const { client } = mockClient({ snapshot: loginAs("A") });
    const defineCalls: { stages: unknown }[] = [];
    let enableCalls = 0;
    const c: ArborClient = {
      ...client,
      getProcess: async (): Promise<ProcessDef> =>
        opts?.def ?? {
          sheet: "S",
          title: null,
          enabled: false,
          row_scope: "root-children",
          start_trigger: "node-created",
          stages: [],
        },
      defineProcess: async (_s, stages) => {
        defineCalls.push({ stages });
        return { kind: "executed" };
      },
      enableProcess: async () => {
        enableCalls += 1;
        return { kind: "executed" };
      },
    };
    return { client: c, defineCalls, getEnable: () => enableCalls };
  }

  it("structural owner sees the Process button; opening seeds the panel from getProcess", async () => {
    const { client } = processClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    fireEvent.click(screen.getByTestId("process-config-button"));
    // The modal opens with the (empty) process editor seeded from getProcess.
    expect(await screen.findByTestId("process-config-modal")).toBeInTheDocument();
    expect(screen.getByTestId("process-config")).toBeInTheDocument();
  });

  it("adding a stage + Save process fires defineProcess", async () => {
    const { client, defineCalls } = processClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    fireEvent.click(screen.getByTestId("process-config-button"));
    await screen.findByTestId("process-config");

    // Pick a column stage then add it.
    fireEvent.change(screen.getByTestId("pc-add-column"), { target: { value: "col:status" } });
    fireEvent.click(screen.getByTestId("pc-add-stage"));
    fireEvent.click(screen.getByTestId("pc-define"));
    await waitFor(() => expect(defineCalls).toHaveLength(1));
  });

  it("Enable fires enableProcess when a process already exists", async () => {
    const { client, getEnable } = processClient({
      def: {
        sheet: "S",
        title: "Flow",
        enabled: false,
        row_scope: "root-children",
        start_trigger: "node-created",
        stages: [{ idx: 0, column: "col:status", label: "Status", sla_seconds: 0 }],
      },
    });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    fireEvent.click(screen.getByTestId("process-config-button"));
    await screen.findByTestId("process-config");
    fireEvent.click(await screen.findByTestId("pc-enable"));
    await waitFor(() => expect(getEnable()).toBe(1));
  });

  it("a non-structural-owner / non-admin sees NO Process button", async () => {
    // B is not the structural owner (A is) and not admin.
    const { client } = mockClient({ snapshot: loginAs("B") });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    expect(screen.queryByTestId("process-config-button")).toBeNull();
  });

  it("renders the header nav (Inbox + Dashboard) links", async () => {
    const { client } = mockClient({ snapshot: loginAs("B") });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    expect(screen.getByTestId("nav-inbox").getAttribute("href")).toBe("?inbox=1");
    expect(screen.getByTestId("nav-dashboard").getAttribute("href")).toContain("dashboard=1");
  });
});
