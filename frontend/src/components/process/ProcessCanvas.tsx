// The bespoke, dependency-free SVG/DOM DAG canvas (WS-A2). A process is a SET of
// trigger->expectation rules; the canvas is a VIEW/PRODUCER over that model. It
// renders a fixed START node ("Row created / updated") + one node per
// participating column, and edges (trigger -> expected column) each carrying an
// editable within-duration chip. It is CONTROLLED: `rules` in, `onChange(rules)`
// out — every mutation re-derives the whole payload via graph.packRules, so the
// SAME `ProcessRuleInput[]` the LLM emits is what a canvas edit emits.
//
// Interactions (all keyboard-reachable, no drag dependency so they unit-test):
//   * add a column node from the sheet's columns (picker + Add)
//   * connect: click a node's "connect" affordance, then click a target node to
//     draw the edge — REJECTED (with an aria-live message, no onChange) if it
//     would self-loop or close a cycle (graph.wouldCreateCycle)
//   * edit an edge's within-duration (seconds) inline
//   * delete an edge; delete a column node (drops its touching edges)
// Validation (self-loop / duplicate / cycle errors + unreachable warnings) is
// surfaced inline via graph.validate; the server re-validates as the authority.

import { useMemo, useState } from "react";
import type { ProcessRuleInput, SnapshotColumn } from "../../api";
import { PlusIcon, TrashIcon } from "../icons";
import {
  START_ID,
  NODE_SIZE,
  buildGraph,
  layout,
  packRules,
  validate,
  wouldCreateCycle,
  type GraphEdge,
} from "./graph";

// Compact seconds -> "2d 4h" / "3h" / "45m" / "30s" for the within-duration chip,
// so an owner reads a real window instead of a raw seconds count. 0 => "no SLA".
export function formatWithin(seconds: number): string {
  if (!seconds || seconds <= 0) return "no SLA";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const parts: string[] = [];
  if (d) parts.push(`${d}d`);
  if (h) parts.push(`${h}h`);
  if (m) parts.push(`${m}m`);
  if (s && !d && !h) parts.push(`${s}s`);
  return parts.slice(0, 2).join(" ") || "no SLA";
}

