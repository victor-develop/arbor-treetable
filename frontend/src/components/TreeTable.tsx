// The snapshot-driven TreeTable. Renders rows in NestedSet order, manages
// expand/collapse (local view state — never an executeAction, WEB_UI-006), and
// routes every cell commit / drag-drop move into executeAction via the shared
// dispatch. Move params are computed purely (computeMove) and illegal drops are
// suppressed before any round-trip (WEB_UI-044/-045).

import { useRef, useState } from "react";
import type { SnapshotColumn, SnapshotNode } from "../api";
import { buildVisibleRows, computeMove, type DropPosition } from "../lib/tree";
import { TreeRow } from "./TreeRow";
import { GearIcon } from "./icons";

export type TreeTableProps = {
  columns: SnapshotColumn[];
  nodes: SnapshotNode[];
  labelColumn: string | null;
  collapsed: Set<string>;
  onToggle: (node: string) => void;
  // Per-cell pending suggestion: boolean marker + an optional tooltip string
  // ("N pending · <requester> → <value>"), both keyed by (node, column).
  pendingCell: (node: string, column: string) => boolean;
  pendingTitle?: (node: string, column: string) => string | undefined;
  // How many open suggestions target this cell (for the count badge).
  pendingCount?: (node: string, column: string) => number;
  isPendingMove: (node: string) => boolean;
  onCommitCell: (node: SnapshotNode, column: SnapshotColumn, value: unknown) => void;
  onMove: (params: { node: string; new_parent: string | null; after: string | null }) => void;
  // Open the schema editor for a data column (configure / delete / reassign
  // ownership). Optional so seeded/standalone renders need not supply it.
  onColumnSettings?: (column: SnapshotColumn) => void;
  // Delete a node (two-step confirm in the row). Optional.
  onDeleteNode?: (node: SnapshotNode) => void;
};

export function TreeTable(props: TreeTableProps): JSX.Element {
  const {
    columns,
    nodes,
    labelColumn,
    collapsed,
    onToggle,
    pendingCell,
    pendingTitle,
    pendingCount,
    isPendingMove,
    onCommitCell,
    onMove,
    onColumnSettings,
    onDeleteNode,
  } = props;

  const dragged = useRef<SnapshotNode | null>(null);
  const [, force] = useState(0);

  const rows = buildVisibleRows(nodes, collapsed);
  const dataColumns = columns.filter((c) => !c.is_label);

  const handleDrop = (target: SnapshotNode, position: DropPosition) => {
    const src = dragged.current;
    dragged.current = null;
    force((n) => n + 1);
    if (!src) return;
    const move = computeMove(src, target, position, nodes);
    if (!move) return; // illegal (cycle/self) — no executeAction (WEB_UI-044/045)
    onMove(move);
  };

  if (rows.length === 0) {
    return (
      <div className="arbor-empty" data-testid="empty-state">
        No nodes yet.
      </div>
    );
  }

  return (
    <table className="arbor-tree" data-testid="tree-table">
      <thead>
        <tr>
          <th className="arbor-label-head">
            {labelColumn ? columns.find((c) => c.name === labelColumn)?.label : "Name"}
          </th>
          {dataColumns.map((c) => (
            <th
              key={c.name}
              data-testid={`col-head-${c.name}`}
              className={c.type === "number" ? "is-numeric" : undefined}
              style={{ width: c.width }}
            >
              <span className="arbor-col-head">
                {c.label}
                {onColumnSettings && (
                  <button
                    type="button"
                    className="arbor-col-settings-open"
                    data-testid={`col-settings-open-${c.name}`}
                    title={`Configure ${c.label}`}
                    aria-label={`Configure ${c.label}`}
                    onClick={() => onColumnSettings(c)}
                  >
                    <GearIcon size={14} />
                  </button>
                )}
              </span>
            </th>
          ))}
          {onDeleteNode && <th className="arbor-actions-head" aria-label="Actions" />}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <TreeRow
            key={row.node.name}
            row={row}
            columns={columns}
            labelColumn={labelColumn}
            collapsed={collapsed.has(row.node.name)}
            pendingCell={pendingCell}
            pendingTitle={pendingTitle}
            pendingCount={pendingCount}
            pendingMove={isPendingMove(row.node.name)}
            onToggle={onToggle}
            onCommitCell={onCommitCell}
            onDragStart={(n) => {
              dragged.current = n;
            }}
            onDrop={handleDrop}
            onDelete={onDeleteNode}
          />
        ))}
      </tbody>
    </table>
  );
}
