// Unit spec for the Activity / change-history timeline (now self-contained).
//
// ActivityPanel owns its OWN fetching/paging/filtering against client.listActivity
// (the NEW {events, next_cursor} contract). It fetches page 1 on mount / sheet
// change / refreshKey change / filter change (reset + replace), and a "Load older"
// button fetches the next page (before=next_cursor) and APPENDS. Filters are a TYPE
// <select> and an ACTOR <select> (built from the distinct actors currently loaded),
// AND-combined and passed through to the client.
//
// Read-ACL is enforced SERVER-SIDE (the feed never carries raw cell values, and a
// column the viewer cannot read is omitted from the summary). The panel only
// renders what the server returns, so these specs assert presentation + paging +
// filter wiring, not ACL.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ActivityPanel } from "./ActivityPanel";
import type { ActivityEvent, ArborClient } from "../api";

function ev(over: Partial<ActivityEvent>): ActivityEvent {
  return {
    event_id: "E-1",
    type: "CELL_UPDATED",
    actor: "alice",
    actor_type: "User",
    timestamp: "2026-06-20T10:00:00",
    change_request: null,
    node: null,
    column: null,
    summary: "alice updated a cell",
    ...over,
  };
}

// A minimal client exposing only the listActivity spy this panel uses. The other
// ArborClient members are required by the type but never called here.
function makeClient(
  listActivity: ArborClient["listActivity"],
): { client: ArborClient; spy: ReturnType<typeof vi.fn> } {
  const spy = listActivity as unknown as ReturnType<typeof vi.fn>;
  const client = {
    executeAction: vi.fn(),
    getSheetSnapshot: vi.fn(),
    agentChat: vi.fn(),
    listActivity,
  } as unknown as ArborClient;
  return { client, spy };
}

// A single-page response (no older events → next_cursor null).
function page(events: ActivityEvent[], next_cursor: string | null = null) {
  return { events, next_cursor };
}

describe("ActivityPanel — page 1 fetch + rendering", () => {
  it("fetches page 1 on mount and renders the events newest-first as returned", async () => {
    const events = [
      ev({ event_id: "E-3", actor: "carol", summary: "carol was granted Reviewer", timestamp: "2026-06-20T12:00:00" }),
      ev({ event_id: "E-2", actor: "bob", summary: "bob proposed a change", timestamp: "2026-06-20T11:00:00" }),
      ev({ event_id: "E-1", actor: "alice", summary: "alice updated the Stage of SSO Federation", timestamp: "2026-06-20T10:00:00" }),
    ];
    const listActivity = vi.fn().mockResolvedValue(page(events, null));
    const { client } = makeClient(listActivity);

    render(<ActivityPanel client={client} sheet="S" />);

    await waitFor(() => expect(screen.getAllByTestId(/^activity-row-/)).toHaveLength(3));
    const rows = screen.getAllByTestId(/^activity-row-/);
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      "activity-row-E-3",
      "activity-row-E-2",
      "activity-row-E-1",
    ]);
    // Called with the sheet and no cursor (page 1 = fresh fetch).
    expect(listActivity).toHaveBeenCalledTimes(1);
    const [sheet, opts] = listActivity.mock.calls[0];
    expect(sheet).toBe("S");
    expect(opts?.before).toBeUndefined();
  });

  it("shows summary, actor, a type chip, a timestamp, and a CR marker when present", async () => {
    const listActivity = vi.fn().mockResolvedValue(
      page([
        ev({
          event_id: "E-9",
          actor: "alice",
          type: "CHANGE_PROPOSED",
          summary: "alice proposed a change to SSO Federation",
          timestamp: "2026-06-20T10:00:00",
          change_request: "CR-007",
        }),
      ]),
    );
    const { client } = makeClient(listActivity);
    render(<ActivityPanel client={client} sheet="S" />);

    const row = await screen.findByTestId("activity-row-E-9");
    expect(within(row).getByText(/proposed a change to SSO Federation/i)).toBeInTheDocument();
    expect(within(row).getByTestId("activity-actor")).toHaveTextContent("alice");
    const chip = within(row).getByTestId("activity-type");
    expect(chip).toHaveAttribute("data-type", "CHANGE_PROPOSED");
    expect(within(row).getByTestId("activity-time")).toHaveAttribute("datetime", "2026-06-20T10:00:00");
    expect(within(row).getByTestId("activity-cr")).toHaveTextContent("CR-007");
  });

  it("renders an empty state when the first page is empty", async () => {
    const listActivity = vi.fn().mockResolvedValue(page([], null));
    const { client } = makeClient(listActivity);
    render(<ActivityPanel client={client} sheet="S" />);

    expect(await screen.findByTestId("activity-empty")).toBeInTheDocument();
    expect(screen.getByText(/no activity yet/i)).toBeInTheDocument();
    expect(screen.queryAllByTestId(/^activity-row-/)).toHaveLength(0);
    // No "Load older" when there is nothing more.
    expect(screen.queryByTestId("activity-load-older")).toBeNull();
  });
});

