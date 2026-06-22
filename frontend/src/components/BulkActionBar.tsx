// Bulk approve/reject action bar for the Change Requests tab (SPEC P2). The bar
// owns the client-side loop over the selected CR ids: it calls the per-CR async
// onApprove(name) / onReject(name, reason) INDEPENDENTLY (one dispatch each — the
// host wires those to the SAME approveChange/rejectChange capability used by the
// per-row buttons), tolerates partial failure, and reports ONE consolidated
// "X approved · Y failed" summary with a [Retry failed] that re-runs only the
// failed ids. No N-toast spam. It re-derives no ACL — the selection handed in is
// already authority-scoped (see selectableCRs / useCrSelection upstream), so
// every id here is one the viewer may decide.
//
// Renders only when >=1 CR is selected; the sticky dock lives at the panel
// bottom. Per-row Approve/Reject buttons remain and coexist (in ChangeRequestPanel).

import { useState } from "react";
import type { ChangeRequestView } from "../api";

// Authority-scoped selection source of truth: the ONLY CR names the viewer may
// act on. "Select all I can approve" and every bulk op feed from this, so a
// read-only CR can never enter the selection set. A missing viewer_is_approver
// flag is treated as NOT selectable (fail-closed).
export function selectableCRs(crs: ChangeRequestView[]): string[] {
  return crs.filter((cr) => cr.viewer_is_approver === true).map((cr) => cr.name);
}

type Summary = { ok: number; failed: number; failedIds: string[]; verb: "approve" | "reject" };

export function BulkActionBar({
  selected,
  onApprove,
  onReject,
  onClear,
  onComplete,
  onProcessingChange,
}: {
  // authority-scoped selection (subset of selectableCRs)
  selected: string[];
  // per-CR dispatch — independent calls; rejects on failure for that id
  onApprove: (name: string) => Promise<unknown>;
  // reason is the shared optional bulk reject comment
  onReject: (name: string, reason?: string) => Promise<unknown>;
  onClear: () => void;
  // optional: fired after a run settles (App refetches snapshot + refreshes CRs)
  onComplete?: (result: { ok: number; failed: number; verb: "approve" | "reject" }) => void;
  // optional: report which ids are mid-flight so the host can mark rows processing
  onProcessingChange?: (ids: string[]) => void;
}): JSX.Element | null {
  // Two-step reject: first click opens the shared reason field + drop-the-batch
  // warning; confirm fires the loop. Approve fires immediately.
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [processing, setProcessing] = useState(false);
  const [summary, setSummary] = useState<Summary | null>(null);

  // Render while there is a live selection OR a settled summary to report. The
  // host clears the selection after a bulk run settles (queue drains), so without
  // the `summary` clause the bar — and its "X approved · Y failed" line — would
  // unmount the instant the run completes, before it could be read. The action
  // row below still hides when nothing is selected, so a post-run bar shows only
  // the summary (+ Retry failed).
  if (selected.length < 1 && !summary) return null;

  // Run one verb across `ids`, collecting failures in place. One settle → one
  // summary line; never a per-call toast.
  const runBatch = async (ids: string[], verb: "approve" | "reject", rejectReason?: string) => {
    if (processing || ids.length === 0) return;
    setProcessing(true);
    setSummary(null); // drop any prior run's line before this run reports its own
    onProcessingChange?.(ids);
    const failedIds: string[] = [];
    let ok = 0;
    await Promise.all(
      ids.map(async (name) => {
        try {
          if (verb === "approve") await onApprove(name);
          else await onReject(name, rejectReason);
          ok += 1;
        } catch {
          failedIds.push(name);
        }
      }),
    );
    setSummary({ ok, failed: failedIds.length, failedIds, verb });
    setProcessing(false);
    onProcessingChange?.([]);
    onComplete?.({ ok, failed: failedIds.length, verb });
  };

  const approveAll = () => void runBatch(selected, "approve");
  const confirmReject = () => {
    const r = reason.trim();
    setRejecting(false);
    setReason("");
    void runBatch(selected, "reject", r || undefined);
  };
  const retryFailed = () => {
    if (!summary) return;
    void runBatch(summary.failedIds, summary.verb, summary.verb === "reject" ? reason.trim() || undefined : undefined);
  };

  const n = selected.length;

  return (
    <div className="arbor-cr-bulk-bar" data-testid="cr-bulk-bar" data-processing={processing}>
      {n >= 1 && (
      <div className="arbor-cr-bulk-row">
        <span className="arbor-cr-bulk-count" data-testid="cr-bulk-count">
          {processing ? (
            <span className="arbor-cr-bulk-progress" data-testid="cr-bulk-progress">
              Processing {n}…
            </span>
          ) : (
            <>{n} selected</>
          )}
        </span>

        {!rejecting ? (
          <div className="arbor-cr-bulk-actions">
            <button
              type="button"
              className="arbor-primary"
              data-testid="cr-bulk-approve"
              disabled={processing}
              onClick={approveAll}
            >
              Approve {n}
            </button>
            <button
              type="button"
              data-testid="cr-bulk-reject"
              disabled={processing}
              onClick={() => setRejecting(true)}
            >
              Reject {n}
            </button>
            <button
              type="button"
              data-testid="cr-bulk-clear"
              disabled={processing}
              onClick={onClear}
            >
              Clear
            </button>
          </div>
        ) : (
          <div className="arbor-cr-bulk-reject">
            <p className="arbor-cr-bulk-warn" data-testid="cr-bulk-reject-warn">
              Rejecting a batch Change Request drops the whole batch — every change
              in it is discarded.
            </p>
            <label className="arbor-field">
              <span className="arbor-field-label">Reason (optional)</span>
              <input
                data-testid="cr-bulk-reject-reason"
                placeholder="Why are these being rejected?"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
            <div className="arbor-cr-bulk-actions">
              {/* Re-uses the cr-bulk-reject testid for the confirm so the same
                  control both opens and confirms (matches the unit-test flow:
                  click once to open, change reason, click again to fire). */}
              <button
                type="button"
                data-testid="cr-bulk-reject"
                disabled={processing}
                onClick={confirmReject}
              >
                Reject {n}
              </button>
              <button
                type="button"
                data-testid="cr-bulk-reject-cancel"
                disabled={processing}
                onClick={() => {
                  setRejecting(false);
                  setReason("");
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
      )}

      {summary && (
        <div className="arbor-cr-bulk-summary-row">
          <span
            className="arbor-cr-bulk-summary"
            data-testid="cr-bulk-summary"
            data-failed={summary.failed > 0}
          >
            {summary.ok} {summary.verb === "approve" ? "approved" : "rejected"} of{" "}
            {summary.ok + summary.failed} · {summary.failed} failed
          </span>
          {summary.failed > 0 && (
            <button
              type="button"
              data-testid="cr-bulk-retry"
              disabled={processing}
              onClick={retryFailed}
            >
              Retry failed
            </button>
          )}
        </div>
      )}
    </div>
  );
}
