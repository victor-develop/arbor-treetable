// Feature 2 (shareable views) — ViewMenu component tests, written RED before
// src/components/ViewMenu.tsx exists. ViewMenu is presentation-only: it lists the
// snapshot-present (already read-ACL-filtered) columns and lets the user toggle
// visibility / reorder / resize. Every interaction emits a new SheetView via
// onChange and issues ZERO executeAction calls (views never mutate the backend).

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ViewMenu, reorderByDrag } from "./ViewMenu";
import type { SheetView } from "../lib/view";
import type { SnapshotColumn } from "../api";

function col(name: string, opts: Partial<SnapshotColumn> = {}): SnapshotColumn {
  return {
    name,
    field: name.replace(/^col:/, ""),
    label: name.replace(/^col:/, ""),
    type: "text",
    is_label: false,
    column_owner: "A",
    editors: [],
    can_edit: false,
    ...opts,
  };
}

const LABEL = col("col:name", { is_label: true, label: "Name" });
const STATUS = col("col:status", { label: "Status" });
const BUDGET = col("col:budget", { label: "Budget" });
const COLS: SnapshotColumn[] = [LABEL, STATUS, BUDGET];

const baseView: SheetView = { v: 1, hidden: [], order: [] };

describe("ViewMenu — lists only snapshot-present columns", () => {
  it("renders a toggle row for each NON-label snapshot column and none for absent columns", () => {
    const onChange = vi.fn();
    render(<ViewMenu columns={COLS} view={baseView} onChange={onChange} />);
    // status + budget are toggleable; an absent col:secret never appears.
    expect(screen.getByTestId("view-col-col:status")).toBeInTheDocument();
    expect(screen.getByTestId("view-col-col:budget")).toBeInTheDocument();
    expect(screen.queryByTestId("view-col-col:secret")).not.toBeInTheDocument();
  });

  it("does not offer the label column as a hideable toggle (label always visible)", () => {
    render(<ViewMenu columns={COLS} view={baseView} onChange={vi.fn()} />);
    expect(screen.queryByTestId("view-col-col:name")).not.toBeInTheDocument();
  });

  it("renders the toggle as the bare label and conveys state via aria-pressed + aria-label (no visible '· Visible/Hidden' suffix)", () => {
    render(
      <ViewMenu
        columns={COLS}
        view={{ v: 1, hidden: ["col:budget"], order: [] }}
        onChange={vi.fn()}
      />,
    );
    // Visible toggle: bare label text, aria-pressed=true, state only in aria-label.
    const statusToggle = screen.getByTestId("view-toggle-col:status");
    expect(statusToggle).toHaveTextContent(/^Status$/);
    expect(statusToggle).not.toHaveTextContent(/Visible|Hidden/);
    expect(statusToggle).toHaveAttribute("aria-pressed", "true");
    expect(statusToggle).toHaveAttribute("aria-label", "Status — visible");
    // Hidden toggle: bare label text, aria-pressed=false, "hidden" only in aria-label.
    const budgetToggle = screen.getByTestId("view-toggle-col:budget");
    expect(budgetToggle).toHaveTextContent(/^Budget$/);
    expect(budgetToggle).not.toHaveTextContent(/Visible|Hidden/);
    expect(budgetToggle).toHaveAttribute("aria-pressed", "false");
    expect(budgetToggle).toHaveAttribute("aria-label", "Budget — hidden");
    // the styled checkbox is still present to convey state visually.
    expect(
      budgetToggle.querySelector(".arbor-view-check"),
    ).toBeInTheDocument();
  });
});

