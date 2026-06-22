import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentSidebar } from "./AgentSidebar";
import { mockClient } from "../test/fixture";
import type { AgentFrame } from "../api";

describe("AgentSidebar (thin shell)", () => {
  it("sends agent.chat with sheet + message and shows it in the transcript (WEB_UI-063)", async () => {
    const { client, chatCalls } = mockClient({ frames: [] });
    render(<AgentSidebar client={client} sheet="S" />);
    fireEvent.change(screen.getByTestId("agent-input"), {
      target: { value: "set X status to done" },
    });
    fireEvent.click(screen.getByTestId("agent-send"));
    await waitFor(() => expect(chatCalls).toHaveLength(1));
    expect(chatCalls[0]).toEqual({ sheet: "S", message: "set X status to done" });
  });

  it("renders streamed Re-Act frames in arrival order (WEB_UI-064)", async () => {
    const frames: AgentFrame[] = [
      { type: "thought", content: "reasoning" },
      { type: "action", tool: "updateCell", arguments: { node: "X" } },
      { type: "observation", outcome: "executed" },
      { type: "final", content: "I updated 1 cell" },
    ];
    const { client } = mockClient({ frames });
    render(<AgentSidebar client={client} sheet="S" />);
    fireEvent.change(screen.getByTestId("agent-input"), { target: { value: "go" } });
    fireEvent.click(screen.getByTestId("agent-send"));
    await screen.findByTestId("frame-final");
    expect(screen.getByTestId("frame-thought")).toBeInTheDocument();
    expect(screen.getByTestId("frame-action-tool")).toHaveTextContent("updateCell");
    expect(screen.getByTestId("frame-final")).toHaveTextContent("I updated 1 cell");
  });

  it("a suggested observation renders a CR chip that routes to the review handler (WEB_UI-066/-072)", async () => {
    const onCrChip = vi.fn();
    const frames: AgentFrame[] = [
      { type: "observation", outcome: "suggested", change_request: "CR1", resolved_approver: "C" },
      { type: "final", content: "Filed a change request for C" },
    ];
    const { client } = mockClient({ frames });
    render(<AgentSidebar client={client} sheet="S" onCrChip={onCrChip} />);
    fireEvent.change(screen.getByTestId("agent-input"), { target: { value: "change budget" } });
    fireEvent.click(screen.getByTestId("agent-send"));
    const chip = await screen.findByTestId("cr-chip-CR1");
    fireEvent.click(chip);
    expect(onCrChip).toHaveBeenCalledWith("CR1");
  });

  it("shows a suggested-prompt empty state that seeds the composer without auto-sending (UX)", () => {
    const { client, chatCalls } = mockClient();
    render(<AgentSidebar client={client} sheet="S" />);
    // Empty transcript → richer empty state with clickable starter prompts.
    expect(screen.getByTestId("agent-empty")).toBeInTheDocument();
    const suggestion = screen.getAllByTestId("agent-suggestion")[0];
    const text = suggestion.textContent ?? "";
    fireEvent.click(suggestion);
    // Clicking seeds the composer; it does NOT fire a turn.
    expect(screen.getByTestId("agent-input")).toHaveValue(text);
    expect(chatCalls).toHaveLength(0);
  });

  it("does not send empty/whitespace messages (WEB_UI-071)", () => {
    const { client, chatCalls } = mockClient();
    render(<AgentSidebar client={client} sheet="S" />);
    expect(screen.getByTestId("agent-send")).toBeDisabled();
    expect(chatCalls).toHaveLength(0);
  });

  it("tool list excludes internalReset (WEB_UI-070)", () => {
    const { client } = mockClient();
    render(<AgentSidebar client={client} sheet="S" />);
    fireEvent.click(screen.getByTestId("agent-tools-toggle"));
    expect(screen.getByTestId("tool-updateCell")).toBeInTheDocument();
    expect(screen.queryByTestId("tool-internalReset")).toBeNull();
  });

  it("the sidebar never calls executeAction directly (WEB_UI-068)", async () => {
    const { client, calls } = mockClient({ frames: [{ type: "final", content: "done" }] });
    render(<AgentSidebar client={client} sheet="S" />);
    fireEvent.change(screen.getByTestId("agent-input"), { target: { value: "do it" } });
    fireEvent.click(screen.getByTestId("agent-send"));
    await screen.findByTestId("frame-final");
    expect(calls).toHaveLength(0);
  });
});
