// Feature 2 (shareable views) — PURE, presentation-only view layer. A SheetView
// is an overlay (hidden / order / width / collapsed) encoded into a ?v= base64url
// token so a sheet's column visibility/ordering/sizing + collapsed subtrees can
// be shared by link. This layer issues ZERO mutations and touches no backend.
//
// The load-bearing invariant is REVEAL-IMPOSSIBILITY: resolveColumns ALWAYS
// starts from the (already read-ACL-filtered) snapshot.columns, so a column the
// recipient cannot read can NEVER be surfaced even if a forwarded token's
// order/hidden/width names it. visible = (present MINUS hidden), never a
// force-show. The label column is always kept (nodes need their display label).

import type { SnapshotColumn } from "../api";

export type SheetView = {
  v: 1;
  hidden: string[];
  order: string[];
  // optional per-column pixel width overlay.
  width?: Record<string, number>;
  // optional collapsed-subtree seed (node names) — seeds the tree's collapsed Set.
  collapsed?: string[];
};

// Max decoded token length we accept. A shared link is presentation state, not a
// payload channel; anything larger is treated as malformed → default view.
const MAX_TOKEN_LEN = 4096;

// JSON → utf8 → base64url (no padding). URL-safe: + → -, / → _, strip '='.
export function encodeView(view: SheetView): string {
  const json = JSON.stringify(view);
  // encode utf8 safely (btoa is latin1-only) via encodeURIComponent round-trip.
  const utf8 = unescape(encodeURIComponent(json));
  return btoa(utf8).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// base64url → SheetView, or null on ANY problem (non-base64url, non-JSON,
// oversize, unknown version, wrong shape). A null view means "use the default".
export function decodeView(token: string | null | undefined): SheetView | null {
  if (!token) return null;
  if (token.length > MAX_TOKEN_LEN) return null;
  try {
    const b64 = token.replace(/-/g, "+").replace(/_/g, "/");
    // atob tolerates missing padding in practice but normalize anyway.
    const pad = b64.length % 4 === 0 ? b64 : b64 + "=".repeat(4 - (b64.length % 4));
    const utf8 = atob(pad);
    const json = decodeURIComponent(escape(utf8));
    const parsed = JSON.parse(json) as unknown;
    if (!isSheetView(parsed)) return null;
    return parsed;
  } catch {
    return null;
  }
}

function isStringArray(x: unknown): x is string[] {
  return Array.isArray(x) && x.every((s) => typeof s === "string");
}

function isSheetView(x: unknown): x is SheetView {
  if (!x || typeof x !== "object") return false;
  const o = x as Record<string, unknown>;
  if (o.v !== 1) return false;
  if (!isStringArray(o.hidden)) return false;
  if (!isStringArray(o.order)) return false;
  if (o.width !== undefined) {
    if (!o.width || typeof o.width !== "object") return false;
    if (!Object.values(o.width as Record<string, unknown>).every((n) => typeof n === "number")) {
      return false;
    }
  }
  if (o.collapsed !== undefined && !isStringArray(o.collapsed)) return false;
  return true;
}

// SECURITY-CRITICAL, PURE. Resolve the columns to render from the (read-ACL
// filtered) snapshot columns + an optional view. A null view → default
// (all readable columns in snapshot order). Reveal is structurally impossible:
// every result column is drawn from `snapshotColumns`, so nothing absent there
// can appear regardless of what the view names.
export function resolveColumns(
  snapshotColumns: SnapshotColumn[],
  view: SheetView | null,
): SnapshotColumn[] {
  const present = new Map(snapshotColumns.map((c) => [c.name, c]));
  // Default view: everything readable, snapshot order, no width overlay.
  if (!view) return snapshotColumns.slice();

  const hidden = new Set(view.hidden);
  const isVisible = (c: SnapshotColumn): boolean => c.is_label || !hidden.has(c.name);

  // Candidate visible set = present MINUS hidden (label always kept).
  const visible = snapshotColumns.filter(isVisible);
  const visibleNames = new Set(visible.map((c) => c.name));

  // Order: view.order ∩ visible (in view order), then remaining visible in
  // snapshot order. The label column always leads regardless of order.
  const ordered: SnapshotColumn[] = [];
  const taken = new Set<string>();

  // Label first (never hideable, never depends on order).
  for (const c of visible) {
    if (c.is_label) {
      ordered.push(c);
      taken.add(c.name);
    }
  }
  // Then the explicitly-ordered, still-visible, non-label columns.
  for (const name of view.order) {
    if (taken.has(name)) continue;
    if (!visibleNames.has(name)) continue; // unknown/deleted/hidden → dropped
    const c = present.get(name);
    if (c && !c.is_label) {
      ordered.push(c);
      taken.add(name);
    }
  }
  // Then any remaining visible columns in snapshot order (new-since-link case).
  for (const c of visible) {
    if (!taken.has(c.name)) {
      ordered.push(c);
      taken.add(c.name);
    }
  }

  // Apply the width overlay only to columns that survived (no phantom widths).
  if (view.width) {
    const width = view.width;
    return ordered.map((c) =>
      c.name in width ? { ...c, width: width[c.name] } : c,
    );
  }
  return ordered;
}
