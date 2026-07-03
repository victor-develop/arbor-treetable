// Unit spec for the floating agent dock — the bubble/popup that wraps
// AgentSidebar. It is mounted by BOTH App (sheet-scoped) and SheetList
// (workspace, no sheet). The FAB toggles the popup open/closed; the sidebar
// stays mounted (CSS-hidden) so the transcript survives close/reopen — same
// stacking as before the extraction.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentDock } from "./AgentDock";
import { mockClient } from "../test/fixture";

describe("AgentDock", () => {
  it("bubble toggles the floating popup open/closed", () => {
    const { client } = mockClient();
    const { container } = render(<AgentDock client={client} sheet="S" />);
    const dock = container.querySelector(".arbor-agent-dock") as HTMLElement;
    const fab = screen.getByTestId("agent-fab");
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

  it("mounts AgentSidebar (transcript survives while CSS-hidden)", () => {
    const { client } = mockClient();
    render(<AgentDock client={client} sheet="S" />);
    // Sidebar is mounted even when the popup is closed.
    expect(screen.getByTestId("agent-sidebar")).toBeInTheDocument();
  });

  it("passes a null sheet through in workspace mode", async () => {
    const { client, chatCalls } = mockClient({ frames: [{ type: "final", content: "ok" }] });
    render(<AgentDock client={client} sheet={null} />);
    fireEvent.click(screen.getByTestId("agent-fab"));
    fireEvent.change(screen.getByTestId("agent-input"), { target: { value: "hi" } });
    fireEvent.click(screen.getByTestId("agent-send"));
    await screen.findByTestId("frame-final");
    expect(chatCalls[0].sheet ?? null).toBeNull();
  });

  it("forwards onSheetCreated from the sidebar", async () => {
    const onSheetCreated = vi.fn();
    const { client } = mockClient({
      frames: [
        { type: "observation", outcome: "executed", data: { sheet: "roadmap-1" } },
        { type: "final", content: "done" },
      ],
    });
    render(<AgentDock client={client} sheet={null} onSheetCreated={onSheetCreated} />);
    fireEvent.click(screen.getByTestId("agent-fab"));
    fireEvent.change(screen.getByTestId("agent-input"), { target: { value: "make it" } });
    fireEvent.click(screen.getByTestId("agent-send"));
    await screen.findByTestId("frame-final");
    expect(onSheetCreated).toHaveBeenCalledWith("roadmap-1");
  });
});
