// Per-process kanban / flow dashboard (Feature: process). One column per stage
// showing the pending count, the out-of-SLA (breached) count, and the avg time in
// stage (enter -> fill); a top summary of active / completed / throughput.
// Clicking a stage drills into that stage's runs via client.listProcessRuns.
//
// SELF-CONTAINED: it fetches the dashboard aggregate on mount / sheet change /
// refreshKey change against client.processDashboard, and fetches the drill-down
// runs lazily on a stage click. It re-derives no ACL — the server redacts an
// unreadable stage column's LABEL to null (we render a generic "Stage N"
// placeholder, never the raw field key), and run rows never carry cell values.

import { useCallback, useEffect, useRef, useState } from "react";
import type { ArborClient, ProcessDashboard as Dash, ProcessRun } from "../api";

// A stage label the viewer can read, else a generic ordinal that leaks nothing.
function stageLabel(label: string | null, idx: number): string {
  return label ?? `Stage ${idx + 1}`;
}

// Compact avg-duration rendering: seconds -> "2m" / "1.0h" / "—" when unknown.
function formatAvg(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

export function ProcessDashboard({
  client,
  sheet,
  refreshKey,
}: {
  client: ArborClient;
  sheet: string;
  // Bumped by the host when the process mutates, so the dashboard re-fetches.
  refreshKey?: number;
}): JSX.Element {
  const [dashboard, setDashboard] = useState<Dash | null>(null);
  const [loading, setLoading] = useState(false);
  // The stage currently drilled into (its idx), plus that stage's runs.
  const [openStage, setOpenStage] = useState<number | null>(null);
  const [runs, setRuns] = useState<ProcessRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  // Guards a stale dashboard fetch from clobbering a newer one.
  const reqId = useRef(0);

  const fetchDashboard = useCallback(async () => {
    if (!client.processDashboard) return;
    const id = ++reqId.current;
    setLoading(true);
    try {
      const res = await client.processDashboard(sheet);
      if (id !== reqId.current) return;
      setDashboard(res);
    } catch {
      if (id === reqId.current) setDashboard(null);
    } finally {
      if (id === reqId.current) setLoading(false);
    }
  }, [client, sheet]);

  useEffect(() => {
    void fetchDashboard();
    // A dashboard refetch invalidates any open drill-down.
    setOpenStage(null);
    setRuns([]);
  }, [fetchDashboard, refreshKey]);

  const drill = useCallback(
    async (stageIdx: number) => {
      if (!client.listProcessRuns) return;
      setOpenStage(stageIdx);
      setRunsLoading(true);
      try {
        const res = await client.listProcessRuns(sheet, { stage_idx: stageIdx });
        setRuns(res);
      } catch {
        setRuns([]);
      } finally {
        setRunsLoading(false);
      }
    },
    [client, sheet],
  );

  const stages = dashboard?.stages ?? [];

  return (
    <section className="arbor-process-dashboard" data-testid="process-dashboard" data-sheet={sheet}>
      <header className="arbor-pd-summary" data-testid="pd-summary">
        <span className="arbor-pd-metric">
          <span className="arbor-pd-metric-value" data-testid="pd-total-active">
            {dashboard?.total_active ?? 0}
          </span>
          <span className="arbor-pd-metric-label">active</span>
        </span>
        <span className="arbor-pd-metric">
          <span className="arbor-pd-metric-value" data-testid="pd-total-completed">
            {dashboard?.total_completed ?? 0}
          </span>
          <span className="arbor-pd-metric-label">completed</span>
        </span>
        <span className="arbor-pd-metric">
          <span className="arbor-pd-metric-value" data-testid="pd-throughput">
            {dashboard?.throughput ?? 0}
          </span>
          <span className="arbor-pd-metric-label">throughput</span>
        </span>
      </header>

      {stages.length === 0 ? (
        <p className="arbor-pd-empty" data-testid="pd-empty">
          {loading ? "Loading…" : "No process stages."}
        </p>
      ) : (
        <div className="arbor-pd-board" data-testid="pd-board">
          {stages.map((s) => (
            <div
              key={s.idx}
              className="arbor-pd-stage"
              data-testid={`pd-stage-${s.idx}`}
              data-breached={s.breached_count > 0}
            >
              <button
                type="button"
                className="arbor-pd-stage-head"
                data-testid="pd-stage-drill"
                aria-label={`Show runs in ${stageLabel(s.label, s.idx)}`}
                onClick={() => void drill(s.idx)}
              >
                <span className="arbor-pd-stage-label" data-testid="pd-stage-label">
                  {stageLabel(s.label, s.idx)}
                </span>
              </button>
              <div className="arbor-pd-stage-metrics">
                <span className="arbor-pd-count" data-testid="pd-pending" title="pending">
                  {s.pending_count}
                </span>
                <span
                  className="arbor-pd-count is-breached"
                  data-testid="pd-breached"
                  data-breached={s.breached_count > 0}
                  title="out of SLA"
                >
                  {s.breached_count}
                </span>
                <span className="arbor-pd-avg" data-testid="pd-avg" title="avg time in stage">
                  {formatAvg(s.avg_enter_to_fill_seconds)}
                </span>
              </div>

              {openStage === s.idx && (
                <ul className="arbor-pd-runs" data-testid="pd-runs">
                  {runsLoading ? (
                    <li className="arbor-pd-runs-loading">Loading…</li>
                  ) : runs.length === 0 ? (
                    <li className="arbor-pd-runs-empty" data-testid="pd-runs-empty">
                      No runs in this stage.
                    </li>
                  ) : (
                    runs.map((r) => (
                      <li key={r.name} className="arbor-pd-run" data-testid={`pd-run-${r.name}`} data-status={r.status}>
                        <span className="arbor-pd-run-node">{r.node}</span>
                        <span className="arbor-pd-run-status">{r.status}</span>
                      </li>
                    ))
                  )}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
