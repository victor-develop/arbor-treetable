// Unit spec for ProcessConfigPanel (Feature: process — DAG rule model). The panel
// is the structural-owner-only surface hosting the bespoke DAG canvas: draw the
// trigger->expectation rules, and enable / disable the process. It is a THIN
// presentational shell — it owns the local draft rule set but re-derives no
// authority (the host gates mount on the structural-owner hint) and funnels every
// write through onDefine / onEnable / onDisable (wired to client.defineProcess /
// enableProcess / disableProcess — the SAME payload the LLM emits).
//
// These specs assert: the modal chrome (status pill + fill-rule explainer) stays;
// the canvas mounts with the label column excluded; hydrates edges from an
// existing def; Save fires onDefine with the rule payload; Save is disabled with
// zero rules AND while the graph has a hard validation error (cycle); Enable /
// Disable fire their callbacks.

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

function rule(over: Partial<ProcessDef["rules"][number]>): ProcessDef["rules"][number] {
  return {
    rule_key: "r0",
    idx: 0,
    trigger_kind: "row",
    trigger_column: null,
    trigger_column_label: null,
    trigger_op: "created-or-updated",
    expected_columns: ["owner_c"],
    expected_labels: ["OWNER_C"],
    within_seconds: 0,
    notify_on_expect: true,
    label: null,
    ...over,
  };
}

// A linear-chain DAG (row -> owner_c -> budget) — the simplest shape.
function def(over: Partial<ProcessDef> = {}): ProcessDef {
  return {
    sheet: "S",
    title: "Fill order",
    enabled: false,
    row_scope: "root-children",
    rules: [
      rule({ rule_key: "r0", idx: 0, trigger_kind: "row", trigger_column: null, expected_columns: ["owner_c"] }),
      rule({
        rule_key: "r1",
        idx: 1,
        trigger_kind: "column",
        trigger_column: "owner_c",
        trigger_column_label: "OWNER_C",
        trigger_op: "updated",
        expected_columns: ["budget"],
        expected_labels: ["BUDGET"],
        within_seconds: 3600,
      }),
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

describe("ProcessConfigPanel — chrome + canvas mount", () => {
  it("mounts the DAG canvas with the fixed START node", () => {
    renderPanel();
    expect(screen.getByTestId("process-canvas")).toBeInTheDocument();
    expect(screen.getByTestId("canvas-node-__start__")).toBeInTheDocument();
  });

  it("keeps the fill-rule explainer (trigger / within / default counts)", () => {
    renderPanel();
    const hint = screen.getByTestId("pc-hint");
    expect(hint).toHaveTextContent(/trigger/i);
    expect(hint).toHaveTextContent(/a default counts/i);
  });

  it("excludes the sheet's label/title column from the canvas picker", () => {
    const cols = [col("initiative", { is_label: true }), col("owner_c"), col("budget")];
    renderPanel({ columns: cols });
    const picker = screen.getByTestId("canvas-add-column") as HTMLSelectElement;
    const opts = within(picker).getAllByRole("option").map((o) => (o as HTMLOptionElement).value);
    expect(opts).not.toContain("initiative");
    expect(opts).toContain("owner_c");
  });

  it("hydrates the canvas edges from an existing process definition", () => {
    renderPanel({ process: def() });
    // row -> owner_c and owner_c -> budget edges render.
    expect(screen.getByTestId("canvas-edge-row-__start__-owner_c")).toBeInTheDocument();
    expect(screen.getByTestId("canvas-edge-row-owner_c-budget")).toBeInTheDocument();
    // budget's within survives hydration.
    expect((screen.getByTestId("canvas-edge-within-owner_c-budget") as HTMLInputElement).value).toBe("3600");
  });
});

describe("ProcessConfigPanel — callbacks", () => {
  it("Save fires onDefine with the projected rule DAG + threads the title", async () => {
    const { onDefine } = renderPanel({ process: def() });
    await act(async () => {
      screen.getByTestId("pc-define").click();
    });
    expect(onDefine).toHaveBeenCalledTimes(1);
    const [rules, opts] = onDefine.mock.calls[0];
    expect(rules).toEqual([
      {
        rule_key: "r0",
        trigger_kind: "row",
        trigger_column: null,
        trigger_op: "created-or-updated",
        expected_columns: ["owner_c"],
        within_seconds: 0,
        notify_on_expect: true,
      },
      {
        rule_key: "r1",
        trigger_kind: "column",
        trigger_column: "owner_c",
        trigger_op: "updated",
        expected_columns: ["budget"],
        within_seconds: 3600,
        notify_on_expect: true,
      },
    ]);
    expect(opts).toMatchObject({ title: "Fill order" });
  });

  it("Save is disabled with zero rules", () => {
    renderPanel();
    expect(screen.getByTestId("pc-define")).toBeDisabled();
  });

  it("shows Enable when defined-but-disabled and fires onEnable", async () => {
    const { onEnable } = renderPanel({ process: def({ enabled: false }) });
    const enable = screen.getByTestId("pc-enable");
    expect(enable).toBeInTheDocument();
    expect(screen.queryByTestId("pc-disable")).toBeNull();
    await act(async () => {
      enable.click();
    });
    expect(onEnable).toHaveBeenCalledTimes(1);
  });

  it("shows Disable when enabled and fires onDisable", async () => {
    const { onDisable } = renderPanel({ process: def({ enabled: true }) });
    const disable = screen.getByTestId("pc-disable");
    expect(disable).toBeInTheDocument();
    expect(screen.queryByTestId("pc-enable")).toBeNull();
    await act(async () => {
      disable.click();
    });
    expect(onDisable).toHaveBeenCalledTimes(1);
  });

  it("blocks Save (and surfaces an error) while the graph has a cycle", async () => {
    const { onDefine } = renderPanel({
      // A cyclic def a->b->a — the client mirrors the server's hard error.
      process: def({
        rules: [
          rule({ rule_key: "r0", trigger_kind: "column", trigger_column: "owner_c", expected_columns: ["budget"] }),
          rule({ rule_key: "r1", trigger_kind: "column", trigger_column: "budget", expected_columns: ["owner_c"] }),
        ],
      }),
    });
    expect(screen.getByTestId("canvas-error")).toHaveTextContent(/cycle/i);
    const save = screen.getByTestId("pc-define");
    expect(save).toBeDisabled();
    await act(async () => {
      save.click();
    });
    expect(onDefine).not.toHaveBeenCalled();
  });
});
