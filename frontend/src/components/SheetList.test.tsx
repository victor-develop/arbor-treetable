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
});
