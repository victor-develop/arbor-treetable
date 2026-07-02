import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ViewModeToggle } from "./ViewModeToggle";

describe("ViewModeToggle", () => {
  it("marks the active mode with aria-pressed", () => {
    render(<ViewModeToggle mode="live" onChange={() => {}} />);
    expect(screen.getByTestId("view-mode-live")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("view-mode-proposed")).toHaveAttribute("aria-pressed", "false");
  });

  it("emits the other mode on click", () => {
    const onChange = vi.fn();
    render(<ViewModeToggle mode="live" onChange={onChange} />);
    fireEvent.click(screen.getByTestId("view-mode-proposed"));
    expect(onChange).toHaveBeenCalledWith("proposed");
  });

  it("clicking Live from proposed emits live", () => {
    const onChange = vi.fn();
    render(<ViewModeToggle mode="proposed" onChange={onChange} />);
    expect(screen.getByTestId("view-mode-proposed")).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByTestId("view-mode-live"));
    expect(onChange).toHaveBeenCalledWith("live");
  });
});
