// Unit spec for ProcessConfigPanel (Feature: process). The panel is the
// structural-owner-only stage editor: define the ordered column stages
// (add / remove / reorder), set a per-stage SLA (seconds), and enable / disable
// the process. It is a THIN presentational shell — it owns local edit state but
// re-derives no authority (the host gates mount on the structural-owner hint) and
// funnels every write through the onDefine / onEnable / onDisable callbacks
// (which the host wires to client.defineProcess / enableProcess / disableProcess).
//
// These specs assert: the columns picker offers only sheet columns not already
// staged; add appends a stage; remove drops one; reorder (move up / move down)
// swaps adjacent stages and keeps the SLA attached to its stage; the per-stage
// SLA input edits the right stage; Define fires onDefine with the ordered stage
// payload; Enable / Disable fire their callbacks; hydrates from an existing def.

import { act, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProcessConfigPanel } from "./ProcessConfigPanel";
import type { ProcessDef, SnapshotColumn } from "../api";

function col(name: string, over: Partial<SnapshotColumn> = {}): SnapshotColumn {
  return {
    name,
    field: name,
    label: name.toUpperCase(),
    type: "text",
    is_label: false,
    column_owner: "owner",
    editors: [],
    can_edit: true,
    ...over,
  };
}

const COLS = [col("owner_c"), col("budget"), col("approval")];

function def(over: Partial<ProcessDef> = {}): ProcessDef {
  return {
    sheet: "S",
    title: "Fill order",
    enabled: false,
    row_scope: "root-children",
    start_trigger: "node-created",
    stages: [
      { idx: 0, column: "owner_c", label: "OWNER_C", sla_seconds: 0 },
      { idx: 1, column: "budget", label: "BUDGET", sla_seconds: 3600 },
    ],
    ...over,
  };
}

function renderPanel(overProps: Partial<React.ComponentProps<typeof ProcessConfigPanel>> = {}) {
  const onDefine = vi.fn();
  const onEnable = vi.fn();
  const onDisable = vi.fn();
  render(
    <ProcessConfigPanel
      sheet="S"
      columns={COLS}
      process={null}
      onDefine={onDefine}
      onEnable={onEnable}
      onDisable={onDisable}
      {...overProps}
    />,
  );
  return { onDefine, onEnable, onDisable };
}