describe("ViewMenu — interactions emit SheetView, never executeAction", () => {
  it("toggling visibility issues ZERO executeAction and emits an updated view via onChange", () => {
    const executeAction = vi.fn();
    const onChange = vi.fn();
    render(
      <ViewMenu
        columns={COLS}
        view={baseView}
        onChange={onChange}
        // a spy that MUST never be called — proves views are mutation-free.
        client={{ executeAction } as never}
      />,
    );
    fireEvent.click(screen.getByTestId("view-toggle-col:budget"));
    expect(executeAction).not.toHaveBeenCalled();
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as SheetView;
    expect(next.hidden).toContain("col:budget");
  });

  it("un-hiding a hidden column emits a view without it in hidden", () => {
    const onChange = vi.fn();
    render(
      <ViewMenu
        columns={COLS}
        view={{ v: 1, hidden: ["col:budget"], order: [] }}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByTestId("view-toggle-col:budget"));
    const next = onChange.mock.calls[0][0] as SheetView;
    expect(next.hidden).not.toContain("col:budget");
  });

  it("reordering issues ZERO executeAction and emits a view with the new order", () => {
    const executeAction = vi.fn();
    const onChange = vi.fn();
    render(
      <ViewMenu
        columns={COLS}
        view={baseView}
        onChange={onChange}
        client={{ executeAction } as never}
      />,
    );
    // move budget up one slot (above status).
    fireEvent.click(screen.getByTestId("view-up-col:budget"));
    expect(executeAction).not.toHaveBeenCalled();
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as SheetView;
    // order now leads with budget ahead of status (label excluded from order).
    expect(next.order.indexOf("col:budget")).toBeLessThan(
      next.order.indexOf("col:status"),
    );
  });

  it("setting a width issues ZERO executeAction and emits a view with the width", () => {
    const executeAction = vi.fn();
    const onChange = vi.fn();
    render(
      <ViewMenu
        columns={COLS}
        view={baseView}
        onChange={onChange}
        client={{ executeAction } as never}
      />,
    );
    fireEvent.change(screen.getByTestId("view-width-col:status"), {
      target: { value: "260" },
    });
    expect(executeAction).not.toHaveBeenCalled();
    const next = onChange.mock.calls.at(-1)![0] as SheetView;
    expect(next.width?.["col:status"]).toBe(260);
  });
});

describe("reorderByDrag — pure reorder helper", () => {
  it("moves the dragged name to the drop target's slot (downward)", () => {
    expect(reorderByDrag(["a", "b", "c"], "a", "c")).toEqual(["b", "c", "a"]);
  });
  it("moves the dragged name to the drop target's slot (upward)", () => {
    expect(reorderByDrag(["a", "b", "c"], "c", "a")).toEqual(["c", "a", "b"]);
  });
  it("is a no-op when source and target are the same", () => {
    expect(reorderByDrag(["a", "b", "c"], "b", "b")).toEqual(["a", "b", "c"]);
  });
  it("is a no-op when either name is absent", () => {
    expect(reorderByDrag(["a", "b"], "x", "a")).toEqual(["a", "b"]);
    expect(reorderByDrag(["a", "b"], "a", "x")).toEqual(["a", "b"]);
  });
  it("returns a new array (does not mutate the input)", () => {
    const input = ["a", "b", "c"];
    const out = reorderByDrag(input, "a", "b");
    expect(out).not.toBe(input);
    expect(input).toEqual(["a", "b", "c"]);
  });
});

describe("ViewMenu — drag-to-reorder", () => {
  it("renders a draggable drag handle on every data-column row", () => {
    render(<ViewMenu columns={COLS} view={baseView} onChange={vi.fn()} />);
    const handle = screen.getByTestId("view-handle-col:status");
    expect(handle).toBeInTheDocument();
    // the row itself carries draggable so the whole grip-row drags.
    const row = screen.getByTestId("view-col-col:status");
    expect(row).toHaveAttribute("draggable", "true");
  });

  it("drag-dropping a row issues ZERO executeAction and emits the new order", () => {
    const executeAction = vi.fn();
    const onChange = vi.fn();
    render(
      <ViewMenu
        columns={COLS}
        view={baseView}
        onChange={onChange}
        client={{ executeAction } as never}
      />,
    );
    const status = screen.getByTestId("view-col-col:status");
    const budget = screen.getByTestId("view-col-col:budget");
    // grab status, drag over budget, drop → status lands after budget.
    fireEvent.dragStart(status);
    fireEvent.dragEnter(budget);
    fireEvent.dragOver(budget);
    fireEvent.drop(budget);
    expect(executeAction).not.toHaveBeenCalled();
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as SheetView;
    expect(next.order.indexOf("col:budget")).toBeLessThan(
      next.order.indexOf("col:status"),
    );
  });
});
