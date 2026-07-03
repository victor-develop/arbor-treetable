// Bench-free unit spec for the SheetList home page — the no-?sheet landing
// surface (DELIVERABLE: Sheet List home page). It fetches sheet summaries via the
// client's listSheets(), renders each as a link to ?sheet=<name>, sorts by
// node_count DESC (so the ~3000 orphan empty test sheets sink below real ones),
// shows each sheet's node_count, and offers a client-side text filter. The
// component does not exist yet — this file is RED until it does.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SheetList } from "./SheetList";
import type { ArborClient, SheetSummary } from "../api";

function sheet(over: Partial<SheetSummary>): SheetSummary {
  return { name: "S1", structural_owner: "alice@example.com", node_count: 0, ...over };
}

// A minimal client exposing only listSheets (the surface SheetList consumes).
function clientWith(
  sheets: SheetSummary[],
  createSheet?: ArborClient["createSheet"],
): ArborClient {
  return {
    executeAction: vi.fn(),
    getSheetSnapshot: vi.fn(),
    agentChat: vi.fn(),
    listSheets: vi.fn(async () => sheets),
    createSheet,
  } as unknown as ArborClient;
}

describe("SheetList", () => {
  it("renders sheets sorted by node_count desc, each a link to ?sheet=<name>", async () => {
    const client = clientWith([
      sheet({ name: "Small", node_count: 3 }),
      sheet({ name: "Big", node_count: 120 }),
      sheet({ name: "Empty", node_count: 0 }),
    ]);
    render(<SheetList client={client} />);

    // Wait for the async fetch to populate the list.
    await waitFor(() => expect(screen.getByTestId("sheet-list")).toBeInTheDocument());

    const rows = screen.getAllByTestId(/^sheet-row-/);
    // Sorted by node_count desc: Big (120), Small (3), Empty (0).
    expect(rows.map((r) => r.getAttribute("data-name"))).toEqual(["Big", "Small", "Empty"]);

    // Each row links to ?sheet=<name> and shows its node_count.
    const big = screen.getByTestId("sheet-link-Big") as HTMLAnchorElement;
    expect(big.getAttribute("href")).toBe("?sheet=Big");
    expect(screen.getByTestId("sheet-count-Big")).toHaveTextContent("120");
  });

  it("narrows the visible sheets with the client-side filter box", async () => {
    const client = clientWith([
      sheet({ name: "Roadmap", node_count: 50 }),
      sheet({ name: "Budget", node_count: 40 }),
      sheet({ name: "Roster", node_count: 10 }),
    ]);
    render(<SheetList client={client} />);
    await waitFor(() => expect(screen.getByTestId("sheet-list")).toBeInTheDocument());
    expect(screen.getAllByTestId(/^sheet-row-/)).toHaveLength(3);

    fireEvent.change(screen.getByTestId("sheet-filter"), { target: { value: "ro" } });
    // Case-insensitive substring on the name: Roadmap + Roster, not Budget.
    const names = screen.getAllByTestId(/^sheet-row-/).map((r) => r.getAttribute("data-name"));
    expect(names).toEqual(["Roadmap", "Roster"]);
  });

  it("shows an empty state when there are no sheets", async () => {
    const client = clientWith([]);
    render(<SheetList client={client} />);
    await waitFor(() => expect(screen.getByTestId("sheet-list-empty")).toBeInTheDocument());
  });

  it("New-sheet form calls createSheet(name) and navigates to ?sheet=<name> (PART D)", async () => {
    const createSheet = vi.fn(async (name: string) => ({ sheet: name }));
    const client = clientWith([], createSheet);
    const onNavigate = vi.fn();
    render(<SheetList client={client} onNavigate={onNavigate} />);
    await waitFor(() => expect(screen.getByTestId("sheet-list-empty")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("new-sheet-name"), { target: { value: "Roadmap" } });
    fireEvent.click(screen.getByTestId("new-sheet-create"));

    await waitFor(() => expect(createSheet).toHaveBeenCalledWith("Roadmap"));
    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith("Roadmap"));
  });

  it("surfaces a duplicate-name error gracefully without navigating (PART D)", async () => {
    const createSheet = vi.fn(async () => {
      throw new Error("create_sheet failed: 409");
    });
    const client = clientWith([], createSheet);
    const onNavigate = vi.fn();
    render(<SheetList client={client} onNavigate={onNavigate} />);
    await waitFor(() => expect(screen.getByTestId("sheet-list-empty")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("new-sheet-name"), { target: { value: "Dup" } });
    fireEvent.click(screen.getByTestId("new-sheet-create"));

    await waitFor(() => expect(screen.getByTestId("new-sheet-error")).toBeInTheDocument());
    expect(onNavigate).not.toHaveBeenCalled();
  });

  // ---- workspace agent on the home page (GOAL: talk to the agent to build) ----

  it("mounts the floating agent dock on the home page", async () => {
    const client = clientWith([sheet({ name: "S1", node_count: 1 })]);
    render(<SheetList client={client} />);
    await waitFor(() => expect(screen.getByTestId("sheet-list")).toBeInTheDocument());
    expect(screen.getByTestId("agent-dock")).toBeInTheDocument();
    expect(screen.getByTestId("agent-fab")).toBeInTheDocument();
  });

  it("the home-page agent chats in WORKSPACE mode (no sheet)", async () => {
    const chatCalls: Array<string | null | undefined> = [];
    const client = {
      ...clientWith([]),
      agentChat: vi.fn(async (s: string | null | undefined, _m: string, onFrame: (f: unknown) => void) => {
        chatCalls.push(s);
        onFrame({ type: "final", content: "ok" });
      }),
    } as unknown as ArborClient;
    render(<SheetList client={client} />);
    await waitFor(() => expect(screen.getByTestId("sheet-list-empty")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("agent-fab"));
    fireEvent.change(screen.getByTestId("agent-input"), {
      target: { value: "create a Roadmap sheet" },
    });
    fireEvent.click(screen.getByTestId("agent-send"));
    await waitFor(() => expect(chatCalls).toHaveLength(1));
    expect(chatCalls[0] ?? null).toBeNull();
  });

  it("refreshes the list and surfaces an 'open <sheet>' CTA after the agent creates a sheet", async () => {
    // First listSheets() → empty; after the agent creates one, a refetch returns it.
    const rowsByCall = [[], [sheet({ name: "roadmap-1", node_count: 1 })]];
    let call = 0;
    const listSheets = vi.fn(async () => rowsByCall[Math.min(call++, rowsByCall.length - 1)]);
    const client = {
      executeAction: vi.fn(),
      getSheetSnapshot: vi.fn(),
      listSheets,
      agentChat: vi.fn(async (_s, _m, onFrame) => {
        onFrame({ type: "observation", outcome: "executed", data: { sheet: "roadmap-1" } });
        onFrame({ type: "final", content: "Created roadmap-1." });
      }),
    } as unknown as ArborClient;
    const onNavigate = vi.fn();
    render(<SheetList client={client} onNavigate={onNavigate} />);
    await waitFor(() => expect(screen.getByTestId("sheet-list-empty")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("agent-fab"));
    fireEvent.change(screen.getByTestId("agent-input"), { target: { value: "make it" } });
    fireEvent.click(screen.getByTestId("agent-send"));

    // The list refetches (call count grows past the initial mount fetch)…
    await waitFor(() => expect(listSheets.mock.calls.length).toBeGreaterThan(1));
    // …and it now contains the created sheet.
    await waitFor(() => expect(screen.getByTestId("sheet-row-roadmap-1")).toBeInTheDocument());

    // An "open <sheet>" affordance appears; clicking it navigates to the new sheet.
    const cta = screen.getByTestId("open-created-sheet");
    expect(cta).toHaveTextContent("roadmap-1");
    fireEvent.click(cta);
    expect(onNavigate).toHaveBeenCalledWith("roadmap-1");
  });
});
