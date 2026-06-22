// The shared Change Request review panel (WEB_UI-072, -088, -089). The SAME
// component renders whether the CR was reached from the grid or an agent CR chip
// (DRY — no agent-specific approval path). Control visibility follows the
// requester-vs-approver role; Approve disables on first click to prevent a
// double-replay (WEB_UI-089).

import { useState } from "react";

// Single source of truth for these shapes is api.ts; re-export so existing
// imports `from "./components/ChangeRequestPanel"` keep working.
import type { ChangeRequestItem, ChangeRequestView } from "../api";
export type { ChangeRequestItem, ChangeRequestView };

function payloadBits(p: Record<string, unknown>): string {
  return ["node", "column", "new_parent", "field", "value", "patch"]
    .filter((k) => p[k] !== undefined && p[k] !== null)
    .map((k) => `${k}=${JSON.stringify(p[k])}`)
    .join(", ");
}

function itemSummary(c: ChangeRequestItem): string {
  return `${c.action}(${payloadBits(c.payload || {})})`;
}

// A readable one-line diff for a single-change CR (whose change lives at the CR
// top level, not in the `changes` table).
function singleSummary(cr: ChangeRequestView): string {
  const verb = [cr.operation, cr.target_kind].filter(Boolean).join(" ");
  const bits = payloadBits(cr.payload || {});
  return `${verb || "change"}(${bits})`;
}

// The compact RAW op string (monospace) shown only inside Details now — it is the
// machine-precise diff, demoted below the human-readable decision lead (UX:
// scannability). For a batch CR it names the first change + "+K more".
function rowSummary(cr: ChangeRequestView): string {
  const items = cr.changes ?? [];
  if (items.length === 0) return singleSummary(cr);
  const first = itemSummary(items[0]);
  return items.length > 1 ? `${first} +${items.length - 1} more` : first;
}

// Humanize a scoped name: drop the "col:"/"node:" prefix so an approver reads
// "Budget", not "col:budget". Falls back to the raw value.
function humanize(raw: unknown): string {
  if (typeof raw !== "string" || raw === "") return String(raw ?? "");
  const bare = raw.includes(":") ? raw.slice(raw.indexOf(":") + 1) : raw;
  return bare.charAt(0).toUpperCase() + bare.slice(1);
}

function fmtValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

// The human-readable decision lead shown FIRST on the collapsed row (UX fix):
// the single piece of information an approver needs — node · column → value —
// instead of a truncated raw op string. The raw op string + CR hash are demoted
// into the Details disclosure. Returns null when the payload carries nothing
// decision-shaped (e.g. an unusual op) so the row falls back to the raw summary.
function decisionLead(cr: ChangeRequestView): string | null {
  // Prefer the first change's payload for a batch; else the CR's own payload.
  const items = cr.changes ?? [];
  const p = (items.length > 0 ? items[0].payload : cr.payload) || {};
  const node = p.node;
  const column = p.column ?? p.field;
  const parts: string[] = [];
  if (typeof node === "string" && node) parts.push(humanize(node));
  if (typeof column === "string" && column) parts.push(humanize(column));
  // Structural move: lead with the destination parent instead of a value.
  if (p.new_parent !== undefined) {
    const dest = p.new_parent === null ? "root" : humanize(p.new_parent);
    if (parts.length === 0) return null;
    return `${parts.join(" · ")} → under ${dest}`;
  }
  if (p.value !== undefined) {
    if (parts.length === 0) return null;
    return `${parts.join(" · ")} → ${fmtValue(p.value)}`;
  }
  // No node/column/value to anchor a human lead.
  return parts.length > 0 ? parts.join(" · ") : null;
}

