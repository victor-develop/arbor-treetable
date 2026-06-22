// Runnable: bench-free (vitest + jsdom; no Frappe, no running app).
//
// Agent-sidebar streaming behaviours beyond the basics in AgentSidebar.test.tsx.
// The sidebar is a thin shell over agent.chat: it renders Re-Act frames in
// arrival order, fires onActionObserved per observation so the host can refetch
// the grid (the agent's executed actions become visible there — WEB_UI-065,
// -073 at component scope), summarizes mixed outcomes via the final frame
// (WEB_UI-067), and survives a mid-loop stream error with prior frames intact
// and a retry affordance (WEB_UI-069).
//
// Case IDs: WEB_UI-065 (observation drives refetch), WEB_UI-067 (mixed-outcome
// summary + multiple CR chips), WEB_UI-069 (error mid-loop, transcript preserved).

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentSidebar } from "./AgentSidebar";
import type { AgentFrame, ArborClient } from "../api";

function streamingClient(frames: AgentFrame[], opts?: { throwAfter?: number }): ArborClient {
  return {
    executeAction: async () => ({ kind: "executed" }),
    getSheetSnapshot: async () => {
      throw new Error("not used");
    },
    agentChat: async (_sheet, _message, onFrame) => {
      let i = 0;
      for (const f of frames) {
        if (opts?.throwAfter !== undefined && i === opts.throwAfter) {
          throw new Error("stream interrupted");
        }
        onFrame(f);
        i += 1;
      }
    },
  };
}

describe("AgentSidebar streaming", () => {
  it("fires onActionObserved for every observation so the host refetches the grid (WEB_UI-065/-073)", async () => {
    const onActionObserved = vi.fn();
    const frames: AgentFrame[] = [
      { type: "thought", content: "I'll set the status" },
      { type: "action", tool: "updateCell", arguments: { node: "X", column: "col:status", value: ["done"] } },
      { type: "observation", outcome: "executed" },
      { type: "final", content: "I updated 1 cell" },
    ];
    render(<AgentSidebar client={streamingClient(frames)} sheet="S" onActionObserved={onActionObserved} />);
    fireEvent.change(screen.getByTestId("agent-input"), { target: { value: "set X status to done" } });
    fireEvent.click(screen.getByTestId("agent-send"));

    await screen.findByTestId("frame-final");
    // exactly one observation → exactly one refetch trigger
    expect(onActionObserved).toHaveBeenCalledTimes(1);
    expect(onActionObserved.mock.calls[0][0]).toMatchObject({ type: "observation", outcome: "executed" });
    expect(screen.getByTestId("frame-final")).toHaveTextContent("I updated 1 cell");
  });

  it("renders a mixed-outcome summary and one CR chip per suggested observation (WEB_UI-067)", async () => {
    const onCrChip = vi.fn();
    const frames: AgentFrame[] = [
      { type: "action", tool: "updateCell", arguments: { node: "X" } },
      { type: "observation", outcome: "executed" },
      { type: "action", tool: "updateCell", arguments: { node: "Y" } },
      { type: "observation", outcome: "executed" },
      { type: "action", tool: "updateCell", arguments: { node: "Z" } },
      { type: "observation", outcome: "executed" },
      { type: "action", tool: "updateCell", arguments: { node: "X", column: "col:budget" } },
      { type: "observation", outcome: "suggested", change_request: "CR1", resolved_approver: "C" },
      { type: "action", tool: "addNode", arguments: { parent: "P2" } },
      { type: "observation", outcome: "suggested", change_request: "CR2", resolved_approver: "D" },
      { type: "final", content: "3 cells updated, 2 change requests filed" },
    ];
    render(<AgentSidebar client={streamingClient(frames)} sheet="S" onCrChip={onCrChip} />);
    fireEvent.change(screen.getByTestId("agent-input"), { target: { value: "bulk update" } });
    fireEvent.click(screen.getByTestId("agent-send"));

    await screen.findByTestId("frame-final");
    expect(screen.getByTestId("frame-final")).toHaveTextContent("3 cells updated, 2 change requests filed");
    // two distinct CR chips render
    expect(screen.getByTestId("cr-chip-CR1")).toBeInTheDocument();
    expect(screen.getByTestId("cr-chip-CR2")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("cr-chip-CR2"));
    expect(onCrChip).toHaveBeenCalledWith("CR2");
  });

  it("a mid-loop stream error keeps prior frames and offers a retry (WEB_UI-069)", async () => {
    const frames: AgentFrame[] = [
      { type: "thought", content: "starting" },
      { type: "action", tool: "updateCell", arguments: { node: "X" } },
      { type: "observation", outcome: "executed" },
      // throwAfter=3 → the error fires before any 4th frame is delivered
      { type: "final", content: "should never render" },
    ];
    render(<AgentSidebar client={streamingClient(frames, { throwAfter: 3 })} sheet="S" />);
    fireEvent.change(screen.getByTestId("agent-input"), { target: { value: "go" } });
    fireEvent.click(screen.getByTestId("agent-send"));

    const err = await screen.findByTestId("agent-error");
    expect(err).toHaveTextContent("stream interrupted");
    // prior frames survive; the (never-delivered) final is absent
    expect(screen.getByTestId("frame-thought")).toBeInTheDocument();
    expect(screen.getByTestId("frame-observation")).toBeInTheDocument();
    expect(screen.queryByTestId("frame-final")).toBeNull();
    // a retry affordance is present
    expect(screen.getByTestId("agent-retry")).toBeInTheDocument();
  });
});
