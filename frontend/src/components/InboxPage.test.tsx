// Unit spec for InboxPage (Feature: process). A per-user CROSS-SHEET notification
// inbox: it fetches the viewer's notifications via client.inbox() (self-scoped
// server-side), groups them by source (tree_event / comment / process / sla),
// sorts newest-first within a group, fires an ack action for requires_ack rows
// (reusing the acknowledge capability through onAck), and deep-links each row to
// its originating sheet/cell via onOpen({sheet,node}).
//
// The row source is derived from the event_type discriminator carried by the
// server (e.g. "COMMENT_ADDED" -> comment; a process-stage type -> process; an
// SLA-breach type -> sla; else tree_event). These specs assert grouping, the ack
// wiring (only on requires_ack && !acked rows), and the deep-link wiring.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { InboxPage } from "./InboxPage";
import type { ArborClient, InboxItem } from "../api";

function item(over: Partial<InboxItem> = {}): InboxItem {
  return {
    name: "NOTIF-1",
    sheet: "sheet-a",
    event_type: "NODE_VALUE_UPDATED",
    message: "alice updated a cell",
    requires_ack: false,
    acked: false,
    node: null,
    ...over,
  };
}

function makeClient(inbox: ArborClient["inbox"]): ArborClient {
  return {
    executeAction: vi.fn(),
    getSheetSnapshot: vi.fn(),
    agentChat: vi.fn(),
    inbox,
  } as unknown as ArborClient;
}

describe("InboxPage — cross-sheet list + grouping by source", () => {
  it("fetches inbox on mount and lists the viewer's cross-sheet notifications", async () => {
    const inbox = vi.fn().mockResolvedValue([
      item({ name: "N-1", sheet: "sheet-a", message: "cell edited" }),
      item({ name: "N-2", sheet: "sheet-b", message: "comment on a cell", event_type: "COMMENT_ADDED" }),
    ]);
    render(<InboxPage client={makeClient(inbox)} />);

    await waitFor(() => expect(screen.getAllByTestId(/^inbox-row-/)).toHaveLength(2));
    expect(inbox).toHaveBeenCalledTimes(1);
    // Each row shows its source sheet + message.
    expect(screen.getByTestId("inbox-row-N-1")).toHaveTextContent("sheet-a");
    expect(screen.getByTestId("inbox-row-N-2")).toHaveTextContent("comment on a cell");
  });

  it("groups rows by source (tree_event / comment / process / sla)", async () => {
    const inbox = vi.fn().mockResolvedValue([
      item({ name: "N-1", event_type: "NODE_VALUE_UPDATED" }),
      item({ name: "N-2", event_type: "COMMENT_ADDED" }),
      item({ name: "N-3", event_type: "PROCESS_STAGE_ASSIGNED" }),
      item({ name: "N-4", event_type: "PROCESS_SLA_BREACHED", requires_ack: true }),
    ]);
    render(<InboxPage client={makeClient(inbox)} />);

    await waitFor(() => expect(screen.getAllByTestId(/^inbox-group-/)).toHaveLength(4));
    // Each row lands under its derived source group.
    expect(within(screen.getByTestId("inbox-group-tree_event")).getByTestId("inbox-row-N-1")).toBeInTheDocument();
    expect(within(screen.getByTestId("inbox-group-comment")).getByTestId("inbox-row-N-2")).toBeInTheDocument();
    expect(within(screen.getByTestId("inbox-group-process")).getByTestId("inbox-row-N-3")).toBeInTheDocument();
    expect(within(screen.getByTestId("inbox-group-sla")).getByTestId("inbox-row-N-4")).toBeInTheDocument();
  });

  it("renders an empty state when the inbox is empty", async () => {
    render(<InboxPage client={makeClient(vi.fn().mockResolvedValue([]))} />);
    expect(await screen.findByTestId("inbox-empty")).toBeInTheDocument();
    expect(screen.queryAllByTestId(/^inbox-row-/)).toHaveLength(0);
  });
});

describe("InboxPage — ack action", () => {
  it("shows an ack button only on requires_ack && !acked rows and fires onAck", async () => {
    const onAck = vi.fn();
    const inbox = vi.fn().mockResolvedValue([
      item({ name: "N-1", requires_ack: true, acked: false }),
      item({ name: "N-2", requires_ack: true, acked: true }),
      item({ name: "N-3", requires_ack: false, acked: false }),
    ]);
    render(<InboxPage client={makeClient(inbox)} onAck={onAck} />);

    await waitFor(() => expect(screen.getAllByTestId(/^inbox-row-/)).toHaveLength(3));
    // Only the un-acked requires_ack row has an ack button.
    expect(screen.getByTestId("inbox-ack-N-1")).toBeInTheDocument();
    expect(screen.queryByTestId("inbox-ack-N-2")).toBeNull();
    expect(screen.queryByTestId("inbox-ack-N-3")).toBeNull();
    // Already-acked row shows an acknowledged marker.
    expect(within(screen.getByTestId("inbox-row-N-2")).getByTestId("inbox-acked")).toBeInTheDocument();

    await act(async () => {
      screen.getByTestId("inbox-ack-N-1").click();
    });
    expect(onAck).toHaveBeenCalledWith("N-1");
  });
});

describe("InboxPage — deep link to originating sheet/cell", () => {
  it("clicking a row fires onOpen with the sheet + node", async () => {
    const onOpen = vi.fn();
    const inbox = vi.fn().mockResolvedValue([item({ name: "N-1", sheet: "sheet-a", node: "node-7" })]);
    render(<InboxPage client={makeClient(inbox)} onOpen={onOpen} />);

    const link = await screen.findByTestId("inbox-open-N-1");
    await act(async () => {
      link.click();
    });
    expect(onOpen).toHaveBeenCalledWith({ sheet: "sheet-a", node: "node-7" });
  });
});

describe("InboxPage — refetch", () => {
  it("re-fetches when refreshKey changes", async () => {
    const spy = vi.fn().mockResolvedValue([item()]);
    const client = makeClient(spy);
    const { rerender } = render(<InboxPage client={client} refreshKey={0} />);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    rerender(<InboxPage client={client} refreshKey={1} />);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
  });
});
