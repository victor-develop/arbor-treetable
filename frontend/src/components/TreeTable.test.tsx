import { createEvent, fireEvent, render, screen, within } from "@testing-library/react";
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

  it("threads onEdit to each row; clicking a row's edit calls it with the node", () => {
    const onEdit = vi.fn();
    const { snap } = renderTable({ onEdit });
    fireEvent.click(screen.getByTestId("edit-node-P2"));
    const p2 = snap.nodes.find((n) => n.name === "P2");
    expect(onEdit).toHaveBeenCalledWith(p2);
  });

  it("renders NO trailing actions column (the cluster lives in the frozen-left label cell)", () => {
    const { container } = renderTable({ onAddChild: vi.fn(), onDeleteNode: vi.fn() });
    // No trailing actions <col>, header <th>, or body <td>.
    expect(container.querySelector("col.arbor-col-actions")).toBeNull();
    expect(container.querySelector("th.arbor-actions-head")).toBeNull();
    expect(container.querySelector("td.arbor-actions-cell")).toBeNull();
  });

  it("omits the root add button when onAddNode is not supplied", () => {
    renderTable();
    expect(screen.queryByTestId("add-root-node")).not.toBeInTheDocument();
  });
});

describe("TreeTable — Proposed preview (read-only)", () => {
  it("hides the drag handle and the per-row action cluster in preview", () => {
    renderTable({
      preview: true,
      onAddChild: vi.fn(),
      onAddSibling: vi.fn(),
      onEdit: vi.fn(),
      onDeleteNode: vi.fn(),
    });
    // No drag handle anywhere.
    expect(screen.queryByTestId("drag-handle-P2")).toBeNull();
    // No row-action buttons.
    expect(screen.queryByTestId("add-child-P2")).toBeNull();
    expect(screen.queryByTestId("add-sibling-P2")).toBeNull();
    expect(screen.queryByTestId("edit-node-P2")).toBeNull();
    expect(screen.queryByTestId("delete-node-P2")).toBeNull();
  });

  it("still allows chevron expand/collapse in preview", () => {
    const { onToggle } = renderTable({ preview: true });
    fireEvent.click(screen.getByTestId("chevron-P2"));
    expect(onToggle).toHaveBeenCalledWith("P2");
  });

  it("renders cells static in preview: clicking a cell does NOT open an editor", () => {
    const { onCommitCell } = renderTable({ preview: true });
    const notesCell = screen.getByTestId("row-X").querySelector('[data-column="col:notes"] [data-testid="cell"]')!;
    expect(notesCell).toHaveAttribute("data-mode", "preview");
    fireEvent.click(notesCell);
    expect(screen.queryByTestId("cell-input")).toBeNull();
    expect(onCommitCell).not.toHaveBeenCalled();
  });

  it("marks proposed cells + moved rows via the predicates", () => {
    renderTable({
      preview: true,
      proposedCell: (n, c) => n === "X" && c === "col:budget",
      movedNode: (n) => n === "X",
    });
    const budgetCell = screen.getByTestId("row-X").querySelector('[data-column="col:budget"] [data-testid="cell"]')!;
    expect(budgetCell).toHaveAttribute("data-proposed", "true");
    expect(screen.getByTestId("moved-X")).toBeInTheDocument();
    // A non-proposed cell is unmarked; a non-moved row has no tag.
    const yBudget = screen.getByTestId("row-Y").querySelector('[data-column="col:budget"] [data-testid="cell"]')!;
    expect(yBudget).not.toHaveAttribute("data-proposed");
    expect(screen.queryByTestId("moved-Y")).toBeNull();
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
    fireEvent.dragStart(screen.getByTestId("drag-handle-Y")); // drag Y via its handle
    dropFromTop("row-P2", 0.5); // middle of a group = inside
    expect(onMove).toHaveBeenCalledWith({ node: "Y", new_parent: "P2", after: null });
  });

  it("illegal drop onto own descendant is suppressed (WEB_UI-044)", () => {
    const { onMove } = renderTable();
    fireEvent.dragStart(screen.getByTestId("drag-handle-P2"));
    dropFromTop("row-Z", 0.5);
    expect(onMove).not.toHaveBeenCalled();
  });

  function stubRect(testId: string) {
    const row = screen.getByTestId(testId);
    row.getBoundingClientRect = () =>
      ({ top: 0, height: 90, left: 0, right: 0, bottom: 90, width: 0, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
    return row;
  }
  // fireEvent(...).dragOver drops the clientY init for drag events, so build the
  // event and pin clientY on it directly (positionFromEvent reads e.clientY).
  function dragOverAt(row: HTMLElement, fraction: number) {
    const ev = createEvent.dragOver(row);
    Object.defineProperty(ev, "clientY", { value: 90 * fraction });
    fireEvent(row, ev);
  }

  it("shows a live drop indicator on the hovered row (before/inside/after) and clears it on drop", () => {
    renderTable();
    fireEvent.dragStart(screen.getByTestId("drag-handle-Y"));
    const p2 = stubRect("row-P2");
    dragOverAt(p2, 0.05); // top third → before
    expect(p2).toHaveAttribute("data-drop", "before");
    dragOverAt(p2, 0.5); // middle → inside
    expect(p2).toHaveAttribute("data-drop", "inside");
    dragOverAt(p2, 0.95); // bottom third → after
    expect(p2).toHaveAttribute("data-drop", "after");
    fireEvent.drop(p2, { clientY: 90 * 0.5 });
    expect(p2).not.toHaveAttribute("data-drop"); // cleared after the drop
  });

  it("suppresses the drop indicator over an illegal target (own descendant)", () => {
    renderTable();
    fireEvent.dragStart(screen.getByTestId("drag-handle-P2"));
    const z = stubRect("row-Z"); // Z is inside P2 → illegal
    dragOverAt(z, 0.5);
    expect(z).not.toHaveAttribute("data-drop");
  });

  it("clears the drop indicator when the drag ends without a drop (cancel)", () => {
    renderTable();
    fireEvent.dragStart(screen.getByTestId("drag-handle-Y"));
    const p2 = stubRect("row-P2");
    dragOverAt(p2, 0.5);
    expect(p2).toHaveAttribute("data-drop", "inside");
    fireEvent.dragEnd(screen.getByTestId("drag-handle-Y"));
    expect(p2).not.toHaveAttribute("data-drop");
  });
});
describe("ghost column quick-add (insert to the right)", () => {
  // Header cells of the rendered <thead>, in DOM order — the ghost column has
  // to land at the right INDEX among them, not merely somewhere in the row.
  const headOrder = (container: HTMLElement) =>
    Array.from(container.querySelectorAll("thead th")).map(
      (th) => th.getAttribute("data-testid") ?? "label",
    );

  it("renders no ghost affordance without onCreateColumn", () => {
    renderTable();
    expect(screen.queryByTestId(/^ghost-col-open/)).toBeNull();
    expect(screen.queryByTestId("ghost-col-head")).toBeNull();
  });

  it("idle state reserves NO blank column — one hover + per data column header", () => {
    const { container } = renderTable({ onCreateColumn: vi.fn() });
    expect(screen.queryByTestId("ghost-col-head")).toBeNull();
    expect(container.querySelectorAll("td.arbor-ghost-cell")).toHaveLength(0);
    // Every data column carries its own opener, each inside its own header.
    // Openers are keyed on `field` (stable + predictable), headers on the id.
    const dataCols = loginAs("D").columns.filter((c) => !c.is_label);
    expect(screen.getAllByTestId(/^ghost-col-open-/)).toHaveLength(dataCols.length);
    dataCols.forEach((c) => {
      expect(screen.getByTestId(`col-head-${c.name}`)).toContainElement(
        screen.getByTestId(`ghost-col-open-${c.field}`),
      );
    });
    // Named for the column it inserts after, so each one is distinguishable.
    expect(screen.getByTestId("ghost-col-open-budget")).toHaveAttribute(
      "aria-label",
      "Insert column to the right of Budget",
    );
  });

  it("the hover + opens the inline editor; Enter submits the trimmed label + the anchor", () => {
    const onCreateColumn = vi.fn();
    renderTable({ onCreateColumn });
    fireEvent.click(screen.getByTestId("ghost-col-open-status"));
    const input = screen.getByTestId("ghost-col-input");
    fireEvent.change(input, { target: { value: "  Due Date  " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onCreateColumn).toHaveBeenCalledWith("Due Date", "col:status");
    // Editor + its transient column dissolve after submit.
    expect(screen.queryByTestId("ghost-col-head")).toBeNull();
  });

  it("the LAST column's + still means append (its own name is the rightmost anchor)", () => {
    const onCreateColumn = vi.fn();
    const { container } = renderTable({ onCreateColumn });
    fireEvent.click(screen.getByTestId("ghost-col-open-tags"));
    expect(headOrder(container).at(-1)).toBe("ghost-col-head");
    fireEvent.change(screen.getByTestId("ghost-col-input"), { target: { value: "Due" } });
    fireEvent.keyDown(screen.getByTestId("ghost-col-input"), { key: "Enter" });
    expect(onCreateColumn).toHaveBeenCalledWith("Due", "col:tags");
  });

  it("the ghost column materializes immediately to the RIGHT of the hovered column", () => {
    const { container } = renderTable({ onCreateColumn: vi.fn() });
    fireEvent.click(screen.getByTestId("ghost-col-open-status"));
    expect(headOrder(container)).toEqual([
      "label",
      "col-head-col:status",
      "ghost-col-head",
      "col-head-col:budget",
      "col-head-col:notes",
      "col-head-col:tags",
    ]);
    // The colgroup grows in lockstep, so the transient column has a width.
    expect(container.querySelectorAll("colgroup col.arbor-col-ghost")).toHaveLength(1);
  });

  it("each row's pad cell sits at the SAME index as the ghost header", () => {
    const { container } = renderTable({ onCreateColumn: vi.fn() });
    fireEvent.click(screen.getByTestId("ghost-col-open-status"));
    const ghostIndex = headOrder(container).indexOf("ghost-col-head");
    const rows = screen.getAllByTestId(/^row-/);
    expect(container.querySelectorAll("td.arbor-ghost-cell")).toHaveLength(rows.length);
    rows.forEach((row) => {
      const cells = Array.from(row.querySelectorAll("td"));
      expect(cells[ghostIndex]).toHaveClass("arbor-ghost-cell");
    });
  });

  it("Escape dissolves the ghost column without creating; empty label creates nothing", () => {
    const onCreateColumn = vi.fn();
    renderTable({ onCreateColumn });
    fireEvent.click(screen.getByTestId("ghost-col-open-notes"));
    fireEvent.keyDown(screen.getByTestId("ghost-col-input"), { key: "Escape" });
    expect(screen.queryByTestId("ghost-col-head")).toBeNull();
    fireEvent.click(screen.getByTestId("ghost-col-open-notes"));
    fireEvent.keyDown(screen.getByTestId("ghost-col-input"), { key: "Enter" });
    expect(onCreateColumn).not.toHaveBeenCalled();
  });

  it("a sheet with no data columns keeps its label-header +, which appends", () => {
    const onCreateColumn = vi.fn();
    const labelOnly = loginAs("D").columns.filter((c) => c.is_label);
    renderTable({ onCreateColumn, columns: labelOnly });
    expect(screen.queryAllByTestId(/^ghost-col-open-(?!label$)/)).toHaveLength(0);
    fireEvent.click(screen.getByTestId("ghost-col-open-label"));
    const input = screen.getByTestId("ghost-col-input");
    fireEvent.change(input, { target: { value: "Status" } });
    fireEvent.keyDown(input, { key: "Enter" });
    // No anchor exists yet, so the new column can only be appended.
    expect(onCreateColumn).toHaveBeenCalledWith("Status", null);
  });

  it("keeps the open ghost visible when the sheet loses a column under it", () => {
    // `ghost.index` is captured at click time. Unclamped, a shrunk column list
    // renders no ghost <th>/<col>/pad while `ghost` stays set — and since every
    // "+" is gated on !ghost, quick add would disappear until a remount.
    const snap = loginAs("D");
    const table = (columns: typeof snap.columns) => (
      <TreeTable
        columns={columns}
        nodes={snap.nodes}
        labelColumn={snap.label_column}
        collapsed={new Set<string>()}
        onToggle={vi.fn()}
        pendingCell={() => false}
        isPendingMove={() => false}
        onCommitCell={vi.fn()}
        onMove={vi.fn()}
        onCreateColumn={vi.fn()}
      />
    );
    const { container, rerender } = render(table(snap.columns));
    const last = snap.columns.filter((c) => !c.is_label).at(-1)!;
    fireEvent.click(screen.getByTestId(`ghost-col-open-${last.field}`));
    expect(headOrder(container).at(-1)).toBe("ghost-col-head");
    rerender(table(snap.columns.filter((c) => c.name !== last.name)));
    expect(headOrder(container).at(-1)).toBe("ghost-col-head");
    expect(container.querySelectorAll("colgroup col.arbor-col-ghost")).toHaveLength(1);
    expect(container.querySelectorAll("td.arbor-ghost-cell")).toHaveLength(
      screen.getAllByTestId(/^row-/).length,
    );
  });

  it("preview is read-only: no + on any header, label header included", () => {
    // Same rule as the per-row cluster above. Before this gate the Proposed
    // preview offered one "+" per data column — every one of them a write.
    const onCreateColumn = vi.fn();
    renderTable({ onCreateColumn, preview: true });
    expect(screen.queryByTestId(/^ghost-col-open/)).toBeNull();
    const labelOnly = loginAs("D").columns.filter((c) => c.is_label);
    renderTable({ onCreateColumn, preview: true, columns: labelOnly });
    expect(screen.queryByTestId("ghost-col-open-label")).toBeNull();
  });
});

// Header drag → reorder MY VIEW. The grid already carries two drag surfaces
// (rows drag by their own grip; every data header has a hover "+" and a gear),
// so the column grip has to be discoverable WITHOUT swallowing those.
describe("column-header drag → reorder my view", () => {
  it("renders no grip without onReorderColumns", () => {
    renderTable();
    expect(screen.queryByTestId(/^col-grip-/)).toBeNull();
  });

  it("puts a draggable grip in EVERY data column header (never on the label)", () => {
    renderTable({ onReorderColumns: vi.fn() });
    const dataCols = loginAs("D").columns.filter((c) => !c.is_label);
    expect(screen.getAllByTestId(/^col-grip-/)).toHaveLength(dataCols.length);
    dataCols.forEach((c) => {
      const grip = screen.getByTestId(`col-grip-${c.field}`);
      expect(grip).toHaveAttribute("draggable", "true");
      expect(screen.getByTestId(`col-head-${c.name}`)).toContainElement(grip);
    });
    // The label header is not reorderable, so it has no grip.
    expect(screen.getByTestId("col-head-col:status")).toBeInTheDocument();
    expect(document.querySelector("th.arbor-label-head .arbor-col-grip")).toBeNull();
  });

  it("dropping a grip on another header emits (from, to) as column IDS", () => {
    const onReorderColumns = vi.fn();
    renderTable({ onReorderColumns });
    fireEvent.dragStart(screen.getByTestId("col-grip-budget"));
    fireEvent.dragEnter(screen.getByTestId("col-head-col:status"));
    fireEvent.dragOver(screen.getByTestId("col-head-col:status"));
    fireEvent.drop(screen.getByTestId("col-head-col:status"));
    expect(onReorderColumns).toHaveBeenCalledWith("col:budget", "col:status");
  });

  it("dropping a header on itself emits nothing", () => {
    const onReorderColumns = vi.fn();
    renderTable({ onReorderColumns });
    fireEvent.dragStart(screen.getByTestId("col-grip-budget"));
    fireEvent.drop(screen.getByTestId("col-head-col:budget"));
    expect(onReorderColumns).not.toHaveBeenCalled();
  });

  it("a ROW drag passing over a header is not treated as a column drop", () => {
    // The two drag surfaces share the same DOM tree; a row drag must reach
    // onMove, never the column reorder (no dragStart happened on a grip).
    const onReorderColumns = vi.fn();
    const { onMove } = renderTable({ onReorderColumns });
    fireEvent.dragStart(screen.getByTestId("drag-handle-Y"));
    fireEvent.drop(screen.getByTestId("col-head-col:status"));
    expect(onReorderColumns).not.toHaveBeenCalled();
    expect(onMove).not.toHaveBeenCalled();
  });

  it("keeps the header + and gear clickable while a grip is present", () => {
    const onCreateColumn = vi.fn();
    const onColumnSettings = vi.fn();
    renderTable({ onReorderColumns: vi.fn(), onCreateColumn, onColumnSettings });
    fireEvent.click(screen.getByTestId("col-settings-open-col:status"));
    expect(onColumnSettings).toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("ghost-col-open-status"));
    expect(screen.getByTestId("ghost-col-input")).toBeInTheDocument();
  });

  it("the read-only Proposed preview offers no grip", () => {
    renderTable({ onReorderColumns: vi.fn(), preview: true });
    expect(screen.queryByTestId(/^col-grip-/)).toBeNull();
  });
});
