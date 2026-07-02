// Unit spec for ProcessDashboard (Feature: process). A per-process kanban/flow
// view: one column per stage showing the pending count, the out-of-SLA (breached)
// count, and the avg enter->fill time; a top summary (active / completed /
// throughput). Clicking a stage column drills into that stage's runs via
// client.listProcessRuns(sheet, {stage_idx}).
//
// The panel is a PURE render off the dashboard payload (self-fetches the dashboard
// on mount / refreshKey; fetches runs lazily on drill). Read-ACL is server-side:
// an unreadable stage column arrives with label=null, which we render as a generic
// placeholder (never a leaked field key). These specs assert the counts, the
// breached emphasis, the summary, and the drill-down wiring.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProcessDashboard } from "./ProcessDashboard";
import type { ArborClient, ProcessDashboard as Dash, ProcessRun } from "../api";

function dash(over: Partial<Dash> = {}): Dash {
  return {
    stages: [
      { idx: 0, column: "owner_c", label: "Owner", pending_count: 3, breached_count: 0, avg_enter_to_fill_seconds: 120 },
      { idx: 1, column: "budget", label: "Budget", pending_count: 2, breached_count: 1, avg_enter_to_fill_seconds: 3600 },
      { idx: 2, column: "approval", label: null, pending_count: 0, breached_count: 0, avg_enter_to_fill_seconds: null },
    ],
    total_active: 5,
    total_completed: 4,
    throughput: 9,
    ...over,
  };
}

function run(over: Partial<ProcessRun> = {}): ProcessRun {
  return {
    name: "R-1",
    process: "P-1",
    sheet: "S",
    node: "N-1",
    status: "active",
    current_stage_idx: 1,
    started_at: "2026-06-20T10:00:00",
    completed_at: null,
    ...over,
  };
}

function makeClient(
  processDashboard: ArborClient["processDashboard"],
  listProcessRuns?: ArborClient["listProcessRuns"],
): ArborClient {
  return {
    executeAction: vi.fn(),
    getSheetSnapshot: vi.fn(),
    agentChat: vi.fn(),
    processDashboard,
    listProcessRuns,
  } as unknown as ArborClient;
}

describe("ProcessDashboard — stage columns + counts", () => {
  it("renders a column per stage with pending, breached, and avg-time", async () => {
    const client = makeClient(vi.fn().mockResolvedValue(dash()));
    render(<ProcessDashboard client={client} sheet="S" />);

    await waitFor(() => expect(screen.getAllByTestId(/^pd-stage-\d+$/)).toHaveLength(3));
    const s0 = screen.getByTestId("pd-stage-0");
    expect(within(s0).getByTestId("pd-stage-label")).toHaveTextContent("Owner");
    expect(within(s0).getByTestId("pd-pending")).toHaveTextContent("3");
    expect(within(s0).getByTestId("pd-breached")).toHaveTextContent("0");
  });

  it("emphasizes stages with SLA breaches (data-breached=true) and shows the breached count", async () => {
    const client = makeClient(vi.fn().mockResolvedValue(dash()));
    render(<ProcessDashboard client={client} sheet="S" />);

    const s1 = await screen.findByTestId("pd-stage-1");
    expect(within(s1).getByTestId("pd-breached")).toHaveTextContent("1");
    expect(within(s1).getByTestId("pd-breached")).toHaveAttribute("data-breached", "true");
    // A stage with no breaches is not emphasized.
    const s0 = screen.getByTestId("pd-stage-0");
    expect(within(s0).getByTestId("pd-breached")).toHaveAttribute("data-breached", "false");
  });

  it("renders a generic placeholder when the stage column label is null (read-ACL redacted)", async () => {
    const client = makeClient(vi.fn().mockResolvedValue(dash()));
    render(<ProcessDashboard client={client} sheet="S" />);

    const s2 = await screen.findByTestId("pd-stage-2");
    // Never leak the raw column field key when the viewer can't read it.
    expect(within(s2).getByTestId("pd-stage-label")).not.toHaveTextContent("approval");
    expect(within(s2).getByTestId("pd-stage-label")).toHaveTextContent(/stage 3/i);
  });

  it("shows the top summary (active / completed / throughput)", async () => {
    const client = makeClient(vi.fn().mockResolvedValue(dash()));
    render(<ProcessDashboard client={client} sheet="S" />);

    const summary = await screen.findByTestId("pd-summary");
    expect(within(summary).getByTestId("pd-total-active")).toHaveTextContent("5");
    expect(within(summary).getByTestId("pd-total-completed")).toHaveTextContent("4");
    expect(within(summary).getByTestId("pd-throughput")).toHaveTextContent("9");
  });

  it("renders an empty state when there are no stages", async () => {
    const client = makeClient(vi.fn().mockResolvedValue(dash({ stages: [], total_active: 0, total_completed: 0, throughput: 0 })));
    render(<ProcessDashboard client={client} sheet="S" />);
    expect(await screen.findByTestId("pd-empty")).toBeInTheDocument();
  });
});

describe("ProcessDashboard — drill-down to runs", () => {
  it("clicking a stage fetches that stage's runs and lists them", async () => {
    const listRuns = vi.fn().mockResolvedValue([run({ name: "R-1", node: "N-1" }), run({ name: "R-2", node: "N-2" })]);
    const client = makeClient(vi.fn().mockResolvedValue(dash()), listRuns);
    render(<ProcessDashboard client={client} sheet="S" />);

    const s1 = await screen.findByTestId("pd-stage-1");
    await act(async () => {
      within(s1).getByTestId("pd-stage-drill").click();
    });

    await waitFor(() => expect(listRuns).toHaveBeenCalledWith("S", expect.objectContaining({ stage_idx: 1 })));
    await waitFor(() => expect(screen.getAllByTestId(/^pd-run-/)).toHaveLength(2));
    expect(screen.getByTestId("pd-run-R-1")).toHaveTextContent("N-1");
  });
});

describe("ProcessDashboard — refetch", () => {
  it("re-fetches the dashboard when refreshKey changes", async () => {
    const spy = vi.fn().mockResolvedValue(dash());
    const client = makeClient(spy);
    const { rerender } = render(<ProcessDashboard client={client} sheet="S" refreshKey={0} />);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    rerender(<ProcessDashboard client={client} sheet="S" refreshKey={1} />);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
  });
});
