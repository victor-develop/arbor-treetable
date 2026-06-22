import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SelectSplitCell } from "./cells/SelectSplitCell";

const statusOptions = { groups: [{ label: "Stage", options: ["todo", "doing", "done"] }] };
const tagOptions = { groups: [{ label: "Tags", options: ["urgent", "backend"] }] };

describe("SelectSplitCell", () => {
  it("renders segments from snapshot options, not hardcoded (WEB_UI-031)", () => {
    render(
      <SelectSplitCell type="single-select-split" value={["todo"]} options={statusOptions} canEdit onCommit={() => {}} />,
    );
    expect(screen.getByTestId("segment-todo")).toBeInTheDocument();
    expect(screen.getByTestId("segment-doing")).toBeInTheDocument();
    expect(screen.getByTestId("segment-done")).toBeInTheDocument();
  });

  it("single-select commits a single-element array and replaces (WEB_UI-027/-034)", () => {
    const onCommit = vi.fn();
    render(
      <SelectSplitCell type="single-select-split" value={["todo"]} options={statusOptions} canEdit onCommit={onCommit} />,
    );
    fireEvent.click(screen.getByTestId("segment-done"));
    expect(onCommit).toHaveBeenCalledWith(["done"]);
  });

  it("multi-select adds a value (WEB_UI-028)", () => {
    const onCommit = vi.fn();
    render(
      <SelectSplitCell type="multi-select-split" value={["urgent"]} options={tagOptions} canEdit onCommit={onCommit} />,
    );
    fireEvent.click(screen.getByTestId("segment-backend"));
    expect(onCommit).toHaveBeenCalledWith(["urgent", "backend"]);
  });

  it("multi-select deselect removes one, preserves rest (WEB_UI-029)", () => {
    const onCommit = vi.fn();
    render(
      <SelectSplitCell type="multi-select-split" value={["urgent", "backend"]} options={tagOptions} canEdit onCommit={onCommit} />,
    );
    fireEvent.click(screen.getByTestId("segment-urgent"));
    expect(onCommit).toHaveBeenCalledWith(["backend"]);
  });

  it("non-owned split renders in suggest mode but still produces intent (WEB_UI-030)", () => {
    const onCommit = vi.fn();
    render(
      <SelectSplitCell type="single-select-split" value={["todo"]} options={statusOptions} canEdit={false} onCommit={onCommit} />,
    );
    expect(screen.getByTestId("split-cell")).toHaveAttribute("data-mode", "suggest");
    fireEvent.click(screen.getByTestId("segment-done"));
    expect(onCommit).toHaveBeenCalledWith(["done"]);
  });

  it("uses radiogroup role for single, group for multi (WEB_UI-035)", () => {
    const { rerender } = render(
      <SelectSplitCell type="single-select-split" value={[]} options={statusOptions} canEdit onCommit={() => {}} />,
    );
    expect(screen.getByTestId("split-cell")).toHaveAttribute("role", "radiogroup");
    rerender(
      <SelectSplitCell type="multi-select-split" value={[]} options={tagOptions} canEdit onCommit={() => {}} />,
    );
    expect(screen.getByTestId("split-cell")).toHaveAttribute("role", "group");
  });

  it("flags an out-of-set legacy value (WEB_UI-033)", () => {
    render(
      <SelectSplitCell type="single-select-split" value={["archived"]} options={statusOptions} canEdit onCommit={() => {}} />,
    );
    expect(screen.getByTestId("legacy-archived")).toBeInTheDocument();
  });
});
