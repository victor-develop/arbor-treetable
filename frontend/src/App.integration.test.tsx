import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
