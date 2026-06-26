// Unit spec for TreeRow's per-row CREATE control — the "add child" affordance
// that sits next to the delete control (PART C). Unlike delete (gated on
// can_change_structure), the add-child button shows for EVERYONE: a non-owner
// click files a CR, exactly like "Suggest column". The button calls
// onAddChild(node) and never re-derives ACL.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TreeRow } from "./TreeRow";
import type { SnapshotColumn } from "../api";
import type { TreeRow as Row } from "../lib/tree";

const labelCol: SnapshotColumn = {
  name: "col:name",
  field: "name",
  label: "Name",
  type: "text",
  is_label: true,
  column_owner: "B",
  editors: [],
  can_edit: false,
};

function makeRow(over?: Partial<Row["node"]>): Row {
  return {
    node: {
      name: "X",
      parent: "P1",
      lft: 3,
      rgt: 4,
      label: "Task X",
      values: { "col:name": "Task X" },
      can_change_structure: false,
      ...over,
    },
    depth: 1,
    hasChildren: false,
  } as Row;
}

function renderRow(over?: {
  node?: Partial<Row["node"]>;
  onAddChild?: (n: unknown) => void;
  onAddSibling?: (n: unknown) => void;
  onDelete?: (n: unknown) => void;
}) {
  const onAddChild = over?.onAddChild ?? vi.fn();
  const onAddSibling = over?.onAddSibling ?? vi.fn();
  const row = makeRow(over?.node);
  render(
    <table>
      <tbody>
        <TreeRow
          row={row}
          columns={[labelCol]}
          labelColumn="col:name"
          collapsed={false}
          pendingCell={() => false}
          pendingMove={false}
          onToggle={() => {}}
          onCommitCell={() => {}}
          onDragStart={() => {}}
          onDrop={() => {}}
          onAddChild={onAddChild}
          onAddSibling={onAddSibling}
          onDelete={over?.onDelete}
        />
      </tbody>
    </table>,
  );
  return { onAddChild, onAddSibling, row };
}

describe("TreeRow add-child control (PART C)", () => {
  it("renders an add-child button and calls onAddChild(node) on click", () => {
    const { onAddChild, row } = renderRow();
    const btn = screen.getByTestId("add-child-X");
    fireEvent.click(btn);
    expect(onAddChild).toHaveBeenCalledWith(row.node);
  });

  it("shows the add-child button for a NON-owner (not gated on can_change_structure)", () => {
    // can_change_structure:false → no delete control, but add-child still shows
    // (a non-owner click just files a CR, same as Suggest column).
    renderRow({ node: { can_change_structure: false } });
    expect(screen.getByTestId("add-child-X")).toBeInTheDocument();
    expect(screen.queryByTestId("delete-node-X")).not.toBeInTheDocument();
  });

  it("renders both add-child and delete for an owner", () => {
    renderRow({ node: { can_change_structure: true }, onDelete: vi.fn() });
    expect(screen.getByTestId("add-child-X")).toBeInTheDocument();
    expect(screen.getByTestId("delete-node-X")).toBeInTheDocument();
  });

  it("renders an add-sibling button and calls onAddSibling(node) on click", () => {
    const { onAddSibling, row } = renderRow();
    const btn = screen.getByTestId("add-sibling-X");
    fireEvent.click(btn);
    expect(onAddSibling).toHaveBeenCalledWith(row.node);
  });

  it("orders the actions cluster +sibling, +child, delete", () => {
    renderRow({ node: { can_change_structure: true }, onDelete: vi.fn() });
    const cluster = screen.getByTestId("add-sibling-X").closest("td")!;
    const actionTestIds = Array.from(
      cluster.querySelectorAll("[data-testid]"),
    ).map((el) => el.getAttribute("data-testid"));
    expect(actionTestIds).toEqual(["add-sibling-X", "add-child-X", "delete-node-X"]);
  });

  it("renders no add-child button when onAddChild is not supplied", () => {
    const row = makeRow();
    render(
      <table>
        <tbody>
          <TreeRow
            row={row}
            columns={[labelCol]}
            labelColumn="col:name"
            collapsed={false}
            pendingCell={() => false}
            pendingMove={false}
            onToggle={() => {}}
            onCommitCell={() => {}}
            onDragStart={() => {}}
            onDrop={() => {}}
          />
        </tbody>
      </table>,
    );
    expect(screen.queryByTestId("add-child-X")).not.toBeInTheDocument();
  });
});
