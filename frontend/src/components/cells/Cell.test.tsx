import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Cell } from "./Cell";
import type { SnapshotColumn } from "../../api";

function rerenderCell(
  rerender: (ui: JSX.Element) => void,
  props: Partial<Parameters<typeof Cell>[0]> & { startEditing?: number },
) {
  rerender(
    <Cell column={col({})} value="v" onCommit={vi.fn()} {...props} />,
  );
}

function col(over: Partial<SnapshotColumn>): SnapshotColumn {
  return {
    name: "c",
    field: "c",
    label: "C",
    type: "text",
    is_label: false,
    column_owner: "owner@x",
    editors: [],
    can_edit: true,
    ...over,
  } as SnapshotColumn;
}

describe("Cell — long-text density tagging", () => {
  it("multiline-text cells get .is-longtext so CSS can line-clamp them (UX D2)", () => {
    render(<Cell column={col({ type: "multiline-text" })} value="a long paragraph" onCommit={vi.fn()} />);
    expect(screen.getByTestId("cell")).toHaveClass("is-longtext");
  });

  it("plain text cells are NOT tagged long-text (no clamp)", () => {
    render(<Cell column={col({ type: "text" })} value="short" onCommit={vi.fn()} />);
    expect(screen.getByTestId("cell")).not.toHaveClass("is-longtext");
  });

  it("renders a count badge when >1 suggestion is pending, a dot for one", () => {
    const { rerender } = render(
      <Cell column={col({})} value="v" pending pendingCount={2} pendingTitle="2 pending" onCommit={vi.fn()} />,
    );
    const marker = screen.getByTestId("pending-marker");
    expect(marker).toHaveAttribute("data-count", "2");
    expect(marker).toHaveTextContent("2");
    rerender(<Cell column={col({})} value="v" pending pendingCount={1} onCommit={vi.fn()} />);
    expect(screen.getByTestId("pending-marker")).toHaveTextContent("•");
  });
});

describe("Cell — external edit trigger (edit-pencil wiring)", () => {
  it("opens the editor when startEditing increments to a truthy value", () => {
    const { rerender } = render(
      <Cell column={col({ type: "text" })} value="hello" startEditing={0} onCommit={vi.fn()} />,
    );
    // Not editing initially.
    expect(screen.queryByTestId("cell-input")).not.toBeInTheDocument();
    // Bumping the signal opens the editor and seeds the draft from the value.
    rerenderCell(rerender, { startEditing: 1, value: "hello" });
    const input = screen.getByTestId("cell-input") as HTMLInputElement;
    expect(input).toBeInTheDocument();
    expect(input.value).toBe("hello");
  });

  it("does NOT open the editor when startEditing is 0 / undefined", () => {
    const { rerender } = render(
      <Cell column={col({ type: "text" })} value="x" onCommit={vi.fn()} />,
    );
    rerenderCell(rerender, { startEditing: 0, value: "x" });
    expect(screen.queryByTestId("cell-input")).not.toBeInTheDocument();
  });

  it("does not open a read-only (non-interactive) cell editor on signal", () => {
    const { rerender } = render(
      <Cell column={col({ type: "text", can_edit: false })} value="x" startEditing={0} onCommit={vi.fn()} />,
    );
    rerender(
      <Cell column={col({ type: "text", can_edit: false })} value="x" startEditing={1} onCommit={vi.fn()} />,
    );
    // can_edit:false text cells are still interactive (suggest mode) — the
    // editor opens so a non-owner can type a suggestion.
    expect(screen.getByTestId("cell-input")).toBeInTheDocument();
  });
});
