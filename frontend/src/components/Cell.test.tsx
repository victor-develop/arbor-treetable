import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Cell } from "./cells/Cell";
import type { SnapshotColumn } from "../api";

const notes = (canEdit: boolean): SnapshotColumn => ({
  name: "col:notes",
  field: "notes",
  label: "Notes",
  type: "multiline-text",
  is_label: false,
  column_owner: "B",
  editors: [],
  can_edit: canEdit,
});

const budget = (canEdit: boolean, editable = true): SnapshotColumn => ({
  name: "col:budget",
  field: "budget",
  label: "Budget",
  type: "number",
  is_label: false,
  column_owner: "C",
  editors: [],
  can_edit: canEdit,
  editable,
});

describe("Cell affordance driven by snapshot hint", () => {
  it("owned column renders in edit mode (WEB_UI-011/-013)", () => {
    render(<Cell column={notes(true)} value="v1" onCommit={() => {}} />);
    expect(screen.getByTestId("cell")).toHaveAttribute("data-mode", "edit");
  });

  it("non-owned column renders in suggest mode (WEB_UI-014/-015)", () => {
    render(<Cell column={notes(false)} value="v1" onCommit={() => {}} />);
    expect(screen.getByTestId("cell")).toHaveAttribute("data-mode", "suggest");
  });

  it("editable=false renders read-only for everyone (WEB_UI-016)", () => {
    const onCommit = vi.fn();
    render(<Cell column={budget(true, false)} value={1000} onCommit={onCommit} />);
    const cell = screen.getByTestId("cell");
    expect(cell).toHaveAttribute("data-mode", "readonly");
    fireEvent.click(cell);
    expect(screen.queryByTestId("cell-input")).toBeNull();
    expect(onCommit).not.toHaveBeenCalled();
  });
});

describe("Cell editing → onCommit routing", () => {
  it("commits a changed value once (WEB_UI-011)", () => {
    const onCommit = vi.fn();
    render(<Cell column={notes(true)} value="v1" onCommit={onCommit} />);
    fireEvent.click(screen.getByTestId("cell"));
    const input = screen.getByTestId("cell-input");
    fireEvent.change(input, { target: { value: "ship by Q3" } });
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledTimes(1);
    expect(onCommit).toHaveBeenCalledWith("ship by Q3");
  });

  it("Escape cancels without committing (WEB_UI-017)", () => {
    const onCommit = vi.fn();
    render(<Cell column={notes(true)} value="v1" onCommit={onCommit} />);
    fireEvent.click(screen.getByTestId("cell"));
    const input = screen.getByTestId("cell-input");
    fireEvent.change(input, { target: { value: "changed" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onCommit).not.toHaveBeenCalled();
  });

  it("unchanged value commit is a no-op (WEB_UI-018)", () => {
    const onCommit = vi.fn();
    render(<Cell column={notes(true)} value="v1" onCommit={onCommit} />);
    fireEvent.click(screen.getByTestId("cell"));
    const input = screen.getByTestId("cell-input");
    fireEvent.blur(input); // committed unchanged
    expect(onCommit).not.toHaveBeenCalled();
  });

  it("number column blocks non-numeric input client-side (WEB_UI-019)", () => {
    const onCommit = vi.fn();
    render(<Cell column={budget(true)} value={1000} onCommit={onCommit} />);
    fireEvent.click(screen.getByTestId("cell"));
    const input = screen.getByTestId("cell-input");
    fireEvent.change(input, { target: { value: "abc" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onCommit).not.toHaveBeenCalled();
    expect(screen.getByTestId("invalid-hint")).toBeInTheDocument();
  });

  it("explicit empty value is sent as a deliberate clear (WEB_UI-024)", () => {
    const onCommit = vi.fn();
    render(<Cell column={notes(true)} value="v1" onCommit={onCommit} />);
    fireEvent.click(screen.getByTestId("cell"));
    const input = screen.getByTestId("cell-input");
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledWith("");
  });

  it("pending marker renders when a suggestion is outstanding (WEB_UI-014)", () => {
    render(<Cell column={notes(false)} value="v1" pending onCommit={() => {}} />);
    expect(screen.getByTestId("pending-marker")).toBeInTheDocument();
  });
});
