import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TreeTable, computeOverflowState } from "./TreeTable";
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

  it("wraps the table in a horizontal scroll viewport (UX D1)", () => {
    renderTable();
    const vp = screen.getByTestId("table-viewport");
    expect(vp).toBeInTheDocument();
    expect(vp).toContainElement(screen.getByTestId("tree-table"));
  });

  it("colgroup assigns predictable widths by column type + honors a user width (UX D3)", () => {
    // Craft one column of each type so every colWidth branch is exercised.
    const mk = (over: Record<string, unknown>) => ({
      name: String(over.name),
      field: String(over.name),
      label: String(over.name),
      is_label: false,
      column_owner: "o",
      editors: [],
      can_edit: false,
      ...over,
    });
    const columns = [
      mk({ name: "L", type: "text", is_label: true }),
      mk({ name: "txt", type: "text" }),
      mk({ name: "num", type: "number" }),
      mk({ name: "sel", type: "single-select-split" }),
      mk({ name: "msel", type: "multi-select-split" }),
      mk({ name: "long", type: "multiline-text" }),
      mk({ name: "fixed", type: "text", width: 277 }),
    ] as unknown as Parameters<typeof TreeTable>[0]["columns"];
    const { container } = renderTable({ columns, labelColumn: "L" });
    const cols = Array.from(container.querySelectorAll("colgroup col"));
    expect(cols[0]).toHaveClass("arbor-col-label"); // label col
    const widthOf = (i: number) => (cols[i] as HTMLElement).style.width;
    expect(widthOf(1)).toBe("160px"); // text default
    expect(widthOf(2)).toBe("104px"); // number
    expect(widthOf(3)).toBe("184px"); // single-select-split
    expect(widthOf(4)).toBe("184px"); // multi-select-split
    expect(widthOf(5)).toBe("300px"); // multiline-text
    expect(widthOf(6)).toBe("277px"); // explicit user width wins
  });
});

describe("computeOverflowState (scroll-shadow affordance, UX P1-3)", () => {
  it("no cues when the table fits (no overflow)", () => {
    expect(computeOverflowState({ scrollLeft: 0, clientWidth: 800, scrollWidth: 800 })).toEqual({
      left: false,
      right: false,
    });
  });
  it("right cue only at the start of a wide table", () => {
    // 790px visible vs 2203px content — the real clipped case.
    expect(computeOverflowState({ scrollLeft: 0, clientWidth: 790, scrollWidth: 2203 })).toEqual({
      left: false,
      right: true,
    });
  });
  it("both cues when scrolled into the middle", () => {
    expect(computeOverflowState({ scrollLeft: 600, clientWidth: 790, scrollWidth: 2203 })).toEqual({
      left: true,
      right: true,
    });
  });
  it("right cue clears at end-of-travel (within 1px epsilon)", () => {
    // maxScroll = 2203 - 790 = 1413; landing within 1px counts as the end.
    expect(computeOverflowState({ scrollLeft: 1413, clientWidth: 790, scrollWidth: 2203 })).toEqual({
      left: true,
      right: false,
    });
    expect(
      computeOverflowState({ scrollLeft: 1412.4, clientWidth: 790, scrollWidth: 2203 }).right,
    ).toBe(false);
  });
});

describe("TreeTable scroll-shadow wiring", () => {
  it("sets data-overflow-right on mount when content is wider than the viewport", () => {
    renderTable();
    const vp = screen.getByTestId("table-viewport");
    // jsdom reports zero layout — stub a clipped geometry then fire a scroll to
    // run the same handler used on mount.
    Object.defineProperty(vp, "clientWidth", { configurable: true, value: 790 });
    Object.defineProperty(vp, "scrollWidth", { configurable: true, value: 2203 });
    vp.scrollLeft = 0;
    fireEvent.scroll(vp);
    expect(vp.hasAttribute("data-overflow-right")).toBe(true);
    expect(vp.hasAttribute("data-overflow-left")).toBe(false);

    // Scroll to the end → right cue clears, left cue appears.
    vp.scrollLeft = 2203 - 790;
    fireEvent.scroll(vp);
    expect(vp.hasAttribute("data-overflow-right")).toBe(false);
    expect(vp.hasAttribute("data-overflow-left")).toBe(true);
  });
});

describe("TreeTable CREATE affordances (PART C)", () => {
  it("renders a root '+ Add node' button that calls onAddNode()", () => {
    const onAddNode = vi.fn();
    renderTable({ onAddNode });
    fireEvent.click(screen.getByTestId("add-root-node"));
    expect(onAddNode).toHaveBeenCalledTimes(1);
  });

  it("threads onAddChild to each row; clicking a row's add-child calls it with the node", () => {
    const onAddChild = vi.fn();
    const { snap } = renderTable({ onAddChild });
    fireEvent.click(screen.getByTestId("add-child-P2"));
    const p2 = snap.nodes.find((n) => n.name === "P2");
    expect(onAddChild).toHaveBeenCalledWith(p2);
  });

  it("threads onAddSibling to each row; clicking a row's add-sibling calls it with the node", () => {
    const onAddSibling = vi.fn();
    const { snap } = renderTable({ onAddSibling });
    fireEvent.click(screen.getByTestId("add-sibling-P2"));
    const p2 = snap.nodes.find((n) => n.name === "P2");
    expect(onAddSibling).toHaveBeenCalledWith(p2);
  });

  it("omits the root add button when onAddNode is not supplied", () => {
    renderTable();
    expect(screen.queryByTestId("add-root-node")).not.toBeInTheDocument();
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
