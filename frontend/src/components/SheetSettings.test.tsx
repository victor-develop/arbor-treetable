// SheetSettings — the ONE unified sheet-config surface (consolidation of the
// previously scattered Process modal, Webhooks modal, per-column gear, and toolbar
// Add-column). These specs prove it seeds every tab from a SINGLE
// getSheetDefinition read (the SAME capability the LLM agent uses), tabs across
// Columns / Process / Webhooks, expands a column's editor, and hides the
// owner-only tabs for a non-owner.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ArborClient, SheetDefinition } from "../api";
import { mockClient } from "../test/fixture";
import { SheetSettings } from "./SheetSettings";

const DEF: SheetDefinition = {
  sheet: {
    name: "S",
    title: "Sheet S",
    structural_owner: "A",
    label_column: "col:name",
    settings: {},
  },
  columns: [
    {
      name: "col:name",
      field: "name",
      label: "Name",
      type: "text",
      column_owner: "B",
      editors: [],
      is_label: true,
      can_edit: false,
    },
    {
      name: "col:status",
      field: "status",
      label: "Status",
      type: "single-select-split",
      column_owner: "C",
      editors: ["B"],
      is_label: false,
      can_edit: true,
      options: { groups: [{ label: "Stage", options: ["todo", "done"] }] },
    },
  ],
  process: null,
};

function clientWithDef(def: SheetDefinition = DEF): {
  client: ArborClient;
  defCalls: string[];
} {
  const base = mockClient();
  const defCalls: string[] = [];
  const client: ArborClient = {
    ...base.client,
    getSheetDefinition: async (sheet) => {
      defCalls.push(sheet);
      return def;
    },
    // webhook list so the Webhooks tab mounts cleanly
    listWebhooks: async () => [],
  };
  return { client, defCalls };
}

function renderSettings(overrides?: Partial<Parameters<typeof SheetSettings>[0]>) {
  const { client, defCalls } = clientWithDef();
  const props = {
    sheet: "S",
    client,
    canConfigProcess: true,
    onClose: vi.fn(),
    onDefineProcess: vi.fn(),
    onEnableProcess: vi.fn(),
    onDisableProcess: vi.fn(),
    onAddColumn: vi.fn(),
    onUpdateColumn: vi.fn(),
    onDeleteColumn: vi.fn(),
    onGrantColumn: vi.fn(),
    ...overrides,
  };
  render(<SheetSettings {...props} />);
  return { props, defCalls };
}

describe("SheetSettings", () => {
  it("seeds from a single getSheetDefinition read (not a snapshot)", async () => {
    const { defCalls } = renderSettings();
    await waitFor(() => expect(screen.getByTestId("settings-columns")).toBeInTheDocument());
    expect(defCalls).toEqual(["S"]); // exactly one governance read
  });

  it("lists the definition columns with owner + label badge", async () => {
    renderSettings();
    await waitFor(() => screen.getByTestId("settings-column-col:name"));
    const nameRow = screen.getByTestId("settings-column-col:name");
    expect(within(nameRow).getByText("Name")).toBeInTheDocument();
    expect(within(nameRow).getByText("label")).toBeInTheDocument(); // is_label badge
    expect(screen.getByTestId("settings-column-col:status")).toBeInTheDocument();
  });

  it("expands a column into its ColumnSettings editor", async () => {
    renderSettings();
    await waitFor(() => screen.getByTestId("settings-column-toggle-col:status"));
    fireEvent.click(screen.getByTestId("settings-column-toggle-col:status"));
    expect(screen.getByTestId("col-settings-col:status")).toBeInTheDocument();
  });

  it("labels the tabs Columns / Flow / Delivery", async () => {
    renderSettings();
    await waitFor(() => screen.getByTestId("settings-tabs"));
    expect(screen.getByTestId("settings-tab-columns")).toHaveTextContent("Columns");
    expect(screen.getByTestId("settings-tab-process")).toHaveTextContent("Flow");
    expect(screen.getByTestId("settings-tab-webhooks")).toHaveTextContent("Delivery");
  });

  it("Flow embeds the ProcessConfigPanel; Delivery embeds the WebhookPanel", async () => {
    renderSettings();
    await waitFor(() => screen.getByTestId("settings-tabs"));
    fireEvent.click(screen.getByTestId("settings-tab-process"));
    expect(screen.getByTestId("settings-process")).toBeInTheDocument();
    // the ProcessConfigPanel (the DAG canvas) mounts unchanged inside the tab
    expect(within(screen.getByTestId("settings-process")).getByTestId("process-config")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("settings-tab-webhooks"));
    expect(screen.getByTestId("settings-webhooks")).toBeInTheDocument();
    // the WebhookPanel (register form + list) mounts embedded inside the tab
    expect(within(screen.getByTestId("settings-webhooks")).getByTestId("webhook-list")).toBeInTheDocument();
  });

  it("hides the owner-only tabs for a non-owner (Columns only)", async () => {
    renderSettings({ canConfigProcess: false });
    await waitFor(() => screen.getByTestId("settings-tabs"));
    expect(screen.getByTestId("settings-tab-columns")).toBeInTheDocument();
    expect(screen.queryByTestId("settings-tab-process")).toBeNull();
    expect(screen.queryByTestId("settings-tab-webhooks")).toBeNull();
  });

  it("closes via the header ✕", async () => {
    const { props } = renderSettings();
    await waitFor(() => screen.getByTestId("settings-close"));
    fireEvent.click(screen.getByTestId("settings-close"));
    expect(props.onClose).toHaveBeenCalled();
  });
});
