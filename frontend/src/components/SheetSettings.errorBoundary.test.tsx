// Regression: a render crash inside the Flow tab's ProcessConfigPanel (the DAG
// canvas — the panel that white-screened before the data guards + boundary
// landed) must be contained by the per-tab "settings-flow" ErrorBoundary, leaving
// the Settings tab strip usable rather than blanking the whole modal / app.
//
// The ProcessConfigPanel is module-mocked to always throw ONLY in this dedicated
// file, so the sibling SheetSettings.test.tsx (which asserts the real panel
// mounts) is unaffected.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ArborClient, SheetDefinition } from "../api";
import { mockClient } from "../test/fixture";
import { SheetSettings } from "./SheetSettings";

vi.mock("./ProcessConfigPanel", () => ({
  ProcessConfigPanel: () => {
    throw new Error("boom: Flow canvas render crash");
  },
}));

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
  ],
  process: null,
};

function renderSettings() {
  const base = mockClient();
  const client: ArborClient = {
    ...base.client,
    getSheetDefinition: async () => DEF,
    listWebhooks: async () => [],
  };
  render(
    <SheetSettings
      sheet="S"
      client={client}
      canConfigProcess
      onClose={vi.fn()}
      onDefineProcess={vi.fn()}
      onEnableProcess={vi.fn()}
      onDisableProcess={vi.fn()}
      onAddColumn={vi.fn()}
      onUpdateColumn={vi.fn()}
      onDeleteColumn={vi.fn()}
      onGrantColumn={vi.fn()}
    />,
  );
}

describe("SheetSettings Flow-tab error boundary", () => {
  beforeEach(() => {
    // The thrown render error logs via console.error (ErrorBoundary + React) —
    // expected noise; suppress it so the run stays clean.
    vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("contains a Flow-canvas crash without blanking the modal", async () => {
    renderSettings();
    await waitFor(() => screen.getByTestId("settings-tabs"));
    fireEvent.click(screen.getByTestId("settings-tab-process"));
    // The boundary's fallback shows...
    expect(screen.getByTestId("error-boundary-settings-flow")).toBeInTheDocument();
    // ...and the tab strip (a sibling of the crashed panel) still renders, so the
    // user can switch tabs / close — the whole app did NOT white-screen.
    expect(screen.getByTestId("settings-tabs")).toBeInTheDocument();
  });
});
