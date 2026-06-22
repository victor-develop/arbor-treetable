import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TreeTable } from "./TreeTable";
import { loginAs } from "../test/fixture";

function renderTable(overrides?: Partial<Parameters<typeof TreeTable>[0]>) {
  const snap = loginAs("D"); // D owns P2 subtree structurally
  const onMove = vi.fn();
  const onToggle = vi.fn();
  const onCommitCell = vi.fn();
  const collapsed = new Set<string>();
  const utils = render(
    <TreeTable
      columns={snap.columns}
      nodes={snap.nodes}
      labelColumn={snap.label_column}
      collapsed={collapsed}
      onToggle={onToggle}
      pendingCell={() => false}
      isPendingMove={() => false}
      onCommitCell={onCommitCell}
      onMove={onMove}
      {...overrides}
    />,
  );
  return { snap, onMove, onToggle, onCommitCell, ...utils };
}

describe("TreeTable render", () => {
  it("renders rows in NestedSet order with labels from is_label column (WEB_UI-001/-002)", () => {
    renderTable();
    const rows = screen.getAllByTestId(/^row-/);
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      "row-R",
      "row-P1",
      "row-X",
      "row-P2",
      "row-Y",
      "row-Z",
    ]);
    expect(within(screen.getByTestId("label-X")).getByText("Task X")).toBeInTheDocument();
  });

  it("groups have a chevron; leaves have a spacer (WEB_UI-003)", () => {
    renderTable();
    expect(screen.getByTestId("chevron-P2")).toBeInTheDocument();
    expect(screen.getByTestId("spacer-X")).toBeInTheDocument();
  });

  it("toggling a chevron calls onToggle, never an executeAction (WEB_UI-006)", () => {
    const { onToggle } = renderTable();
    fireEvent.click(screen.getByTestId("chevron-P2"));
    expect(onToggle).toHaveBeenCalledWith("P2");
  });
});

describe("TreeTable drag-and-drop → moveNode", () => {
  function dropFromTop(targetTestId: string, fraction: number) {
    const row = screen.getByTestId(targetTestId);
    // jsdom getBoundingClientRect returns zeros; stub a usable rect.
    row.getBoundingClientRect = () =>
      ({ top: 0, height: 90, left: 0, right: 0, bottom: 90, width: 0, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
    fireEvent.dragOver(row);
    fireEvent.drop(row, { clientY: 90 * fraction });
  }

  it("drop inside P2 computes moveNode with new_parent=P2 (WEB_UI-036)", () => {
    const { onMove } = renderTable();
    fireEvent.dragStart(screen.getByTestId("row-Y")); // drag Y
    dropFromTop("row-P2", 0.5); // middle of a group = inside
    expect(onMove).toHaveBeenCalledWith({ node: "Y", new_parent: "P2", after: null });
  });

  it("illegal drop onto own descendant is suppressed (WEB_UI-044)", () => {
    const { onMove } = renderTable();
    fireEvent.dragStart(screen.getByTestId("row-P2"));
    dropFromTop("row-Z", 0.5);
    expect(onMove).not.toHaveBeenCalled();
  });
});
