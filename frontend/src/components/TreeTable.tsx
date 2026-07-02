// The snapshot-driven TreeTable. Renders rows in NestedSet order, manages
// expand/collapse (local view state — never an executeAction, WEB_UI-006), and
// routes every cell commit / drag-drop move into executeAction via the shared
// dispatch. Move params are computed purely (computeMove) and illegal drops are
// suppressed before any round-trip (WEB_UI-044/-045).

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { SnapshotColumn, SnapshotNode } from "../api";
import { buildVisibleRows, computeMove, type DropPosition } from "../lib/tree";
import { TreeRow } from "./TreeRow";
import { GearIcon, PlusIcon } from "./icons";

// Whether a horizontally-scrolling viewport is clipping content to the left
// and/or right of the current scroll position. Pure + exported so the
// scroll-shadow logic is unit-testable without a DOM. A 1px epsilon absorbs
// sub-pixel scrollWidth/clientWidth rounding so the right cue reliably clears
// at the true end of travel (UX P1-3).
export type OverflowMetrics = { scrollLeft: number; clientWidth: number; scrollWidth: number };
export type OverflowState = { left: boolean; right: boolean };
export function computeOverflowState(m: OverflowMetrics): OverflowState {
  const EPS = 1;
  const maxScroll = m.scrollWidth - m.clientWidth;
  // Nothing overflows (table fits): no cues either side.
  if (maxScroll <= EPS) return { left: false, right: false };
  return {
    left: m.scrollLeft > EPS,
    right: m.scrollLeft < maxScroll - EPS,
  };
}

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
  // Draft flow — does this cell carry an UNSUBMITTED local draft? Drives the
  // cell's "unsaved draft" treatment (distinct from the pending dot). Optional so
  // seeded/standalone renders need not supply it.
  draftCell?: (node: string, column: string) => boolean;
  isPendingMove: (node: string) => boolean;
  onCommitCell: (node: SnapshotNode, column: SnapshotColumn, value: unknown) => void;
  onMove: (params: { node: string; new_parent: string | null; after: string | null }) => void;
  // Open the schema editor for a data column (configure / delete / reassign
  // ownership). Optional so seeded/standalone renders need not supply it.
  onColumnSettings?: (column: SnapshotColumn) => void;
  // Delete a node (two-step confirm in the row). Optional.
  onDeleteNode?: (node: SnapshotNode) => void;
  // Add a child under a given node (per-row "+" affordance). Optional; shown
  // for everyone (a non-owner click files a CR) — NOT gated on ownership.
  onAddChild?: (node: SnapshotNode) => void;
  // Add a SIBLING of a given node (a new node under that node's parent).
  // Optional; shown for everyone (a non-owner click files a CR) — NOT gated.
  onAddSibling?: (node: SnapshotNode) => void;
  // Put a node's LABEL cell into inline edit (the per-row edit-pencil). Optional;
  // shown for everyone (a non-owner's commit files a CR) — NOT gated.
  onEdit?: (node: SnapshotNode) => void;
  // Which node's label cell should currently open its inline editor, plus a
  // monotonic signal the shell bumps on each edit-pencil click. The matching
  // row receives the signal so its label Cell enters edit mode + focuses.
  editingNode?: string | null;
  editSignal?: number;
  // Add a ROOT-level node (parent=null) — the toolbar "+ Add node" button.
  onAddNode?: () => void;
  // Proposed-view READ-ONLY preview: no drag handle, no per-row action cluster,
  // no cell editing (Cells render static). Chevron expand/collapse still works.
  preview?: boolean;
  // In preview, does this (node, column) cell show a PROPOSED value? Drives the
  // Cell's distinct "proposed" treatment.
  proposedCell?: (node: string, column: string) => boolean;
  // In preview, was this node relocated by an open move CR? Drives the row's
  // "moved · proposed" tag.
  movedNode?: (node: string) => boolean;
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
    draftCell,
    isPendingMove,
    onCommitCell,
    onMove,
    onColumnSettings,
    onDeleteNode,
    onAddChild,
    onAddSibling,
    onEdit,
    editingNode,
    editSignal,
    onAddNode,
    preview,
    proposedCell,
    movedNode,
  } = props;

  const dragged = useRef<SnapshotNode | null>(null);
  const [, force] = useState(0);
  // Live drop indicator: which row the drag is currently over + where it would
  // land (before / inside / after), so a horizontal line (or "drop-into" tint)
  // shows the destination clearly instead of leaving the user guessing.
  const [dropHint, setDropHint] = useState<{ node: string; pos: DropPosition } | null>(null);
  const clearDropHint = useCallback(() => setDropHint(null), []);

  // Right/left scroll-shadow affordance: a soft fading overlay on whichever
  // edge is clipping columns, so a wide matrix doesn't silently lose columns off
  // the right with no signifier. Toggled via data-overflow-* attributes the CSS
  // ::before/::after gradients key off (UX P1-3).
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const syncOverflow = useCallback(() => {
    const el = viewportRef.current;
    if (!el) return;
    const { left, right } = computeOverflowState({
      scrollLeft: el.scrollLeft,
      clientWidth: el.clientWidth,
      scrollWidth: el.scrollWidth,
    });
    el.toggleAttribute("data-overflow-left", left);
    el.toggleAttribute("data-overflow-right", right);
  }, []);
  // Initialize on mount/layout (after the table measures) and keep in sync on
  // window resize. Scroll is handled inline on the element (see onScroll below).
  useLayoutEffect(syncOverflow);
  useEffect(() => {
    window.addEventListener("resize", syncOverflow);
    return () => window.removeEventListener("resize", syncOverflow);
  }, [syncOverflow]);

  const rows = buildVisibleRows(nodes, collapsed);
  const dataColumns = columns.filter((c) => !c.is_label);

  // Predictable per-type column widths (a user-resized width from the view wins).
  // With table-layout:fixed + a horizontal-scroll viewport, the table grows as
  // wide as its columns and the viewport scrolls — so a 16-column matrix stays
  // fully reachable instead of being squeezed to fit (UX review D1/D3).
  const colWidth = (c: SnapshotColumn): number => {
    if (c.width) return c.width;
    switch (c.type) {
      case "number": return 104;
      case "multiline-text": return 300;
      case "single-select-split":
      case "multi-select-split": return 184;
      default: return 160;
    }
  };

  const handleDrop = (target: SnapshotNode, position: DropPosition) => {
    const src = dragged.current;
    dragged.current = null;
    setDropHint(null);
    force((n) => n + 1);
    if (!src) return;
    const move = computeMove(src, target, position, nodes);
    if (!move) return; // illegal (cycle/self) — no executeAction (WEB_UI-044/045)
    onMove(move);
  };

  // Update the live drop indicator as the cursor moves over a row. Suppress the
  // hint for an illegal target (dropping a node onto itself/a descendant) so the
  // line never promises a move computeMove would reject.
  const handleDragOver = (target: SnapshotNode, position: DropPosition) => {
    const src = dragged.current;
    if (src && !computeMove(src, target, position, nodes)) {
      setDropHint(null);
      return;
    }
    setDropHint((h) =>
      h && h.node === target.name && h.pos === position ? h : { node: target.name, pos: position },
    );
  };

  // Root-level "+ Add node" affordance (parent=null). Shown for everyone, like
  // Suggest column — a non-owner click files a CR. Reachable even with zero rows.
  const rootAdd = onAddNode ? (
    <div className="arbor-tree-toolbar">
      <button
        type="button"
        className="arbor-add-node"
        data-testid="add-root-node"
        onClick={() => onAddNode()}
      >
        <PlusIcon size={14} />
        <span>Add node</span>
      </button>
    </div>
  ) : null;

  if (rows.length === 0) {
    return (
      <>
        {rootAdd}
        <div className="arbor-empty" data-testid="empty-state">
          No nodes yet.
        </div>
      </>
    );
  }

  return (
   <>
   {rootAdd}
   <div
     className="arbor-table-viewport"
     data-testid="table-viewport"
     ref={viewportRef}
     onScroll={syncOverflow}
   >
    <table className="arbor-tree" data-testid="tree-table">
      <colgroup>
        <col className="arbor-col-label" />
        {dataColumns.map((c) => (
          <col key={c.name} style={{ width: colWidth(c) }} />
        ))}
      </colgroup>
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
            draftCell={draftCell}
            pendingMove={isPendingMove(row.node.name)}
            onToggle={onToggle}
            onCommitCell={onCommitCell}
            onDragStart={(n) => {
              dragged.current = n;
            }}
            onDragOverRow={handleDragOver}
            onDragEnd={clearDropHint}
            dropPosition={dropHint?.node === row.node.name ? dropHint.pos : null}
            onDrop={handleDrop}
            // Preview is READ-ONLY: withhold every mutating affordance (the drag
            // handle, the +sibling/+child/edit/delete cluster). Chevron toggle +
            // proposed-value styling + the moved tag are what preview keeps.
            onAddChild={preview ? undefined : onAddChild}
            onAddSibling={preview ? undefined : onAddSibling}
            onEdit={preview ? undefined : onEdit}
            onDelete={preview ? undefined : onDeleteNode}
            editSignal={editingNode === row.node.name ? editSignal : undefined}
            preview={preview}
            proposedCell={proposedCell}
            moved={preview ? movedNode?.(row.node.name) : undefined}
          />
        ))}
      </tbody>
    </table>
   </div>
   </>
  );
}
