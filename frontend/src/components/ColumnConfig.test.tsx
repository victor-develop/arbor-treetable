import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AddColumnForm, ColumnSettings } from "./ColumnConfig";
import type { SnapshotColumn } from "../api";

// The form is now collapsed behind a "+ Add column" toggle by default. Most
// specs need the expanded form, so render + expand in one step.
function renderExpanded(props: Parameters<typeof AddColumnForm>[0]) {
  const utils = render(<AddColumnForm {...props} />);
  fireEvent.click(screen.getByTestId("add-column-toggle"));
  return utils;
}

describe("AddColumnForm", () => {
  it("is collapsed by default — only the toggle shows, fields hidden until toggled", () => {
    render(<AddColumnForm sheet="S" existingFields={[]} canAdd onSubmit={() => {}} />);
    // Compact toggle is the only thing in the toolbar slot.
    expect(screen.getByTestId("add-column-toggle")).toBeInTheDocument();
    // The full form + its fields are not mounted yet.
    expect(screen.queryByTestId("add-column-form")).toBeNull();
    expect(screen.queryByTestId("ac-field")).toBeNull();
    expect(screen.queryByTestId("ac-label")).toBeNull();
    // Toggling reveals the form...
    fireEvent.click(screen.getByTestId("add-column-toggle"));
    expect(screen.getByTestId("add-column-form")).toBeInTheDocument();
    expect(screen.getByTestId("ac-field")).toBeInTheDocument();
    // ...and there's a way to collapse it again.
    fireEvent.click(screen.getByTestId("ac-cancel"));
    expect(screen.queryByTestId("add-column-form")).toBeNull();
    expect(screen.getByTestId("add-column-toggle")).toBeInTheDocument();
  });

  it("orders Label before Field key (humans think label-first)", () => {
    renderExpanded({ sheet: "S", existingFields: [], canAdd: true, onSubmit: () => {} });
    const form = screen.getByTestId("add-column-form");
    const labelField = screen.getByTestId("ac-label").closest(".arbor-field")!;
    const keyField = screen.getByTestId("ac-field").closest(".arbor-field")!;
    const kids = Array.from(form.children);
    expect(kids.indexOf(labelField)).toBeGreaterThanOrEqual(0);
    expect(kids.indexOf(keyField)).toBeGreaterThanOrEqual(0);
    expect(kids.indexOf(labelField)).toBeLessThan(kids.indexOf(keyField));
    // Visible-label text order matches.
    const labelText = screen.getByText("Label");
    const keyText = screen.getByText("Field key");
    expect(labelText.compareDocumentPosition(keyText) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("auto-fills the Field key with a slug of the Label as you type", () => {
    renderExpanded({ sheet: "S", existingFields: [], canAdd: true, onSubmit: () => {} });
    fireEvent.change(screen.getByTestId("ac-label"), { target: { value: "Revenue Forecast ($)" } });
    expect(screen.getByTestId("ac-field")).toHaveValue("revenue_forecast");
    // Keeps tracking the label until the user intervenes.
    fireEvent.change(screen.getByTestId("ac-label"), { target: { value: "Q3 Plan!!" } });
    expect(screen.getByTestId("ac-field")).toHaveValue("q3_plan");
  });

  it("stops auto-syncing once the user manually edits the Field key (sticky)", () => {
    renderExpanded({ sheet: "S", existingFields: [], canAdd: true, onSubmit: () => {} });
    fireEvent.change(screen.getByTestId("ac-label"), { target: { value: "Revenue" } });
    expect(screen.getByTestId("ac-field")).toHaveValue("revenue");
    // User takes over the key.
    fireEvent.change(screen.getByTestId("ac-field"), { target: { value: "rev_2026" } });
    // Further label typing must NOT overwrite it.
    fireEvent.change(screen.getByTestId("ac-label"), { target: { value: "Revenue Forecast" } });
    expect(screen.getByTestId("ac-field")).toHaveValue("rev_2026");
  });

  it("offers exactly the allowed type enum (WEB_UI-053)", () => {
    renderExpanded({ sheet: "S", existingFields: [], canAdd: true, onSubmit: () => {} });
    const opts = Array.from(screen.getByTestId("ac-type").querySelectorAll("option")).map(
      (o) => o.getAttribute("value"),
    );
    expect(opts).toEqual([
      "text",
      "multiline-text",
      "number",
      "single-select-split",
      "multi-select-split",
    ]);
  });

  it("rejects a duplicate field key before submit (WEB_UI-062)", () => {
    const onSubmit = vi.fn();
    renderExpanded({ sheet: "S", existingFields: ["name"], canAdd: true, onSubmit });
    // Auto-slug "Name" -> "name", which collides with the existing field.
    fireEvent.change(screen.getByTestId("ac-label"), { target: { value: "Name" } });
    expect(screen.getByTestId("ac-field")).toHaveValue("name");
    expect(screen.getByTestId("ac-duplicate")).toBeInTheDocument();
    expect(screen.getByTestId("ac-submit")).toBeDisabled();
  });

  it("split type requires at least one option before submit (WEB_UI-054)", () => {
    renderExpanded({ sheet: "S", existingFields: [], canAdd: true, onSubmit: () => {} });
    fireEvent.change(screen.getByTestId("ac-label"), { target: { value: "Stage" } });
    fireEvent.change(screen.getByTestId("ac-type"), { target: { value: "single-select-split" } });
    expect(screen.getByTestId("ac-submit")).toBeDisabled();
    fireEvent.change(screen.getByTestId("ac-option-draft"), { target: { value: "todo" } });
    fireEvent.click(screen.getByTestId("ac-option-add"));
    expect(screen.getByTestId("ac-submit")).not.toBeDisabled();
  });

  it("submits addColumn params for the sheet owner (WEB_UI-052)", () => {
    const onSubmit = vi.fn();
    renderExpanded({ sheet: "S", existingFields: [], canAdd: true, onSubmit });
    fireEvent.change(screen.getByTestId("ac-label"), { target: { value: "Priority" } });
    fireEvent.change(screen.getByTestId("ac-type"), { target: { value: "number" } });
    fireEvent.change(screen.getByTestId("ac-owner"), { target: { value: "C" } });
    expect(screen.getByTestId("ac-field")).toHaveValue("priority");
    fireEvent.click(screen.getByTestId("ac-submit"));
    expect(onSubmit).toHaveBeenCalledWith({
      sheet: "S",
      field: "priority",
      label: "Priority",
      type: "number",
      column_owner: "C",
    });
  });

  it("non-owner sees the form in suggest mode (WEB_UI-051/-055)", () => {
    renderExpanded({ sheet: "S", existingFields: [], canAdd: false, onSubmit: () => {} });
    expect(screen.getByTestId("add-column-form")).toHaveAttribute("data-mode", "suggest");
    expect(screen.getByTestId("ac-submit")).toHaveTextContent("Suggest column");
  });

  it("labels every control (a11y / cross-surface consistency)", () => {
    renderExpanded({ sheet: "S", existingFields: [], canAdd: true, onSubmit: () => {} });
    // Visible <label> text wired to each control.
    expect(screen.getByText("Field key")).toBeInTheDocument();
    expect(screen.getByText("Label")).toBeInTheDocument();
    expect(screen.getByText("Type")).toBeInTheDocument();
    expect(screen.getByText("Column owner")).toBeInTheDocument();
    // Each control resolves by its accessible name.
    expect(screen.getByLabelText("Field key")).toBe(screen.getByTestId("ac-field"));
    expect(screen.getByLabelText("Label")).toBe(screen.getByTestId("ac-label"));
    expect(screen.getByLabelText("Type")).toBe(screen.getByTestId("ac-type"));
    expect(screen.getByLabelText("Column owner")).toBe(screen.getByTestId("ac-owner"));
  });

  it("renders the suggest eyebrow only when !canAdd", () => {
    renderExpanded({ sheet: "S", existingFields: [], canAdd: true, onSubmit: () => {} });
    expect(screen.queryByTestId("ac-suggest-eyebrow")).toBeNull();
    cleanup();
    renderExpanded({ sheet: "S", existingFields: [], canAdd: false, onSubmit: () => {} });
    expect(screen.getByTestId("ac-suggest-eyebrow")).toBeInTheDocument();
    expect(screen.getByTestId("ac-suggest-eyebrow")).toHaveTextContent(
      "Routes to the sheet owner for approval",
    );
  });

  it("split options render as a dedicated full-width row (reflow stability)", () => {
    renderExpanded({ sheet: "S", existingFields: [], canAdd: true, onSubmit: () => {} });
    fireEvent.change(screen.getByTestId("ac-type"), { target: { value: "single-select-split" } });
    const options = screen.getByTestId("ac-options");
    expect(options.className).toContain("arbor-ac-options-row");
    // Submit stays a trailing item pinned after the options row in DOM order.
    const form = screen.getByTestId("add-column-form");
    const kids = Array.from(form.children);
    expect(kids.indexOf(options)).toBeLessThan(kids.indexOf(screen.getByTestId("ac-submit")));
  });

  it("clears state + collapses after a successful submit (no duplicate suggestions)", () => {
    const onSubmit = vi.fn();
    renderExpanded({ sheet: "S", existingFields: [], canAdd: true, onSubmit });
    fireEvent.change(screen.getByTestId("ac-label"), { target: { value: "Priority" } });
    fireEvent.change(screen.getByTestId("ac-type"), { target: { value: "number" } });
    fireEvent.change(screen.getByTestId("ac-owner"), { target: { value: "C" } });
    fireEvent.click(screen.getByTestId("ac-submit"));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    // Form collapses back to the toggle on success.
    expect(screen.queryByTestId("add-column-form")).toBeNull();
    expect(screen.getByTestId("add-column-toggle")).toBeInTheDocument();
    // Re-expanding shows a fresh form (state reset, keyDirty cleared so slug syncs).
    fireEvent.click(screen.getByTestId("add-column-toggle"));
    expect(screen.getByTestId("ac-field")).toHaveValue("");
    expect(screen.getByTestId("ac-label")).toHaveValue("");
    expect(screen.getByTestId("ac-owner")).toHaveValue("");
    expect(screen.getByTestId("ac-type")).toHaveValue("text");
    expect(screen.getByTestId("ac-submit")).toBeDisabled();
    fireEvent.change(screen.getByTestId("ac-label"), { target: { value: "Stage" } });
    expect(screen.getByTestId("ac-field")).toHaveValue("stage");
  });
});

const budget: SnapshotColumn = {
  name: "col:budget",
  field: "budget",
  label: "Budget",
  type: "number",
  is_label: false,
  column_owner: "C",
  editors: [],
  can_edit: true,
  width: 120,
};

const label: SnapshotColumn = { ...budget, name: "col:name", field: "name", is_label: true };

describe("ColumnSettings", () => {
  it("owner saves a config patch via updateColumn (WEB_UI-056)", () => {
    const onUpdate = vi.fn();
    render(
      <ColumnSettings sheet="S" column={budget} canConfigure canGrant onUpdate={onUpdate} onDelete={() => {}} onGrant={() => {}} />,
    );
    fireEvent.change(screen.getByTestId("cs-label"), { target: { value: "Budget ($)" } });
    fireEvent.click(screen.getByTestId("cs-save"));
    expect(onUpdate).toHaveBeenCalledWith({
      sheet: "S",
      column: "col:budget",
      patch: { label: "Budget ($)", width: 120 },
    });
  });

  it("delete requires a confirm step then dispatches deleteColumn (WEB_UI-058)", () => {
    const onDelete = vi.fn();
    render(
      <ColumnSettings sheet="S" column={budget} canConfigure canGrant onUpdate={() => {}} onDelete={onDelete} onGrant={() => {}} />,
    );
    fireEvent.click(screen.getByTestId("cs-delete"));
    fireEvent.click(screen.getByTestId("cs-delete-confirm"));
    expect(onDelete).toHaveBeenCalledWith({ sheet: "S", column: "col:budget" });
  });

  it("blocks deleting the is_label column (WEB_UI-059)", () => {
    render(
      <ColumnSettings sheet="S" column={label} canConfigure canGrant onUpdate={() => {}} onDelete={() => {}} onGrant={() => {}} />,
    );
    expect(screen.getByTestId("cs-label-guard")).toBeInTheDocument();
    expect(screen.queryByTestId("cs-delete")).toBeNull();
  });

  it("grantColumn ownership section hidden when viewer cannot grant (WEB_UI-061)", () => {
    render(
      <ColumnSettings sheet="S" column={budget} canConfigure={false} canGrant={false} onUpdate={() => {}} onDelete={() => {}} onGrant={() => {}} />,
    );
    expect(screen.queryByTestId("cs-ownership")).toBeNull();
  });

  it("grantColumn dispatches owner+editors (WEB_UI-060)", () => {
    const onGrant = vi.fn();
    render(
      <ColumnSettings sheet="S" column={{ ...budget, name: "col:status" }} canConfigure canGrant onUpdate={() => {}} onDelete={() => {}} onGrant={onGrant} />,
    );
    fireEvent.change(screen.getByTestId("cs-editor-draft"), { target: { value: "F" } });
    fireEvent.click(screen.getByTestId("cs-editor-add"));
    fireEvent.click(screen.getByTestId("cs-grant-save"));
    expect(onGrant).toHaveBeenCalledWith({
      sheet: "S",
      column: "col:status",
      column_owner: "C",
      editors: ["F"],
    });
  });

  it("non-owner sees the owned-by caption + every action flipped to suggest", () => {
    render(
      <ColumnSettings sheet="S" column={budget} canConfigure={false} canGrant onUpdate={() => {}} onDelete={() => {}} onGrant={() => {}} />,
    );
    // (a) header caption naming the owner.
    const caption = screen.getByTestId("cs-owned-by");
    expect(caption).toHaveTextContent("Owned by C");
    expect(caption).toHaveTextContent("changes are suggested for approval");
    // (b) grant + delete trigger carry the same suggest flip as Save.
    expect(screen.getByTestId("cs-save")).toHaveAttribute("data-mode", "suggest");
    expect(screen.getByTestId("cs-grant-save")).toHaveAttribute("data-mode", "suggest");
    expect(screen.getByTestId("cs-grant-save")).toHaveTextContent("Suggest editor change");
    expect(screen.getByTestId("cs-delete")).toHaveAttribute("data-mode", "suggest");
  });

  it("owner keeps direct-mode actions + no owned-by caption", () => {
    render(
      <ColumnSettings sheet="S" column={budget} canConfigure canGrant onUpdate={() => {}} onDelete={() => {}} onGrant={() => {}} />,
    );
    expect(screen.queryByTestId("cs-owned-by")).toBeNull();
    expect(screen.getByTestId("cs-save")).toHaveAttribute("data-mode", "direct");
    expect(screen.getByTestId("cs-grant-save")).toHaveAttribute("data-mode", "direct");
    expect(screen.getByTestId("cs-grant-save")).toHaveTextContent("Update editors");
    expect(screen.getByTestId("cs-delete")).toHaveAttribute("data-mode", "direct");
  });
});
