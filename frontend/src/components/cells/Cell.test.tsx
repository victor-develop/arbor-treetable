import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Cell } from "./Cell";
import type { SnapshotColumn } from "../../api";

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
