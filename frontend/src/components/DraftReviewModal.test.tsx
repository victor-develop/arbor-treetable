// Draft flow — DraftReviewModal contract. The modal groups staged drafts by
// resolved approver (column_owner), renders each field label + old → new diff,
// and fires onSubmit / onDiscardOne / onDiscardAll / onClose callbacks. It owns
// no mutation logic — it's presentation over already-resolved DraftRows.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DraftReviewModal, type DraftRow } from "./DraftReviewModal";

function rows(): DraftRow[] {
  return [
    {
      key: "X\0col:budget",
      node: "X",
      column: "col:budget",
      columnLabel: "Budget",
      nodeLabel: "Task X",
      oldValue: 1000,
      newValue: 500,
      approver: "C",
    },
    {
      key: "Y\0col:budget",
      node: "Y",
      column: "col:budget",
      columnLabel: "Budget",
      nodeLabel: "Task Y",
      oldValue: 5000,
      newValue: 5500,
      approver: "C",
    },
    {
      key: "X\0col:notes",
      node: "X",
      column: "col:notes",
      columnLabel: "Notes",
      nodeLabel: "Task X",
      oldValue: "v1",
      newValue: "v2",
      approver: "B",
    },
  ];
}

describe("DraftReviewModal", () => {
  it("groups drafts by resolved approver (column_owner)", () => {
    render(
      <DraftReviewModal
        drafts={rows()}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        onDiscardOne={vi.fn()}
        onDiscardAll={vi.fn()}
      />,
    );
    const groupC = screen.getByTestId("draft-group-C");
    const groupB = screen.getByTestId("draft-group-B");
    // approver C's group holds the two budget drafts; B's holds the notes draft.
    expect(within(groupC).getByTestId("draft-row-X-col:budget")).toBeInTheDocument();
    expect(within(groupC).getByTestId("draft-row-Y-col:budget")).toBeInTheDocument();
    expect(within(groupB).getByTestId("draft-row-X-col:notes")).toBeInTheDocument();
  });

  it("renders the field label + an old → new diff per draft", () => {
    render(
      <DraftReviewModal
        drafts={rows()}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        onDiscardOne={vi.fn()}
        onDiscardAll={vi.fn()}
      />,
    );
    const row = screen.getByTestId("draft-row-X-col:budget");
    expect(row).toHaveTextContent("Budget");
    expect(within(row).getByTestId("draft-old")).toHaveTextContent("1000");
    expect(within(row).getByTestId("draft-new")).toHaveTextContent("500");
  });

  it("per-draft discard (×) fires onDiscardOne with (node, column)", () => {
    const onDiscardOne = vi.fn();
    render(
      <DraftReviewModal
        drafts={rows()}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        onDiscardOne={onDiscardOne}
        onDiscardAll={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("draft-discard-X-col:budget"));
    expect(onDiscardOne).toHaveBeenCalledWith("X", "col:budget");
  });

  it("'Discard all' and 'Submit for approval' fire their callbacks", () => {
    const onSubmit = vi.fn();
    const onDiscardAll = vi.fn();
    render(
      <DraftReviewModal
        drafts={rows()}
        onClose={vi.fn()}
        onSubmit={onSubmit}
        onDiscardOne={vi.fn()}
        onDiscardAll={onDiscardAll}
      />,
    );
    fireEvent.click(screen.getByTestId("draft-discard-all"));
    expect(onDiscardAll).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("draft-submit"));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("backdrop click + close button fire onClose", () => {
    const onClose = vi.fn();
    render(
      <DraftReviewModal
        drafts={rows()}
        onClose={onClose}
        onSubmit={vi.fn()}
        onDiscardOne={vi.fn()}
        onDiscardAll={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("draft-modal-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("draft-modal")); // backdrop
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("disables submit/discard-all + shows an empty state when there are no drafts", () => {
    render(
      <DraftReviewModal
        drafts={[]}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        onDiscardOne={vi.fn()}
        onDiscardAll={vi.fn()}
      />,
    );
    expect(screen.getByTestId("draft-modal-empty")).toBeInTheDocument();
    expect(screen.getByTestId("draft-submit")).toBeDisabled();
    expect(screen.getByTestId("draft-discard-all")).toBeDisabled();
  });
});
