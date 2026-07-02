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

describe("Cell — comment glyph", () => {
  it("no glyph when the cell has no comment summary", () => {
    render(<Cell column={notes(true)} value="v1" onCommit={() => {}} onOpenComments={() => {}} />);
    expect(screen.queryByTestId("comment-glyph")).toBeNull();
  });

  it("glyph shows the open count and fires onOpenComments", () => {
    const onOpen = vi.fn();
    render(
      <Cell
        column={notes(true)}
        value="v1"
        comments={{ open: 3, resolved: 1, unread: 0 }}
        onCommit={() => {}}
        onOpenComments={onOpen}
      />,
    );
    const glyph = screen.getByTestId("comment-glyph");
    expect(glyph).toHaveTextContent("3");
    fireEvent.click(glyph);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("an unread comment adds an unread marker on the glyph", () => {
    render(
      <Cell
        column={notes(true)}
        value="v1"
        comments={{ open: 2, resolved: 0, unread: 1 }}
        onCommit={() => {}}
        onOpenComments={() => {}}
      />,
    );
    expect(screen.getByTestId("comment-glyph")).toHaveAttribute("data-unread", "true");
  });

  it("a cell with ONLY resolved comments still shows the glyph (count 0)", () => {
    render(
      <Cell
        column={notes(true)}
        value="v1"
        comments={{ open: 0, resolved: 2, unread: 0 }}
        onCommit={() => {}}
        onOpenComments={() => {}}
      />,
    );
    // Shows a plain glyph (no numeric badge) so a resolved thread is still reachable.
    expect(screen.getByTestId("comment-glyph")).toBeInTheDocument();
  });

  it("clicking the glyph does not open the cell editor (stopPropagation)", () => {
    const onCommit = vi.fn();
    render(
      <Cell
        column={notes(true)}
        value="v1"
        comments={{ open: 1, resolved: 0, unread: 0 }}
        onCommit={onCommit}
        onOpenComments={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("comment-glyph"));
    expect(screen.queryByTestId("cell-input")).toBeNull();
  });

  it("glyph is inert (hidden) in Proposed preview", () => {
    render(
      <Cell
        column={notes(true)}
        value="v1"
        preview
        comments={{ open: 3, resolved: 0, unread: 1 }}
        onCommit={() => {}}
        onOpenComments={() => {}}
      />,
    );
    expect(screen.queryByTestId("comment-glyph")).toBeNull();
  });
});

describe("Cell — Proposed preview (read-only overlay)", () => {
  it("preview renders the value STATIC: no editor opens on click, no commit", () => {
    const onCommit = vi.fn();
    render(<Cell column={notes(true)} value="v1" preview onCommit={onCommit} />);
    const cell = screen.getByTestId("cell");
    expect(cell).toHaveAttribute("data-mode", "preview");
    fireEvent.click(cell);
    expect(screen.queryByTestId("cell-input")).toBeNull();
    expect(onCommit).not.toHaveBeenCalled();
  });

  it("preview shows no edit/suggest hint (read-only)", () => {
    const { container } = render(<Cell column={notes(false)} value="v1" preview onCommit={() => {}} />);
    expect(container.querySelector(".arbor-edit-hint")).toBeNull();
    expect(container.querySelector(".arbor-suggest-hint")).toBeNull();
  });

  it("a proposed cell gets a distinct proposed treatment (data-proposed + chip)", () => {
    render(<Cell column={notes(true)} value="proposed value" preview proposed onCommit={() => {}} />);
    const cell = screen.getByTestId("cell");
    expect(cell).toHaveAttribute("data-proposed", "true");
    expect(screen.getByTestId("proposed-marker")).toBeInTheDocument();
    expect(cell).toHaveTextContent("proposed value");
  });

  it("preview still renders the pending marker (the dot survives)", () => {
    render(<Cell column={notes(false)} value="v1" preview proposed pending pendingCount={2} onCommit={() => {}} />);
    expect(screen.getByTestId("pending-marker")).toHaveTextContent("2");
  });

  it("a preview cell that is NOT proposed carries neither data-proposed nor the chip", () => {
    render(<Cell column={notes(true)} value="v1" preview onCommit={() => {}} />);
    expect(screen.getByTestId("cell")).not.toHaveAttribute("data-proposed");
    expect(screen.queryByTestId("proposed-marker")).toBeNull();
  });

  it("preview applies even to a split (non-text) column — static, no editor", () => {
    const status: SnapshotColumn = {
      name: "col:status",
      field: "status",
      label: "Status",
      type: "single-select-split",
      is_label: false,
      column_owner: "C",
      editors: [],
      can_edit: true,
      options: { groups: [{ label: "Stage", options: ["todo", "done"] }] },
    };
    render(<Cell column={status} value={["done"]} preview proposed onCommit={() => {}} />);
    const cell = screen.getByTestId("cell");
    expect(cell).toHaveAttribute("data-mode", "preview");
    expect(cell).toHaveTextContent("done");
    expect(screen.getByTestId("proposed-marker")).toBeInTheDocument();
  });
});