describe("ActivityPanel — Load older pagination (append)", () => {
  it("shows Load older while next_cursor != null; clicking appends the next page", async () => {
    const p1 = page(
      [ev({ event_id: "E-3", timestamp: "2026-06-20T12:00:00" }), ev({ event_id: "E-2", timestamp: "2026-06-20T11:00:00" })],
      "CURSOR-1",
    );
    const p2 = page([ev({ event_id: "E-1", timestamp: "2026-06-20T10:00:00" })], null);
    const listActivity = vi
      .fn()
      .mockResolvedValueOnce(p1)
      .mockResolvedValueOnce(p2);
    const { client } = makeClient(listActivity);
    render(<ActivityPanel client={client} sheet="S" />);

    // Page 1: two rows + a Load-older button (next_cursor present).
    await waitFor(() => expect(screen.getAllByTestId(/^activity-row-/)).toHaveLength(2));
    const loadOlder = screen.getByTestId("activity-load-older");
    expect(loadOlder).toBeInTheDocument();

    // Click → fetch page 2 with before=CURSOR-1; rows APPEND (now 3 total).
    await act(async () => {
      loadOlder.click();
    });
    await waitFor(() => expect(screen.getAllByTestId(/^activity-row-/)).toHaveLength(3));
    const rows = screen.getAllByTestId(/^activity-row-/);
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      "activity-row-E-3",
      "activity-row-E-2",
      "activity-row-E-1",
    ]);
    expect(listActivity).toHaveBeenLastCalledWith("S", expect.objectContaining({ before: "CURSOR-1" }));
    // Page 2 had next_cursor null → button hides.
    expect(screen.queryByTestId("activity-load-older")).toBeNull();
  });
});

describe("ActivityPanel — filters refetch (reset, page 1)", () => {
  it("changing the TYPE filter refetches page 1 with {type} and resets the list", async () => {
    const listActivity = vi
      .fn()
      .mockResolvedValueOnce(
        page(
          [ev({ event_id: "E-3", type: "CELL_UPDATED" }), ev({ event_id: "E-2", type: "CHANGE_PROPOSED" })],
          "CURSOR-1",
        ),
      )
      .mockResolvedValueOnce(page([ev({ event_id: "E-2", type: "CHANGE_PROPOSED" })], null));
    const { client } = makeClient(listActivity);
    render(<ActivityPanel client={client} sheet="S" />);
    await waitFor(() => expect(screen.getAllByTestId(/^activity-row-/)).toHaveLength(2));

    const typeSelect = screen.getByTestId("activity-filter-type") as HTMLSelectElement;
    await act(async () => {
      typeSelect.value = "CHANGE_PROPOSED";
      typeSelect.dispatchEvent(new Event("change", { bubbles: true }));
    });

    await waitFor(() =>
      expect(listActivity).toHaveBeenLastCalledWith("S", expect.objectContaining({ type: "CHANGE_PROPOSED" })),
    );
    // The new fetch is page 1 (no cursor) and replaces the list (not appended).
    expect(listActivity.mock.calls.at(-1)?.[1]?.before).toBeUndefined();
    await waitFor(() => expect(screen.getAllByTestId(/^activity-row-/)).toHaveLength(1));
    expect(screen.queryByTestId("activity-load-older")).toBeNull();
  });

  it("changing the ACTOR filter refetches page 1 with {actor}", async () => {
    const listActivity = vi
      .fn()
      .mockResolvedValueOnce(
        page([ev({ event_id: "E-3", actor: "alice" }), ev({ event_id: "E-2", actor: "bob" })], null),
      )
      .mockResolvedValueOnce(page([ev({ event_id: "E-2", actor: "bob" })], null));
    const { client } = makeClient(listActivity);
    render(<ActivityPanel client={client} sheet="S" />);
    await waitFor(() => expect(screen.getAllByTestId(/^activity-row-/)).toHaveLength(2));

    // The actor select is built from the distinct actors currently loaded.
    const actorSelect = screen.getByTestId("activity-filter-actor") as HTMLSelectElement;
    expect(within(actorSelect).getByRole("option", { name: "bob" })).toBeInTheDocument();
    expect(within(actorSelect).getByRole("option", { name: "alice" })).toBeInTheDocument();

    await act(async () => {
      actorSelect.value = "bob";
      actorSelect.dispatchEvent(new Event("change", { bubbles: true }));
    });

    await waitFor(() =>
      expect(listActivity).toHaveBeenLastCalledWith("S", expect.objectContaining({ actor: "bob" })),
    );
  });
});