describe("ProcessConfigPanel — stage editing", () => {
  it("starts empty (no existing process) and the column picker offers all sheet columns", () => {
    renderPanel();
    expect(screen.queryAllByTestId(/^pc-stage-/)).toHaveLength(0);
    const picker = screen.getByTestId("pc-add-column") as HTMLSelectElement;
    const opts = within(picker).getAllByRole("option").map((o) => (o as HTMLOptionElement).value);
    // The leading placeholder + the three sheet columns.
    expect(opts).toContain("owner_c");
    expect(opts).toContain("budget");
    expect(opts).toContain("approval");
  });

  it("adds a stage from the picker and drops it from the remaining choices", async () => {
    renderPanel();
    const picker = screen.getByTestId("pc-add-column") as HTMLSelectElement;
    await act(async () => {
      picker.value = "budget";
      picker.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      screen.getByTestId("pc-add-stage").click();
    });
    expect(screen.getAllByTestId(/^pc-stage-\d+$/)).toHaveLength(1);
    expect(screen.getByTestId("pc-stage-0")).toHaveTextContent("BUDGET");
    // budget is now staged → no longer offered.
    const opts = within(picker).getAllByRole("option").map((o) => (o as HTMLOptionElement).value);
    expect(opts).not.toContain("budget");
    expect(opts).toContain("owner_c");
  });

  it("removes a stage", async () => {
    renderPanel({ process: def() });
    expect(screen.getAllByTestId(/^pc-stage-\d+$/)).toHaveLength(2);
    await act(async () => {
      screen.getByTestId("pc-stage-remove-0").click();
    });
    const stages = screen.getAllByTestId(/^pc-stage-\d+$/);
    expect(stages).toHaveLength(1);
    // The surviving stage is the old #1 (budget), re-indexed to 0.
    expect(screen.getByTestId("pc-stage-0")).toHaveTextContent("BUDGET");
  });

  it("reorders stages (move up) and keeps each SLA attached to its stage", async () => {
    renderPanel({ process: def() });
    // owner_c (sla 0) at idx0, budget (sla 3600) at idx1. Move budget up.
    await act(async () => {
      screen.getByTestId("pc-stage-up-1").click();
    });
    expect(screen.getByTestId("pc-stage-0")).toHaveTextContent("BUDGET");
    expect(screen.getByTestId("pc-stage-1")).toHaveTextContent("OWNER_C");
    // SLA followed the stage: budget's 3600 is now on stage 0.
    expect((screen.getByTestId("pc-stage-sla-0") as HTMLInputElement).value).toBe("3600");
    expect((screen.getByTestId("pc-stage-sla-1") as HTMLInputElement).value).toBe("0");
  });

  it("move-down on the last stage is a no-op (buttons bound the ends)", async () => {
    renderPanel({ process: def() });
    // The last stage has no move-down button (idx1 of 2).
    expect(screen.queryByTestId("pc-stage-down-1")).toBeNull();
    // The first stage has no move-up button.
    expect(screen.queryByTestId("pc-stage-up-0")).toBeNull();
  });

  it("edits a per-stage SLA", async () => {
    renderPanel({ process: def() });
    const sla = screen.getByTestId("pc-stage-sla-0") as HTMLInputElement;
    await act(async () => {
      sla.value = "7200";
      sla.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect((screen.getByTestId("pc-stage-sla-0") as HTMLInputElement).value).toBe("7200");
  });
});

describe("ProcessConfigPanel — callbacks", () => {
  it("Define fires onDefine with the ordered stage payload (column + sla_seconds)", async () => {
    const { onDefine } = renderPanel({ process: def() });
    await act(async () => {
      screen.getByTestId("pc-define").click();
    });
    expect(onDefine).toHaveBeenCalledTimes(1);
    const [stages, opts] = onDefine.mock.calls[0];
    expect(stages).toEqual([
      { column: "owner_c", sla_seconds: 0 },
      { column: "budget", sla_seconds: 3600 },
    ]);
    // Title threads through when present.
    expect(opts).toMatchObject({ title: "Fill order" });
  });

  it("Define is disabled with zero stages", () => {
    renderPanel();
    expect(screen.getByTestId("pc-define")).toBeDisabled();
  });

  it("shows Enable when the process is defined but disabled, and fires onEnable", async () => {
    const { onEnable } = renderPanel({ process: def({ enabled: false }) });
    const enable = screen.getByTestId("pc-enable");
    expect(enable).toBeInTheDocument();
    expect(screen.queryByTestId("pc-disable")).toBeNull();
    await act(async () => {
      enable.click();
    });
    expect(onEnable).toHaveBeenCalledTimes(1);
  });

  it("shows Disable when the process is enabled, and fires onDisable", async () => {
    const { onDisable } = renderPanel({ process: def({ enabled: true }) });
    const disable = screen.getByTestId("pc-disable");
    expect(disable).toBeInTheDocument();
    expect(screen.queryByTestId("pc-enable")).toBeNull();
    await act(async () => {
      disable.click();
    });
    expect(onDisable).toHaveBeenCalledTimes(1);
  });

  it("hydrates the editor from an existing process definition", () => {
    renderPanel({ process: def() });
    expect(screen.getByTestId("pc-stage-0")).toHaveTextContent("OWNER_C");
    expect(screen.getByTestId("pc-stage-1")).toHaveTextContent("BUDGET");
    expect((screen.getByTestId("pc-stage-sla-1") as HTMLInputElement).value).toBe("3600");
  });
});
