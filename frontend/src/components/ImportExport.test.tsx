import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ImportExport } from "./ImportExport";
import { exportSnapshot } from "../lib/io";
import { loginAs } from "../test/fixture";

describe("ImportExport", () => {
  it("export hands the serialized snapshot to the host (WEB_UI-074)", () => {
    const onExport = vi.fn();
    const snap = loginAs("A");
    render(<ImportExport snapshot={snap} targetSheet="S" onExport={onExport} onConfirmImport={() => {}} />);
    fireEvent.click(screen.getByTestId("export-btn"));
    expect(onExport).toHaveBeenCalledWith(exportSnapshot(snap));
  });

  it("malformed file shows an error and no preview/confirm (WEB_UI-079)", () => {
    const onConfirm = vi.fn();
    render(<ImportExport snapshot={null} targetSheet="S2" onConfirmImport={onConfirm} />);
    fireEvent.change(screen.getByTestId("import-text"), { target: { value: "{bad" } });
    expect(screen.getByTestId("import-error")).toBeInTheDocument();
    expect(screen.queryByTestId("import-confirm")).toBeNull();
  });

  it("valid file previews a plan; no executeAction until confirm (WEB_UI-076/-077)", () => {
    const onConfirm = vi.fn();
    render(<ImportExport snapshot={null} targetSheet="S2" onConfirmImport={onConfirm} />);
    fireEvent.change(screen.getByTestId("import-text"), {
      target: { value: exportSnapshot(loginAs("A")) },
    });
    expect(screen.getByTestId("import-preview")).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("import-confirm"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    const steps = onConfirm.mock.calls[0][0];
    expect(steps.some((s: { action: string }) => s.action === "addColumn")).toBe(true);
    expect(steps.some((s: { action: string }) => s.action === "addNode")).toBe(true);
  });
});
