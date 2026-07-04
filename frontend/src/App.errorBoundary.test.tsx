import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// These tests force a wrapped child to throw during render and assert the
// matching <ErrorBoundary label="…"> fallback contains the blast radius — the
// crash degrades to the "error-boundary-<label>" card while a SIBLING region of
// the shell keeps rendering. This is the regression guard for the white-screen
// crash the boundaries were added to prevent.
//
// We replace the real component modules with throwing stand-ins via vi.mock
// (hoisted). Each mock is toggled by a module-level flag so the mock is only
// explosive in the test that wants it — the shell's other regions render for
// real, proving containment.

let treeTableShouldThrow = false;
let sheetSettingsShouldThrow = false;

vi.mock("./components/TreeTable", () => ({
  TreeTable: () => {
    if (treeTableShouldThrow) throw new Error("boom: TreeTable render");
    // A benign stand-in carrying the same testid the shell asserts on, so the
    // non-throwing tests (and the sheet-settings test) still see the grid.
    return <div data-testid="tree-table">tree</div>;
  },
}));

vi.mock("./components/SheetSettings", () => ({
  SheetSettings: () => {
    if (sheetSettingsShouldThrow) throw new Error("boom: SheetSettings render");
    return <div data-testid="sheet-settings">settings</div>;
  },
}));

// Import App AFTER the mocks are registered (vi.mock is hoisted, but keep the
// import here for clarity of ordering with the fixtures).
import App from "./App";
import { loginAs, mockClient } from "./test/fixture";

describe("App — ErrorBoundary containment", () => {
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    treeTableShouldThrow = false;
    sheetSettingsShouldThrow = false;
    // A render-phase throw is expected; React (and our boundary) log it. Suppress
    // the noise so the run stays clean, but keep it a spy so we could assert on it.
    errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    errorSpy.mockRestore();
  });

  it("tree-table crash is caught by its boundary while the toolbar/header survive", async () => {
    treeTableShouldThrow = true;
    const { client } = mockClient({ snapshot: loginAs("B") });
    render(<App client={client} sheetName="S" />);

    // The tree-table boundary fell back instead of tearing down #root.
    const fallback = await screen.findByTestId("error-boundary-tree-table");
    expect(fallback).toHaveTextContent("This panel hit an error.");
    expect(screen.getByTestId("error-boundary-tree-table-reset")).toBeInTheDocument();
    // The real grid never mounted.
    expect(screen.queryByText("tree")).not.toBeInTheDocument();

    // SIBLINGS survive: the header (sheet name) + the toolbar controls still render.
    expect(screen.getByTestId("sheet-name")).toHaveTextContent("Sheet: S");
    expect(screen.getByTestId("density-toggle")).toBeInTheDocument();
    expect(screen.getByTestId("data-disclosure")).toBeInTheDocument();
  });

  it("sheet-settings crash is caught by its boundary while the grid + header survive", async () => {
    sheetSettingsShouldThrow = true;
    // Persona A is the structural_owner of sheet S (fixture), so canConfigProcess
    // is true and the Settings launcher renders.
    const { client } = mockClient({ snapshot: loginAs("A") });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    // Open the Sheet Settings modal — its body throws on render.
    fireEvent.click(screen.getByTestId("sheet-settings-button"));

    const fallback = await screen.findByTestId("error-boundary-sheet-settings");
    expect(fallback).toHaveTextContent("This panel hit an error.");
    expect(screen.getByTestId("error-boundary-sheet-settings-reset")).toBeInTheDocument();

    // SIBLINGS survive: the grid (a sibling of the modal) + the header stayed up
    // instead of the whole app white-screening.
    expect(screen.getByTestId("tree-table")).toBeInTheDocument();
    expect(screen.getByTestId("sheet-name")).toHaveTextContent("Sheet: S");
  });

  it("no crash → boundaries are transparent (children render as usual)", async () => {
    const { client } = mockClient({ snapshot: loginAs("B") });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    // No fallback card is present when nothing throws.
    expect(screen.queryByTestId("error-boundary-tree-table")).not.toBeInTheDocument();
  });
});
