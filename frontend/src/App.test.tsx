import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "./App";
import type { Snapshot } from "./api";

const snapshot: Snapshot = {
  sheet: { name: "S", structural_owner: "A", settings: {} },
  columns: [],
  nodes: [
    { name: "R", parent: null, lft: 1, rgt: 2, label: "Root", values: {}, can_change_structure: false },
  ],
  label_column: "col:name",
  actor: "A",
};

describe("App thin shell", () => {
  it("renders the sheet name from the snapshot", () => {
    render(<App snapshot={snapshot} />);
    expect(screen.getByTestId("sheet-name")).toHaveTextContent("Sheet: S");
    expect(screen.getByTestId("node-count")).toHaveTextContent("1 nodes");
  });

  it("renders without a snapshot", () => {
    render(<App />);
    expect(screen.getByText("No snapshot loaded.")).toBeInTheDocument();
  });
});
