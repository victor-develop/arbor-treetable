// Process stage editor (Feature: process). Structural-owner-only surface — the
// HOST gates mount on the structural-owner hint (snapshot.viewer.can_add_column);
// this shell re-derives no authority. It owns the local edit state (the ordered
// stage list + per-stage SLA) and funnels every write through onDefine / onEnable
// / onDisable, which the host wires to client.defineProcess / enableProcess /
// disableProcess (the same executeAction funnel as every other mutation).
//
// A "process" is an ordered list of column stages A -> B -> C. Left-to-right order
// IS the fill order; each stage carries an SLA in seconds (0 = no SLA). The editor
// lets the owner add a column as a stage (from the not-yet-staged sheet columns),
// remove one, reorder with move-up / move-down (deterministic, no drag dependency),
// and set the SLA. Define writes the whole ordered list; Enable / Disable flip the
// process live.

import { useState } from "react";
import type { ProcessDef, ProcessStageInput, SnapshotColumn } from "../api";

// A stage in the local editor: the column name + its human label (for display) +
// the SLA seconds. Kept flat + ordered; array index IS the fill order.
type EditStage = {
  column: string;
  label: string;
  sla_seconds: number;
};

// Compact seconds -> "2d 4h" / "3h" / "45m" / "30s" rendering for the per-stage
// SLA hint, so an owner reads a real duration instead of a raw seconds count
// (172800 → "2d"). 0 (or unset) means no SLA — the caller decides whether to show.
function formatSla(seconds: number): string {
  if (!seconds || seconds <= 0) return "";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const parts: string[] = [];
  if (d) parts.push(`${d}d`);
  if (h) parts.push(`${h}h`);
  if (m) parts.push(`${m}m`);
  if (s && !d && !h) parts.push(`${s}s`); // only show seconds for sub-hour SLAs
  // Keep it to the two most-significant units so the hint stays a glance.
  return parts.slice(0, 2).join(" ");
}

