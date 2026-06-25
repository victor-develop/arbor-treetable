// Unit spec for the Activity / change-history timeline (presentational).
//
// ActivityPanel is a DENSE, newest-first timeline. The host fetches the events
// (api.listActivity) and hands them in as the `events` prop — the panel re-derives
// nothing. Each row shows the actor, the server-built summary one-liner, a subtle
// event-type chip, an absolute-ish timestamp, and a marker/link when the event
// carries a change_request. The component renders its own empty state.
//
// Read-ACL is enforced SERVER-SIDE (the feed never carries raw cell values, and a
// column the viewer cannot read is omitted from the summary). The panel only
// renders what it is given, so these specs assert presentation, not ACL.

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ActivityPanel } from "./ActivityPanel";
import type { ActivityEvent } from "../api";

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

describe("ActivityPanel — timeline rendering", () => {
  it("renders events in the order given (newest-first, as the server returns)", () => {
    const events: ActivityEvent[] = [
      ev({ event_id: "E-3", actor: "carol", summary: "carol was granted Reviewer", timestamp: "2026-06-20T12:00:00" }),
      ev({ event_id: "E-2", actor: "bob", summary: "bob proposed a change", timestamp: "2026-06-20T11:00:00" }),
      ev({ event_id: "E-1", actor: "alice", summary: "alice updated the Stage of SSO Federation", timestamp: "2026-06-20T10:00:00" }),
    ];
    render(<ActivityPanel events={events} />);

    const rows = screen.getAllByTestId(/^activity-row-/);
    expect(rows).toHaveLength(3);
    // Order is preserved exactly as handed in (host already sorted newest-first).
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      "activity-row-E-3",
      "activity-row-E-2",
      "activity-row-E-1",
    ]);
  });

  it("shows the summary, the actor, a type chip, and a timestamp per row", () => {
    render(
      <ActivityPanel
        events={[
          ev({
            event_id: "E-9",
            actor: "alice",
            type: "CELL_UPDATED",
            summary: "alice updated the Stage of SSO Federation",
            timestamp: "2026-06-20T10:00:00",
          }),
        ]}
      />,
    );
    const row = screen.getByTestId("activity-row-E-9");

    // Summary one-liner (the human sentence the server built).
    expect(within(row).getByText(/updated the Stage of SSO Federation/i)).toBeInTheDocument();
    // Actor is surfaced.
    expect(within(row).getByTestId("activity-actor")).toHaveTextContent("alice");
    // Subtle event-type chip carries the EventType.
    const chip = within(row).getByTestId("activity-type");
    expect(chip).toBeInTheDocument();
    expect(chip).toHaveAttribute("data-type", "CELL_UPDATED");
    // Timestamp is rendered (a <time> with the raw ISO in datetime).
    const ts = within(row).getByTestId("activity-time");
    expect(ts).toHaveAttribute("datetime", "2026-06-20T10:00:00");
  });
});

describe("ActivityPanel — change-request marker", () => {
  it("marks/links a row that references a change request", () => {
    render(
      <ActivityPanel
        events={[
          ev({ event_id: "E-cr", actor: "bob", type: "CHANGE_PROPOSED", summary: "bob proposed a change", change_request: "CR-007" }),
        ]}
      />,
    );
    const row = screen.getByTestId("activity-row-E-cr");
    const cr = within(row).getByTestId("activity-cr");
    expect(cr).toBeInTheDocument();
    expect(cr).toHaveTextContent("CR-007");
  });

  it("renders no CR marker when change_request is null", () => {
    render(<ActivityPanel events={[ev({ event_id: "E-plain", change_request: null })]} />);
    const row = screen.getByTestId("activity-row-E-plain");
    expect(within(row).queryByTestId("activity-cr")).toBeNull();
  });
});

describe("ActivityPanel — empty state", () => {
  it("renders an empty state when there are no events", () => {
    render(<ActivityPanel events={[]} />);
    expect(screen.getByTestId("activity-empty")).toBeInTheDocument();
    expect(screen.getByText(/no activity yet/i)).toBeInTheDocument();
    // No rows are rendered.
    expect(screen.queryAllByTestId(/^activity-row-/)).toHaveLength(0);
  });
});
