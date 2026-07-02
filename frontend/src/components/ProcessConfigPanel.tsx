// Process config panel (Feature: process — DAG rule model). Structural-owner-only
// surface — the HOST gates mount on the structural-owner hint
// (snapshot.viewer.can_add_column); this shell re-derives no authority. It owns
// the local draft rule set + funnels every write through onDefine / onEnable /
// onDisable, which the host wires to client.defineProcess / enableProcess /
// disableProcess (the same executeAction funnel as every other mutation).
//
// A "process" is a SET of trigger->expectation rules forming a DAG: "On <trigger>:
// expect (colA [and colB…]) filled within <within_seconds>". The body is the
// bespoke SVG/DOM canvas (ProcessCanvas) — a fixed "Row created / updated" START
// node + one node per participating column; edges are expectations carrying a
// within-duration. The canvas is a VIEW/PRODUCER over `rules[]`: `defineProcess
// (rules[])` is the SAME payload the LLM emits. Save is blocked while the client
// graph validation (mirroring the server) reports a hard error (cycle / self-loop
// / duplicate).

import { useMemo, useState } from "react";
import type { ProcessDef, ProcessRuleInput, SnapshotColumn } from "../api";
import { ProcessCanvas } from "./process/ProcessCanvas";
import { buildGraph, validate } from "./process/graph";

// Project the read-shape rule DAG (ProcessRuleView[]) to the write-shape draft
// (ProcessRuleInput[]) the canvas edits. Presentation-only fields (idx, labels,
// owners) are dropped; the stable rule_key is kept so an edit re-uses it.
function seed(process: ProcessDef | null): ProcessRuleInput[] {
  if (!process) return [];
  return process.rules.map((r) => ({
    rule_key: r.rule_key,
    trigger_kind: r.trigger_kind,
    trigger_column: r.trigger_column,
    trigger_op: r.trigger_op,
    expected_columns: [...r.expected_columns],
    within_seconds: r.within_seconds,
    notify_on_expect: r.notify_on_expect,
    ...(r.label ? { label: r.label } : {}),
  }));
}

export function ProcessConfigPanel({
  sheet,
  columns,
  process,
  onDefine,
  onEnable,
  onDisable,
}: {
  sheet: string;
  columns: SnapshotColumn[];
  // The existing process definition (hydrate the editor), or null for a fresh one.
  process: ProcessDef | null;
  // Fires with the rule DAG payload + optional {title,row_scope}.
  onDefine: (
    rules: ProcessRuleInput[],
    opts?: { title?: string; row_scope?: string },
  ) => void;
  onEnable: () => void;
  onDisable: () => void;
}): JSX.Element {
  const [rules, setRules] = useState<ProcessRuleInput[]>(() => seed(process));

  // The label column is never a node/trigger: it is set by the row creator and is
  // always filled, so a label expectation would auto-satisfy instantly. Exclude it
  // from the columns the canvas offers (the canvas also guards on is_label).
  const canvasColumns = useMemo(
    () => columns.filter((c) => !c.is_label),
    [columns],
  );

  // Mirror the server validate_rules on the client so Save is blocked before the
  // round-trip when the graph has a hard error (cycle / self-loop / duplicate).
  const labelOf = useMemo(() => {
    const map = new Map(columns.map((c) => [c.name, c.label]));
    return (col: string) => map.get(col) ?? null;
  }, [columns]);
  const validation = useMemo(
    () => validate(buildGraph(rules, labelOf)),
    [rules, labelOf],
  );
  const hasError = validation.errors.length > 0;

  const define = () => {
    if (rules.length === 0 || hasError) return;
    onDefine(rules, process?.title ? { title: process.title } : undefined);
  };

  return (
    <section className="arbor-process-config" data-testid="process-config" data-sheet={sheet}>
      {/* The modal chrome already renders a "Process" title bar + ✕, so this
          header carries only the status pill (not a duplicate <h2>). */}
      <header className="arbor-pc-header">
        <span className="arbor-pc-header-label">Flow</span>
        {process?.enabled ? (
          <span className="arbor-pc-state is-enabled" data-testid="pc-state">
            Enabled
          </span>
        ) : process ? (
          <span className="arbor-pc-state is-disabled" data-testid="pc-state">
            Disabled
          </span>
        ) : null}
      </header>

      {/* One-line mental-model explainer: a rule is "on a trigger, expect columns
          filled within a window"; a filled column (a default counts) satisfies its
          expectation and can itself trigger the next rule — that's the DAG. */}
      <p className="arbor-pc-hint" data-testid="pc-hint">
        Draw the flow: on a trigger (row created, or a column filled), expect the
        connected columns to be filled within their window. A column counts as
        filled when it gets a value (a default counts), which can trigger the next
        step.
      </p>

      <ProcessCanvas columns={canvasColumns} rules={rules} onChange={setRules} />

      <footer className="arbor-pc-footer">
        {process &&
          (process.enabled ? (
            <button
              type="button"
              className="arbor-pc-toggle"
              data-testid="pc-disable"
              onClick={onDisable}
            >
              Disable
            </button>
          ) : (
            <button
              type="button"
              className="arbor-pc-toggle"
              data-testid="pc-enable"
              onClick={onEnable}
            >
              Enable
            </button>
          ))}
        <button
          type="button"
          className="arbor-pc-save"
          data-testid="pc-define"
          disabled={rules.length === 0 || hasError}
          title={hasError ? "Fix the flow errors before saving." : undefined}
          onClick={define}
        >
          Save process
        </button>
      </footer>
    </section>
  );
}