describe("ActivityPanel — impersonation 'via' affix", () => {
  it("renders 'via <real_user>' when real_user is present and distinct from the actor", async () => {
    const listActivity = vi.fn().mockResolvedValue(
      page([
        ev({
          event_id: "E-imp",
          actor: "owner@example.com",
          real_user: "admin@example.com",
          summary: "owner updated the Stage",
        }),
      ]),
    );
    const { client } = makeClient(listActivity);
    render(<ActivityPanel client={client} sheet="S" />);

    const row = await screen.findByTestId("activity-row-E-imp");
    const via = within(row).getByTestId("activity-via");
    expect(via).toHaveTextContent("via admin@example.com");
  });

  it("renders NO affix for a normal (non-impersonated) event", async () => {
    const listActivity = vi.fn().mockResolvedValue(
      page([ev({ event_id: "E-plain", actor: "alice", real_user: null })]),
    );
    const { client } = makeClient(listActivity);
    render(<ActivityPanel client={client} sheet="S" />);

    const row = await screen.findByTestId("activity-row-E-plain");
    expect(within(row).queryByTestId("activity-via")).toBeNull();
  });

  it("renders NO affix when real_user equals the actor (self-attributed)", async () => {
    const listActivity = vi.fn().mockResolvedValue(
      page([ev({ event_id: "E-self", actor: "alice", real_user: "alice" })]),
    );
    const { client } = makeClient(listActivity);
    render(<ActivityPanel client={client} sheet="S" />);

    const row = await screen.findByTestId("activity-row-E-self");
    expect(within(row).queryByTestId("activity-via")).toBeNull();
  });
});

describe("ActivityPanel — onCount reporting", () => {
  it("reports loaded count + hasMore to the host", async () => {
    const onCount = vi.fn();
    const listActivity = vi.fn().mockResolvedValue(
      page([ev({ event_id: "E-3" }), ev({ event_id: "E-2" })], "CURSOR-1"),
    );
    const { client } = makeClient(listActivity);
    render(<ActivityPanel client={client} sheet="S" onCount={onCount} />);

    await waitFor(() => expect(onCount).toHaveBeenCalledWith(2, true));
  });
});

describe("ActivityPanel — refreshKey re-fetch", () => {
  it("re-fetches page 1 when refreshKey changes", async () => {
    const listActivity = vi.fn().mockResolvedValue(page([ev({ event_id: "E-1" })], null));
    const { client } = makeClient(listActivity);
    const { rerender } = render(<ActivityPanel client={client} sheet="S" refreshKey={0} />);
    await waitFor(() => expect(listActivity).toHaveBeenCalledTimes(1));

    rerender(<ActivityPanel client={client} sheet="S" refreshKey={1} />);
    await waitFor(() => expect(listActivity).toHaveBeenCalledTimes(2));
    expect(listActivity.mock.calls.at(-1)?.[1]?.before).toBeUndefined();
  });
});