export function ChangeRequestPanel({
  cr,
  viewer,
  onApprove,
  onReject,
  onWithdraw,
  // Bulk-selection wiring (SPEC P2). A checkbox renders ONLY when the viewer can
  // approve this CR (cr.viewer_is_approver); non-actionable rows show a muted
  // "Approver: <resolved_approver>" caption instead — never a checkbox, so the
  // selection set can never include a CR the viewer cannot decide. When
  // `selectable` is undefined the panel renders exactly as before (no checkbox,
  // no caption) — preserves the standalone unit-test contract.
  selectable,
  selected = false,
  onToggleSelect,
  processing = false,
}: {
  cr: ChangeRequestView;
  viewer: string;
  onApprove: (name: string) => void;
  onReject: (name: string) => void;
  onWithdraw: (name: string) => void;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (name: string, checked: boolean) => void;
  processing?: boolean;
}): JSX.Element {
  const [acting, setActing] = useState(false);
  // Density (UX): each CR is a single-line row by default — checkbox · id · author
  // · one-line diff summary · row actions. The full per-change breakdown lives
  // behind a Details toggle, so a queue of 30-50 stays scannable instead of an
  // endless wall of stacked cards.
  const [expanded, setExpanded] = useState(false);
  // Prefer the server-computed flag (works for multi-change CRs whose approver is
  // per-item); fall back to the single resolved_approver for legacy single-change.
  const isApprover = cr.viewer_is_approver ?? viewer === cr.resolved_approver;
  const isRequester = viewer === cr.requester;
  const terminal = cr.status !== "proposed";
  const items = cr.changes ?? [];
  // Human-readable decision (UX fix): node · column → value, shown before any
  // truncation. The raw monospace op string + CR hash are demoted to Details.
  const lead = decisionLead(cr);
  const rawOp = rowSummary(cr);

  const guard = (fn: (name: string) => void) => () => {
    if (acting || terminal) return;
    setActing(true);
    fn(cr.name);
  };

  // Selection chrome is opt-in: only when the host passes `selectable` AND the CR
  // is still actionable (proposed). An actionable row gets the checkbox; any other
  // row in selection mode gets the read-only "Approver:" caption.
  const selectionEnabled = selectable !== undefined;
  const showCheckbox = selectionEnabled && selectable && !terminal;

  // During an active multi-selection this row's per-row buttons recede so the
  // sticky bulk bar reads as the primary action surface (UX). They remain present
  // + clickable (coexist per SPEC) — just de-emphasized.
  const deemphasizeActions = showCheckbox && selected;

  return (
    <div
      className={`arbor-cr-panel${expanded ? " is-expanded" : ""}`}
      data-testid={`cr-panel-${cr.name}`}
      data-status={cr.status}
      data-processing={processing}
      data-expanded={expanded}
    >
      {/* Single-line row leads with the HUMAN decision (node · column → value),
          then author + batch count; the CR id/hash + raw op string are demoted
          into Details. select · decision · author · expand · actions. */}
      <header>
        {showCheckbox && (
          <input
            type="checkbox"
            className="arbor-cr-select"
            data-testid={`cr-select-${cr.name}`}
            aria-label={`Select Change Request ${cr.name}`}
            checked={selected}
            disabled={processing}
            onChange={(e) => onToggleSelect?.(cr.name, e.target.checked)}
          />
        )}
        {/* Decision lead — the scannable headline. Falls back to the raw op string
            only when the payload carries nothing decision-shaped. */}
        <span className="arbor-cr-decision" data-testid={`cr-rowsummary-${cr.name}`}>
          {lead ?? rawOp}
        </span>
        <span className="arbor-cr-by" data-testid="cr-by">
          by {cr.requester}
        </span>
        {items.length > 0 && <span data-testid="cr-batch-count"> · {items.length} changes</span>}
        {selectionEnabled && !showCheckbox && cr.resolved_approver && (
          <span className="arbor-cr-approver-caption" data-testid={`cr-approver-${cr.name}`}>
            {" "}
            Approver: {cr.resolved_approver}
          </span>
        )}
        <button
          type="button"
          className="arbor-cr-expand"
          data-testid={`cr-expand-${cr.name}`}
          aria-expanded={expanded}
          aria-label={expanded ? "Hide changes" : "Show changes"}
          onClick={() => setExpanded((e) => !e)}
        >
          Details {expanded ? "▴" : "▾"}
        </button>
      </header>

      {expanded && (
        <>
          {/* Demoted raw metadata: the CR hash + the precise monospace op string,
              moved out of the headline row into the disclosure (UX fix). */}
          <div className="arbor-cr-meta" data-testid={`cr-meta-${cr.name}`}>
            <span className="arbor-cr-id">Change Request {cr.name}</span>
            <code className="arbor-cr-rawop">{rawOp}</code>
          </div>
          {items.length > 0 ? (
            <ul className="arbor-cr-changes" data-testid="cr-changes">
              {items.map((c, i) => (
                <li key={i} data-testid={`cr-change-${i}`} data-approved={!!c.item_approved}>
                  {itemSummary(c)} → {c.resolved_approver}
                  {c.item_approved ? " ✓" : " (pending)"}
                </li>
              ))}
            </ul>
          ) : (
            // Single-change CR: show the proposed change so the reviewer can decide
            // with the diff in view, not just the CR id.
            <ul className="arbor-cr-changes" data-testid="cr-changes">
              <li data-testid="cr-change-0">
                {singleSummary(cr)}
                {cr.resolved_approver ? ` → ${cr.resolved_approver}` : ""}
              </li>
            </ul>
          )}
        </>
      )}

      {isApprover && !terminal && (
        <div className={`arbor-cr-actions${deemphasizeActions ? " is-deemphasized" : ""}`}>
          <button type="button" data-testid="cr-approve" disabled={acting} onClick={guard(onApprove)}>
            Approve
          </button>
          <button type="button" data-testid="cr-reject" disabled={acting} onClick={guard(onReject)}>
            Reject
          </button>
        </div>
      )}
      {isRequester && !isApprover && !terminal && (
        <button type="button" data-testid="cr-withdraw" disabled={acting} onClick={guard(onWithdraw)}>
          Withdraw
        </button>
      )}
      {!isApprover && !isRequester && (
        <span data-testid="cr-readonly">Read-only</span>
      )}
    </div>
  );
}
