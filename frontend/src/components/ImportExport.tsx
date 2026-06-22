// Import / export snapshot (WEB_UI-074..082). Export downloads the exact
// snapshot the server returned (viewer's read scope, no fabrication). Import
// validates the file, previews the plan, and on confirm replays through
// executeAction (governed — unauthorized rows become CRs, not raw writes). The
// host supplies the dispatch so this stays a thin shell.

import { useState } from "react";
import type { Snapshot } from "../api";
import { buildImportPlan, exportSnapshot, validateImport, type ImportPlanStep } from "../lib/io";

export function ImportExport({
  snapshot,
  targetSheet,
  existing,
  onExport,
  onConfirmImport,
}: {
  snapshot: Snapshot | null;
  targetSheet: string;
  existing?: Snapshot;
  // host wires the actual file download (kept out of this component for testability)
  onExport?: (text: string) => void;
  // host runs each governed step through executeAction and reports a summary
  onConfirmImport: (steps: ImportPlanStep[]) => void;
}): JSX.Element {
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<ImportPlanStep[] | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);

  const doExport = () => {
    if (!snapshot) return;
    onExport?.(exportSnapshot(snapshot));
  };

  const onFile = (text: string) => {
    setPlan(null);
    setError(null);
    const v = validateImport(text);
    if (!v.ok) {
      setError(v.error);
      return;
    }
    // preview only — no executeAction until confirm (WEB_UI-076)
    setPlan(buildImportPlan(v.snapshot, targetSheet, existing));
  };

  return (
    <div className="arbor-io" data-testid="import-export">
      <button type="button" data-testid="export-btn" onClick={doExport} disabled={!snapshot}>
        Export
      </button>

      {/* Native file input hidden; a styled label is the visible affordance
          (Playwright setInputFiles still targets the input by testid). */}
      <label className="arbor-file">
        <input
          type="file"
          accept="application/json"
          data-testid="import-file"
          onChange={async (e) => {
            const file = e.target.files?.[0];
            if (file) {
              setFileName(file.name);
              onFile(await file.text());
            }
          }}
        />
        <span className="arbor-file-btn">Choose file</span>
        <span className="arbor-file-name">{fileName ?? "No file chosen"}</span>
      </label>
      {/* paste a snapshot JSON to import (alternative to the file picker) */}
      <textarea
        data-testid="import-text"
        aria-label="Paste snapshot JSON to import"
        placeholder="…or paste exported snapshot JSON here to import"
        rows={3}
        onChange={(e) => onFile(e.target.value)}
      />

      {error && (
        <p role="alert" data-testid="import-error">
          {error}
        </p>
      )}

      {plan && (
        <div data-testid="import-preview">
          <p data-testid="import-summary">
            {plan.filter((s) => s.action === "addColumn").length} columns,{" "}
            {plan.filter((s) => s.action === "addNode").length} nodes
          </p>
          <button
            type="button"
            data-testid="import-confirm"
            onClick={() => {
              onConfirmImport(plan);
              setPlan(null);
            }}
          >
            Confirm import
          </button>
        </div>
      )}
    </div>
  );
}
