// Import / export of a sheet snapshot (WEB_UI-074..082). Export serializes the
// exact snapshot shape the server returns (no client-side fabrication — the
// export equals the viewer's read scope, WEB_UI-075). Import validates the file
// then produces a plan of capability calls; the actual replay funnels through
// executeAction (governed — unauthorized rows become CRs, never raw writes).

import { COLUMN_TYPES } from "./capabilities";
import type { Snapshot } from "../api";

export function exportSnapshot(snapshot: Snapshot): string {
  return JSON.stringify(snapshot, null, 2);
}

export type ImportPlanStep =
  | { action: "addColumn"; params: Record<string, unknown> }
  | { action: "addNode"; params: Record<string, unknown> }
  | { action: "updateCell"; params: Record<string, unknown> };

export type ImportValidation =
  | { ok: true; snapshot: Snapshot }
  | { ok: false; error: string };

const VALID_TYPES = new Set<string>(COLUMN_TYPES);

// Parse + structurally validate an import file before any write (WEB_UI-079,
// -080). Returns a typed error rather than throwing so the UI can surface it.
export function validateImport(raw: string): ImportValidation {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ok: false, error: "File is not valid JSON." };
  }
  if (!parsed || typeof parsed !== "object") {
    return { ok: false, error: "Import must be a snapshot object." };
  }
  const snap = parsed as Partial<Snapshot>;
  if (!Array.isArray(snap.columns) || !Array.isArray(snap.nodes)) {
    return { ok: false, error: "Missing required 'columns' or 'nodes'." };
  }
  for (const c of snap.columns) {
    if (!c || typeof c.field !== "string") {
      return { ok: false, error: "A column is missing its 'field' key." };
    }
    if (!VALID_TYPES.has(c.type)) {
      return { ok: false, error: `Unsupported column type: ${String(c.type)}` };
    }
  }
  return { ok: true, snapshot: parsed as Snapshot };
}

// Build the ordered plan of governed capability calls for a confirmed import
// into `targetSheet`. Idempotency-aware: columns whose field already exists in
// `existing` and nodes whose label already matches are skipped (WEB_UI-081).
export function buildImportPlan(
  source: Snapshot,
  targetSheet: string,
  existing?: Snapshot,
): ImportPlanStep[] {
  const existingFields = new Set((existing?.columns ?? []).map((c) => c.field));
  const existingLabels = new Set((existing?.nodes ?? []).map((n) => n.label ?? ""));
  const steps: ImportPlanStep[] = [];

  for (const c of source.columns) {
    if (existingFields.has(c.field)) continue;
    steps.push({
      action: "addColumn",
      params: {
        sheet: targetSheet,
        field: c.field,
        label: c.label,
        type: c.type,
        options: c.options ?? null,
        column_owner: c.column_owner,
        is_label: c.is_label,
      },
    });
  }

  // Cell values in the snapshot are keyed by column NAME (sheet-specific); re-key
  // them by FIELD so they resolve against the freshly-created target columns.
  const nameToField = new Map(source.columns.map((c) => [c.name, c.field]));
  const byField = (values: Record<string, unknown>) =>
    Object.fromEntries(
      Object.entries(values || {}).map(([k, v]) => [nameToField.get(k) ?? k, v]),
    );

  // Nodes in NestedSet order so parents precede children. ``_src`` carries the
  // source node name; the replay (App.onConfirmImport) maps it to the new target
  // node id and rewrites child ``parent`` references accordingly.
  const ordered = [...source.nodes].sort((a, b) => a.lft - b.lft);
  for (const n of ordered) {
    if (n.label != null && existingLabels.has(n.label)) continue;
    steps.push({
      action: "addNode",
      params: { sheet: targetSheet, parent: n.parent, values: byField(n.values), _src: n.name },
    });
  }
  return steps;
}
