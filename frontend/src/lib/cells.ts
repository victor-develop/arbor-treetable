// Pure cell-value helpers: per-type normalization, equality (for the no-op
// guard, WEB_UI-018/-024), and select-split toggle math. Selects always store
// arrays (DATA-MODEL §4). No ACL here — affordance gating reads snapshot hints.

import type { ColumnType, SelectOptions, SnapshotColumn } from "../api";

export function flattenOptions(options?: SelectOptions | null): string[] {
  if (!options) return [];
  return options.groups.flatMap((g) => g.options);
}

// Normalize a raw cell value into the canonical stored shape for its type.
export function normalizeValue(type: ColumnType, raw: unknown): unknown {
  switch (type) {
    case "single-select-split":
      // single cardinality → at most a 1-element array
      if (Array.isArray(raw)) return raw.slice(0, 1);
      return raw == null || raw === "" ? [] : [raw];
    case "multi-select-split":
      if (Array.isArray(raw)) return raw;
      return raw == null || raw === "" ? [] : [raw];
    case "number":
      return raw === "" || raw == null ? null : Number(raw);
    default:
      return raw ?? "";
  }
}

// Toggle one option in a select cell. single → replace; multi → add/remove
// preserving order (WEB_UI-027..029, -032, -034).
export function toggleOption(
  type: "single-select-split" | "multi-select-split",
  current: unknown,
  option: string,
): string[] {
  const arr = Array.isArray(current) ? (current as string[]) : [];
  if (type === "single-select-split") {
    return arr.length === 1 && arr[0] === option ? arr : [option];
  }
  return arr.includes(option) ? arr.filter((o) => o !== option) : [...arr, option];
}

// Deep-ish equality sufficient for cell values (scalars + flat arrays).
export function valuesEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v, i) => v === b[i]);
  }
  // treat null/undefined/"" as equal-ish only when both empty-ish
  const emptyA = a == null || a === "";
  const emptyB = b == null || b === "";
  if (emptyA && emptyB) return true;
  return false;
}

// Client-side numeric validation mirroring server params_schema (WEB_UI-019).
export function isValidForType(type: ColumnType, raw: string): boolean {
  if (type === "number") {
    if (raw.trim() === "") return true; // empty clears
    return !Number.isNaN(Number(raw));
  }
  return true;
}

// Selected values not present in the current option set are "legacy/unknown"
// chips, surfaced rather than silently dropped (WEB_UI-033).
export function unknownSelections(value: unknown, options?: SelectOptions | null): string[] {
  if (!Array.isArray(value)) return [];
  const known = new Set(flattenOptions(options));
  return (value as string[]).filter((v) => !known.has(v));
}

// Whether a cell is interactive at all: a read-only-by-policy column
// (editable === false) shows neither edit nor suggest affordance (WEB_UI-016).
export function isCellInteractive(col: SnapshotColumn): boolean {
  return col.editable !== false;
}