// Seed the editor from an existing definition (hydrate) or start empty.
function seed(process: ProcessDef | null, columns: SnapshotColumn[]): EditStage[] {
  if (!process) return [];
  return process.stages.map((s) => ({
    column: s.column,
    // Prefer the live snapshot column label; fall back to the def's label / name.
    label: columns.find((c) => c.name === s.column)?.label ?? s.label ?? s.column,
    sla_seconds: s.sla_seconds ?? 0,
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
  // Fires with the ordered stage payload + optional {title,row_scope,start_trigger}.
  onDefine: (
    stages: ProcessStageInput[],
    opts?: { title?: string; row_scope?: string; start_trigger?: string },
  ) => void;
  onEnable: () => void;
  onDisable: () => void;
}): JSX.Element {
  const [stages, setStages] = useState<EditStage[]>(() => seed(process, columns));
  // The column selected in the "add a stage" picker (before the + Add click).
  const [pick, setPick] = useState("");

  // Columns not yet used as a stage — the only ones the picker offers (a column
  // fills exactly one stage). The sheet's LABEL/title column is excluded: it is
  // set by the row creator and is always filled, so a label stage would
  // auto-complete instantly (a no-op stage). Filter it the same way we filter
  // already-staged columns.
  const staged = new Set(stages.map((s) => s.column));
  const available = columns.filter((c) => !staged.has(c.name) && !c.is_label);

  const addStage = () => {
    if (!pick) return;
    const c = columns.find((col) => col.name === pick);
    if (!c || staged.has(pick)) return;
    setStages((prev) => [...prev, { column: c.name, label: c.label, sla_seconds: 0 }]);
    setPick("");
  };

  const removeStage = (i: number) => {
    setStages((prev) => prev.filter((_, idx) => idx !== i));
  };

  // Swap stage i with i-1 (move up) — the SLA travels with its stage because the
  // whole EditStage object moves.
  const moveUp = (i: number) => {
    if (i <= 0) return;
    setStages((prev) => {
      const next = [...prev];
      [next[i - 1], next[i]] = [next[i], next[i - 1]];
      return next;
    });
  };

  const moveDown = (i: number) => {
    setStages((prev) => {
      if (i >= prev.length - 1) return prev;
      const next = [...prev];
      [next[i + 1], next[i]] = [next[i], next[i + 1]];
      return next;
    });
  };

  const setSla = (i: number, seconds: number) => {
    setStages((prev) => prev.map((s, idx) => (idx === i ? { ...s, sla_seconds: seconds } : s)));
  };

  const define = () => {
    if (stages.length === 0) return;
    const payload: ProcessStageInput[] = stages.map((s) => ({
      column: s.column,
      sla_seconds: s.sla_seconds,
    }));
    onDefine(payload, process?.title ? { title: process.title } : undefined);
  };

  return (
    <section className="arbor-process-config" data-testid="process-config" data-sheet={sheet}>
      {/* The modal chrome already renders a "Process" title bar + ✕, so this
          header carries only the status pill (not a duplicate <h2>). */}
      <header className="arbor-pc-header">
        <span className="arbor-pc-header-label">Stages</span>
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

      {/* One-line mental-model explainer: order IS fill order, and a stage
          completes when its column receives a value (a default counts), which
          notifies the next stage's owner. Both a UX and a correctness fix. */}
      <p className="arbor-pc-hint" data-testid="pc-hint">
        Stages fill left to right. A stage completes when its column gets a value
        (a default counts), which notifies the next stage's owner.
      </p>

      {stages.length === 0 ? (
        <p className="arbor-pc-empty" data-testid="pc-empty">
          No stages yet. Add columns below in the order they should be filled.
        </p>
      ) : (
        <ol className="arbor-pc-stages" data-testid="pc-stages">
          {stages.map((s, i) => (
            <li key={s.column} className="arbor-pc-stage" data-testid={`pc-stage-${i}`} data-column={s.column}>
              <span className="arbor-pc-stage-idx" aria-hidden="true">
                {i + 1}
              </span>
              <span className="arbor-pc-stage-label">{s.label}</span>
              <label className="arbor-pc-stage-sla">
                <span className="arbor-field-label">SLA (s)</span>
                <input
                  type="number"
                  min={0}
                  className="arbor-field-narrow"
                  data-testid={`pc-stage-sla-${i}`}
                  value={s.sla_seconds}
                  onChange={(e) => setSla(i, Math.max(0, Number(e.target.value) || 0))}
                />
                {/* Friendly duration echo so a raw seconds SLA reads as a real
                    due window (e.g. 172800 → "2d"). Hidden when there's no SLA. */}
                <span className="arbor-pc-stage-sla-hint" data-testid={`pc-stage-sla-hint-${i}`}>
                  {s.sla_seconds > 0 ? formatSla(s.sla_seconds) : "no SLA"}
                </span>
              </label>
              <span className="arbor-pc-stage-move">
                {i > 0 && (
                  <button
                    type="button"
                    data-testid={`pc-stage-up-${i}`}
                    aria-label={`Move ${s.label} earlier`}
                    onClick={() => moveUp(i)}
                  >
                    ↑
                  </button>
                )}
                {i < stages.length - 1 && (
                  <button
                    type="button"
                    data-testid={`pc-stage-down-${i}`}
                    aria-label={`Move ${s.label} later`}
                    onClick={() => moveDown(i)}
                  >
                    ↓
                  </button>
                )}
              </span>
              <button
                type="button"
                className="arbor-pc-stage-remove"
                data-testid={`pc-stage-remove-${i}`}
                aria-label={`Remove ${s.label}`}
                onClick={() => removeStage(i)}
              >
                ✕
              </button>
            </li>
          ))}
        </ol>
      )}

      <div className="arbor-pc-add" data-testid="pc-add">
        <select
          data-testid="pc-add-column"
          value={pick}
          onChange={(e) => setPick(e.target.value)}
        >
          <option value="">Add a stage…</option>
          {available.map((c) => (
            <option key={c.name} value={c.name}>
              {c.label}
            </option>
          ))}
        </select>
        <button type="button" data-testid="pc-add-stage" disabled={!pick} onClick={addStage}>
          + Add
        </button>
      </div>

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
          disabled={stages.length === 0}
          onClick={define}
        >
          Save process
        </button>
      </footer>
    </section>
  );
}
