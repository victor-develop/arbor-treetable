// Per-process flow dashboard (Feature: process DAG). One card per EDGE (trigger ->
// expected column) showing the pending count, the out-of-SLA (breached) count, and
// the avg open->satisfy time; a top summary of active / completed / throughput.
// Clicking an edge drills into that edge's runs via client.listProcessRuns.
//
// NOTE (WS-F1 bridge): this is a minimal projection over the new edge aggregate;
// the richer edge-metric view is WS-B2. SELF-CONTAINED: it fetches the aggregate
// on mount / sheet change / refreshKey change, and drills lazily. It re-derives no
// ACL — the server redacts an unreadable column's LABEL to null (we render a
// generic placeholder, never the raw field key); run rows carry no cell values.

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ArborClient,
  ProcessDashboard as Dash,
  ProcessDashboardEdge,
  ProcessRun,
} from "../api";

// The destination endpoint of an edge — a readable column label, else a generic
// ordinal that leaks nothing (server redacts an unreadable column's label to null).
function toLabel(edge: ProcessDashboardEdge, idx: number): string {
  return edge.to_label ?? `Step ${idx + 1}`;
}

// The source endpoint of an edge, or null for a row (START) trigger. A column
// trigger whose label the viewer can't read arrives redacted -> generic "Step".
function fromLabel(edge: ProcessDashboardEdge): string | null {
  if (edge.from_kind === "row" || edge.from_column == null) return null;
  return edge.from_label ?? "Step";
}

// Compact SLA-window rendering: seconds -> "45s" / "30m" / "1.0h" (matches the
// avg formatter); null/0 = no SLA window on this edge.
function formatSla(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

// A stable per-edge key (rule_key + expected column).
function edgeKey(edge: ProcessDashboardEdge): string {
  return `${edge.rule_key}:${edge.to_column}`;
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
  // The edge currently drilled into (its key), plus that edge's runs.
  const [openEdge, setOpenEdge] = useState<string | null>(null);
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
    setOpenEdge(null);
    setRuns([]);
  }, [fetchDashboard, refreshKey]);

  const drill = useCallback(
    async (edge: ProcessDashboardEdge) => {
      if (!client.listProcessRuns) return;
      setOpenEdge(edgeKey(edge));
      setRunsLoading(true);
      try {
        const res = await client.listProcessRuns(sheet, {
          rule_key: edge.rule_key,
          column: edge.to_column,
        });
        setRuns(res);
      } catch {
        setRuns([]);
      } finally {
        setRunsLoading(false);
      }
    },
    [client, sheet],
  );

  const edges = dashboard?.edges ?? [];

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

      {edges.length === 0 ? (
        <p className="arbor-pd-empty" data-testid="pd-empty">
          {loading ? "Loading…" : "No process rules."}
        </p>
      ) : (
        <div className="arbor-pd-board" data-testid="pd-board">
          {edges.map((s, idx) => (
            <div
              key={edgeKey(s)}
              className="arbor-pd-stage"
              data-testid={`pd-edge-${idx}`}
              data-breached={s.breached_count > 0}
            >
              <button
                type="button"
                className="arbor-pd-stage-head"
                data-testid="pd-stage-drill"
                aria-label={`Show runs for ${toLabel(s, idx)}`}
                onClick={() => void drill(s)}
              >
                <span className="arbor-pd-stage-label" data-testid="pd-stage-label">
                  {fromLabel(s) && (
                    <>
                      <span className="arbor-pd-stage-from" data-testid="pd-stage-from">
                        {fromLabel(s)}
                      </span>
                      <span className="arbor-pd-stage-arrow" aria-hidden="true">
                        →
                      </span>
                    </>
                  )}
                  <span className="arbor-pd-stage-to" data-testid="pd-stage-to">
                    {toLabel(s, idx)}
                  </span>
                </span>
                {s.within_seconds > 0 && (
                  <span className="arbor-pd-sla" data-testid="pd-sla" title="SLA window">
                    ≤ {formatSla(s.within_seconds)}
                  </span>
                )}
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
                <span className="arbor-pd-count is-satisfied" data-testid="pd-satisfied" title="satisfied">
                  {s.satisfied_count}
                </span>
                <span className="arbor-pd-avg" data-testid="pd-avg" title="avg open to satisfy">
                  {formatAvg(s.avg_open_to_satisfy_seconds)}
                </span>
              </div>

              {openEdge === edgeKey(s) && (
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