export function ProcessCanvas({
  columns,
  rules,
  onChange,
}: {
  // Sheet columns that may become nodes (the LABEL column is excluded UPSTREAM by
  // ProcessConfigPanel; the canvas graphs whatever it is given).
  columns: SnapshotColumn[];
  // The current draft rule set (controlled). The canvas derives edges from it.
  rules: ProcessRuleInput[];
  // Fires with the whole re-packed rule payload on EVERY structural mutation.
  onChange: (rules: ProcessRuleInput[]) => void;
}): JSX.Element {
  const labelOf = useMemo(() => {
    const map = new Map(columns.map((c) => [c.name, c.label]));
    return (col: string) => map.get(col) ?? null;
  }, [columns]);

  // Derive edges from the incoming rules. Column nodes that carry NO edge yet
  // (freshly added, not connected) are tracked separately so they still render.
  const graph = useMemo(() => buildGraph(rules, labelOf), [rules, labelOf]);
  const [looseNodes, setLooseNodes] = useState<string[]>([]);

  // The full node set = graph nodes (START + edge-touched columns) + loose nodes.
  const nodes = useMemo(() => {
    const seen = new Set(graph.nodes.map((n) => n.id));
    const extra = looseNodes
      .filter((id) => !seen.has(id))
      .map((id) => ({ id, label: labelOf(id), kind: "column" as const }));
    return [...graph.nodes, ...extra];
  }, [graph, looseNodes, labelOf]);

  const fullGraph = useMemo(() => ({ nodes, edges: graph.edges }), [nodes, graph.edges]);
  const laid = useMemo(() => layout(fullGraph), [fullGraph]);
  const posOf = useMemo(
    () => new Map(laid.nodes.map((n) => [n.id, n])),
    [laid],
  );
  const result = useMemo(() => validate(fullGraph), [fullGraph]);

  // The column picker value; connect-mode source node; last rejection message.
  const [pick, setPick] = useState("");
  const [connectFrom, setConnectFrom] = useState<string | null>(null);
  const [reject, setReject] = useState("");

  // Columns not yet a node — the only ones the picker offers.
  const nodeIds = new Set(nodes.map((n) => n.id));
  const available = columns.filter((c) => !c.is_label && !nodeIds.has(c.name));

  const emit = (edges: GraphEdge[]) => onChange(packRules(edges));

  const addNode = () => {
    if (!pick || nodeIds.has(pick)) return;
    setLooseNodes((prev) => (prev.includes(pick) ? prev : [...prev, pick]));
    setPick("");
  };

  // Begin / target a connection. Clicking the source's connect handle arms
  // connect-mode; clicking a node while armed draws the edge (or is rejected).
  const startConnect = (from: string) => {
    setReject("");
    setConnectFrom((cur) => (cur === from ? null : from));
  };

  const completeConnect = (to: string) => {
    if (connectFrom === null) return;
    const from = connectFrom;
    setConnectFrom(null);
    if (from === to) {
      setReject("A node cannot expect itself.");
      return;
    }
    if (graph.edges.some((e) => e.from === from && e.to === to)) {
      setReject("That edge already exists.");
      return;
    }
    if (wouldCreateCycle(graph.edges, { from, to })) {
      setReject("That connection would create a cycle.");
      return;
    }
    // A freshly drawn edge is always a plain 'any' trigger; the target's ANY/ALL
    // toggle (below) promotes a multi-source target to an AND-join.
    const next: GraphEdge = {
      rule_key: `${from}->${to}`,
      from,
      to,
      within_seconds: 0,
      trigger_op: "created-or-updated",
      notify_on_expect: true,
      join: "any",
    };
    // `to` is now edge-touched, so drop it from loose (it will come from graph).
    setLooseNodes((prev) => prev.filter((id) => id !== to && id !== from));
    emit([...graph.edges, next]);
  };

  const deleteEdge = (from: string, to: string) => {
    emit(graph.edges.filter((e) => !(e.from === from && e.to === to)));
  };

  const deleteNode = (id: string) => {
    if (id === START_ID) return; // START is fixed
    setLooseNodes((prev) => prev.filter((x) => x !== id));
    const remaining = graph.edges.filter((e) => e.from !== id && e.to !== id);
    if (remaining.length !== graph.edges.length) emit(remaining);
  };

  const setWithin = (from: string, to: string, seconds: number) => {
    emit(
      graph.edges.map((e) =>
        e.from === from && e.to === to ? { ...e, within_seconds: Math.max(0, seconds) } : e,
      ),
    );
  };

  // Incoming trigger edges per target column (in edge order). A target with >1
  // distinct incoming source can become an AND-join (expect it when ALL sources
  // are filled) instead of separate OR (ANY) rules.
  const incomingByTarget = useMemo(() => {
    const map = new Map<string, GraphEdge[]>();
    for (const e of graph.edges) {
      const list = map.get(e.to);
      if (list) list.push(e);
      else map.set(e.to, [e]);
    }
    return map;
  }, [graph.edges]);

  // A target is an AND-join when ALL its incoming edges are tagged join='all' AND
  // share ONE rule_key (packRules groups an all-join by rule_key). Otherwise ANY.
  const joinOf = (to: string): "any" | "all" => {
    const inc = incomingByTarget.get(to) ?? [];
    if (inc.length < 2) return "any";
    const key = inc[0].rule_key;
    return inc.every((e) => e.join === "all" && e.rule_key === key) ? "all" : "any";
  };

  // Set the ANY/ALL join for a target's incoming trigger set. ALL rewrites every
  // incoming edge to a SHARED rule_key + join='all' (and a shared window/op/notify,
  // taken from the first incoming edge — one join fires on one window). ANY splits
  // them back into independent join='any' edges with per-edge rule_keys.
  const setTargetJoin = (to: string, join: "any" | "all") => {
    const inc = incomingByTarget.get(to) ?? [];
    if (inc.length < 2) return;
    const others = graph.edges.filter((e) => e.to !== to);
    let rewritten: GraphEdge[];
    if (join === "all") {
      const key = `all:${to}`;
      const lead = inc[0];
      rewritten = inc.map((e) => ({
        ...e,
        rule_key: key,
        join: "all",
        within_seconds: lead.within_seconds,
        trigger_op: lead.trigger_op,
        notify_on_expect: lead.notify_on_expect,
      }));
    } else {
      rewritten = inc.map((e) => ({
        ...e,
        rule_key: `${e.from}->${e.to}`,
        join: "any",
      }));
    }
    emit([...others, ...rewritten]);
  };

  const nodeLabel = (id: string): string => {
    if (id === START_ID) return "Row created / updated";
    const n = nodes.find((x) => x.id === id);
    return n?.label ?? "Hidden column";
  };

  const { w, h } = NODE_SIZE;

  return (
    <section className="arbor-canvas" data-testid="process-canvas">
      {/* Add-a-node affordance — pick a sheet column, add it as a node to connect. */}
      <div className="arbor-canvas-add" data-testid="canvas-add">
        <select
          data-testid="canvas-add-column"
          aria-label="Add a column node"
          value={pick}
          onChange={(e) => setPick(e.target.value)}
        >
          <option value="">Add a column…</option>
          {available.map((c) => (
            <option key={c.name} value={c.name}>
              {c.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          data-testid="canvas-add-node"
          className="arbor-canvas-add-btn"
          disabled={!pick}
          onClick={addNode}
        >
          <PlusIcon size={13} /> Add
        </button>
      </div>

      {/* aria-live rejection banner — a self-loop/cycle/dup attempt is announced
          here and never fires onChange. Empty (visually hidden) when clear. */}
      <p className="arbor-canvas-reject" data-testid="canvas-reject" role="alert" aria-live="assertive">
        {reject}
      </p>

      {connectFrom !== null && (
        <p className="arbor-canvas-connect-hint" data-testid="canvas-connect-hint">
          Connecting from <strong>{nodeLabel(connectFrom)}</strong> — pick a target
          node, or press its connect button again to cancel.
        </p>
      )}

      {/* The SVG canvas. Edges are drawn first (under the nodes); nodes are DOM
          buttons in a foreignObject-free overlay so they stay keyboard-reachable
          and testable without pointer geometry. */}
      <div className="arbor-canvas-stage" data-testid="canvas-stage" style={{ position: "relative" }}>
        <svg
          className="arbor-canvas-svg"
          width={laid.width}
          height={laid.height}
          viewBox={`0 0 ${laid.width} ${laid.height}`}
          role="img"
          aria-label="Process flow graph"
        >
          <defs>
            <marker
              id="arbor-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M0 0 L10 5 L0 10 z" fill="var(--outline-strong)" />
            </marker>
          </defs>
          {graph.edges.map((e) => {
            const a = posOf.get(e.from);
            const b = posOf.get(e.to);
            if (!a || !b) return null;
            const x1 = a.x + w / 2;
            const y1 = a.y;
            const x2 = b.x - w / 2;
            const y2 = b.y;
            const isAll = e.join === "all";
            return (
              <g key={`${e.from}->${e.to}`}>
                <line
                  data-testid={`canvas-edge-${e.from}-${e.to}`}
                  data-join={e.join}
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  stroke={isAll ? "var(--accent, var(--outline-strong))" : "var(--outline-strong)"}
                  strokeWidth={isAll ? 2 : 1.5}
                  strokeDasharray={isAll ? "5 3" : undefined}
                  markerEnd="url(#arbor-arrow)"
                />
                {isAll && (
                  // A "∧" (AND) marker at the edge midpoint so a converging join
                  // reads at a glance — every leg of an all-join carries it.
                  <text
                    data-testid={`canvas-edge-join-${e.from}-${e.to}`}
                    x={(x1 + x2) / 2}
                    y={(y1 + y2) / 2 - 4}
                    textAnchor="middle"
                    fontSize={12}
                    fill="var(--accent, var(--outline-strong))"
                    aria-label="AND-join"
                  >
                    ∧
                  </text>
                )}
              </g>
            );
          })}
        </svg>

        {/* Node overlay — absolutely positioned DOM buttons over the SVG. */}
        {laid.nodes.map((n) => (
          <div
            key={n.id}
            data-testid={`canvas-node-${n.id}`}
            className={
              "arbor-canvas-node" +
              (n.kind === "start" ? " is-start" : "") +
              (connectFrom === n.id ? " is-connecting" : "")
            }
            style={{
              position: "absolute",
              left: n.x - w / 2,
              top: n.y - h / 2,
              width: w,
              height: h,
            }}
          >
            <button
              type="button"
              className="arbor-canvas-node-body"
              data-testid={`canvas-node-body-${n.id}`}
              aria-label={
                connectFrom !== null && connectFrom !== n.id
                  ? `Connect to ${nodeLabel(n.id)}`
                  : nodeLabel(n.id)
              }
              onClick={() => {
                if (connectFrom !== null && connectFrom !== n.id) completeConnect(n.id);
              }}
            >
              <span className="arbor-canvas-node-label">{nodeLabel(n.id)}</span>
            </button>
            <span className="arbor-canvas-node-actions">
              <button
                type="button"
                className="arbor-canvas-connect"
                data-testid={`canvas-connect-${n.id}`}
                aria-label={`Connect from ${nodeLabel(n.id)}`}
                aria-pressed={connectFrom === n.id}
                onClick={() => startConnect(n.id)}
              >
                →
              </button>
              {/* ANY/ALL join toggle — only on a target fed by >1 trigger. ANY =
                  separate OR rules (fire on any one); ALL = a single AND-join that
                  fires once every incoming column is filled. */}
              {(incomingByTarget.get(n.id)?.length ?? 0) > 1 && (
                <button
                  type="button"
                  className={
                    "arbor-canvas-join-toggle" +
                    (joinOf(n.id) === "all" ? " is-all" : "")
                  }
                  data-testid={`canvas-join-toggle-${n.id}`}
                  data-join={joinOf(n.id)}
                  aria-pressed={joinOf(n.id) === "all"}
                  aria-label={
                    joinOf(n.id) === "all"
                      ? `${nodeLabel(n.id)} waits for ALL triggers — switch to ANY`
                      : `${nodeLabel(n.id)} fires on ANY trigger — switch to ALL (wait for all)`
                  }
                  onClick={() =>
                    setTargetJoin(n.id, joinOf(n.id) === "all" ? "any" : "all")
                  }
                >
                  {joinOf(n.id) === "all" ? "ALL" : "ANY"}
                </button>
              )}
              {n.kind !== "start" && (
                <button
                  type="button"
                  className="arbor-canvas-node-del"
                  data-testid={`canvas-node-del-${n.id}`}
                  aria-label={`Delete ${nodeLabel(n.id)}`}
                  onClick={() => deleteNode(n.id)}
                >
                  <TrashIcon size={12} />
                </button>
              )}
            </span>
          </div>
        ))}
      </div>

      {/* Edge list — an accessible, testable fallback + the home of each edge's
          within-duration editor and delete. Mirrors the SVG edges 1:1. */}
      {graph.edges.length === 0 ? (
        <p className="arbor-canvas-empty" data-testid="canvas-empty">
          No connections yet. Add a column, then use its connect button to draw an
          expectation from the start (or another column) to it.
        </p>
      ) : (
        <ul className="arbor-canvas-edges" data-testid="canvas-edge-list">
          {graph.edges.map((e) => (
            <li
              key={`${e.from}->${e.to}`}
              className="arbor-canvas-edge-row"
              data-testid={`canvas-edge-row-${e.from}-${e.to}`}
            >
              <span className="arbor-canvas-edge-desc">
                <strong>{nodeLabel(e.from)}</strong> → <strong>{nodeLabel(e.to)}</strong>
              </span>
              <label className="arbor-canvas-edge-within">
                <span className="arbor-field-label">within (s)</span>
                <input
                  type="number"
                  min={0}
                  className="arbor-field-narrow"
                  data-testid={`canvas-edge-within-${e.from}-${e.to}`}
                  aria-label={`Within seconds for ${nodeLabel(e.from)} to ${nodeLabel(e.to)}`}
                  value={e.within_seconds}
                  onChange={(ev) => setWithin(e.from, e.to, Number(ev.target.value) || 0)}
                />
                <span className="arbor-canvas-edge-within-hint">
                  {formatWithin(e.within_seconds)}
                </span>
              </label>
              <button
                type="button"
                className="arbor-canvas-edge-del"
                data-testid={`canvas-edge-del-${e.from}-${e.to}`}
                aria-label={`Delete edge ${nodeLabel(e.from)} to ${nodeLabel(e.to)}`}
                onClick={() => deleteEdge(e.from, e.to)}
              >
                <TrashIcon size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* Inline validation — hard errors block Save (the panel reads these too via
          the same graph.validate); warnings are advisory. */}
      {(result.errors.length > 0 || result.warnings.length > 0) && (
        <div className="arbor-canvas-validation" data-testid="canvas-validation">
          {result.errors.map((msg, i) => (
            <p key={`e${i}`} className="arbor-canvas-error" data-testid="canvas-error">
              {msg}
            </p>
          ))}
          {result.warnings.map((msg, i) => (
            <p key={`w${i}`} className="arbor-canvas-warning" data-testid="canvas-warning">
              {msg}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}
