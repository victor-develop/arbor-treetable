import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AddColumnForm, ColumnSettings } from "./ColumnConfig";
import type { SnapshotColumn } from "../api";

describe("AddColumnForm", () => {
  it("offers exactly the allowed type enum (WEB_UI-053)", () => {
    render(<AddColumnForm sheet="S" existingFields={[]} canAdd onSubmit={() => {}} />);
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
    render(<AddColumnForm sheet="S" existingFields={["name"]} canAdd onSubmit={onSubmit} />);
    fireEvent.change(screen.getByTestId("ac-field"), { target: { value: "name" } });
    fireEvent.change(screen.getByTestId("ac-label"), { target: { value: "Name" } });
    expect(screen.getByTestId("ac-duplicate")).toBeInTheDocument();
    expect(screen.getByTestId("ac-submit")).toBeDisabled();
  });

  it("split type requires at least one option before submit (WEB_UI-054)", () => {
    render(<AddColumnForm sheet="S" existingFields={[]} canAdd onSubmit={() => {}} />);
    fireEvent.change(screen.getByTestId("ac-field"), { target: { value: "stage" } });
    fireEvent.change(screen.getByTestId("ac-label"), { target: { value: "Stage" } });
    fireEvent.change(screen.getByTestId("ac-type"), { target: { value: "single-select-split" } });
    expect(screen.getByTestId("ac-submit")).toBeDisabled();
    fireEvent.change(screen.getByTestId("ac-option-draft"), { target: { value: "todo" } });
    fireEvent.click(screen.getByTestId("ac-option-add"));
    expect(screen.getByTestId("ac-submit")).not.toBeDisabled();
  });

  it("submits addColumn params for the sheet owner (WEB_UI-052)", () => {
    const onSubmit = vi.fn();
    render(<AddColumnForm sheet="S" existingFields={[]} canAdd onSubmit={onSubmit} />);
    fireEvent.change(screen.getByTestId("ac-field"), { target: { value: "priority" } });
    fireEvent.change(screen.getByTestId("ac-label"), { target: { value: "Priority" } });
    fireEvent.change(screen.getByTestId("ac-type"), { target: { value: "number" } });
    fireEvent.change(screen.getByTestId("ac-owner"), { target: { value: "C" } });
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
    render(<AddColumnForm sheet="S" existingFields={[]} canAdd={false} onSubmit={() => {}} />);
    expect(screen.getByTestId("add-column-form")).toHaveAttribute("data-mode", "suggest");
    expect(screen.getByTestId("ac-submit")).toHaveTextContent("Suggest column");
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
});
