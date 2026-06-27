// One tree row: indentation by depth, expand/collapse chevron for groups (a
// disabled spacer for leaves to preserve alignment, WEB_UI-003), the label cell
// (resolved from the is_label column value, WEB_UI-002), and one Cell per
// column. Drag/drop reports a (position) to the parent which computes moveNode.

import { useState } from "react";
import type { Snapshot, SnapshotColumn, SnapshotNode } from "../api";
import type { DropPosition, TreeRow as Row } from "../lib/tree";
import { Cell } from "./cells/Cell";
import { CornerDownRightIcon, GripVerticalIcon, PencilIcon, PlusIcon, TrashIcon } from "./icons";

export function TreeRow({
  row,
  columns,
  labelColumn,
  collapsed,
  pendingCell,
  pendingTitle,
  pendingCount,
  draftCell,
  pendingMove,
  onToggle,
  onCommitCell,
  onDragStart,
  onDrop,
  onAddChild,
  onAddSibling,
  onEdit,
  onDelete,
  editSignal,
}: {
  row: Row;
  columns: SnapshotColumn[];
  labelColumn: string | null;
  collapsed: boolean;
  pendingCell: (node: string, column: string) => boolean;
  pendingTitle?: (node: string, column: string) => string | undefined;
  pendingCount?: (node: string, column: string) => number;
  // Draft flow — does this cell carry an unsubmitted local draft?
  draftCell?: (node: string, column: string) => boolean;
  pendingMove: boolean;
  // External edit signal for THIS row's label cell — bumped by the parent when
  // the edit-pencil is clicked for this node, opening its inline label editor.
  editSignal?: number;
  onToggle: (node: string) => void;
  onCommitCell: (node: SnapshotNode, column: SnapshotColumn, value: unknown) => void;
  onDragStart: (node: SnapshotNode) => void;
  onDrop: (target: SnapshotNode, position: DropPosition) => void;
  // Add a child under this node. Optional; rendered for EVERYONE when supplied
  // (a non-owner click files a CR, same as "Suggest column") — NOT gated on
  // can_change_structure, unlike delete.
  onAddChild?: (node: SnapshotNode) => void;
  // Add a SIBLING of this node (a new node under the same parent). Optional;
  // rendered for EVERYONE when supplied (a non-owner click files a CR), exactly
  // like add-child — NOT gated on can_change_structure.
  onAddSibling?: (node: SnapshotNode) => void;
  // Put this node's LABEL cell into inline edit. Optional; rendered for EVERYONE
  // when supplied (a non-owner's commit files a CR, like any cell edit) — NOT
  // gated on can_change_structure.
  onEdit?: (node: SnapshotNode) => void;
  // Delete this node (two-step confirm). Optional; rendered only when supplied
  // AND the viewer holds structural authority over the node.
  onDelete?: (node: SnapshotNode) => void;
}): JSX.Element {
  const { node, depth, hasChildren } = row;
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Determine drop position from cursor offset within the row's height.
  const positionFromEvent = (e: React.DragEvent<HTMLTableRowElement>): DropPosition => {
    const rect = e.currentTarget.getBoundingClientRect();
    const y = e.clientY - rect.top;
    const third = rect.height / 3;
    if (y < third) return "before";
    if (y > rect.height - third) return "after";
    // Middle band re-parents (drop INTO the row), making it a parent even when it
    // is currently a leaf; the top/bottom thirds reorder as siblings.
    return "inside";
  };

  const labelCol = labelColumn ? columns.find((c) => c.name === labelColumn) : undefined;
  const labelText = labelColumn ? renderLabel(node, labelColumn) : node.name;

  return (
    <tr
      className="arbor-row"
      data-testid={`row-${node.name}`}
      data-depth={depth}
      data-pending-move={pendingMove ? "true" : undefined}
      data-pending-delete={confirmDelete ? "true" : undefined}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        onDrop(node, positionFromEvent(e));
      }}
    >
      <td className="arbor-label-cell">
        {/* Explicit drag handle: ONLY this grip starts a reorder, so a plain
            single click on a cell edits without fighting row drag. The row stays
            the drop target (onDragOver/onDrop above) but is no longer draggable
            itself. Hover-revealed like the action cluster; keyboard-focusable. */}
        <span
          className="arbor-drag-handle"
          data-testid={`drag-handle-${node.name}`}
          draggable
          role="button"
          tabIndex={0}
          aria-label={`Drag to reorder ${labelText}`}
          title="Drag to reorder"
          onDragStart={(e) => {
            e.stopPropagation();
            onDragStart(node);
          }}
        >
          <GripVerticalIcon size={14} />
        </span>
        <span style={{ paddingLeft: depth * 16 }} className="arbor-indent" />
        {hasChildren ? (
          <button
            type="button"
            className="arbor-chevron"
            data-testid={`chevron-${node.name}`}
            aria-label={collapsed ? "Expand" : "Collapse"}
            aria-expanded={!collapsed}
            onClick={() => onToggle(node.name)}
          >
            {collapsed ? "▶" : "▼"}
          </button>
        ) : (
          <span className="arbor-chevron-spacer" data-testid={`spacer-${node.name}`} />
        )}
        {labelCol ? (
          <span className="arbor-label" data-testid={`label-${node.name}`}>
            <Cell
              column={labelCol}
              value={node.values[labelCol.name]}
              pending={pendingCell(node.name, labelCol.name)}
              pendingTitle={pendingTitle?.(node.name, labelCol.name)}
              pendingCount={pendingCount?.(node.name, labelCol.name)}
              draft={draftCell?.(node.name, labelCol.name)}
              startEditing={editSignal}
              onCommit={(v) => onCommitCell(node, labelCol, v)}
            />
          </span>
        ) : (
          <span data-testid={`label-${node.name}`}>{labelText}</span>
        )}
        {/* Per-row action cluster lives INSIDE the frozen-left label cell so it
            is always reachable with zero horizontal scroll (the trailing actions
            column sat ~2184px off-screen). Right-aligned (margin-left:auto),
            hover/focus-revealed, with an opaque backdrop so it reads cleanly over
            the label. Order: +sibling, +child, edit, delete. */}
        {(onAddSibling || onAddChild || onEdit || onDelete) && (
          <span className="arbor-row-actions">
            {onAddSibling && (
              <button
                type="button"
                className="arbor-row-add"
                data-testid={`add-sibling-${node.name}`}
                title="Add sibling"
                aria-label={`Add sibling of ${labelText}`}
                onClick={() => onAddSibling(node)}
              >
                <PlusIcon size={14} />
              </button>
            )}
            {onAddChild && (
              <button
                type="button"
                className="arbor-row-add"
                data-testid={`add-child-${node.name}`}
                title="Add child"
                aria-label={`Add child under ${labelText}`}
                onClick={() => onAddChild(node)}
              >
                <CornerDownRightIcon size={14} />
              </button>
            )}
            {onEdit && (
              <button
                type="button"
                className="arbor-row-edit"
                data-testid={`edit-node-${node.name}`}
                title="Edit name"
                aria-label={`Edit ${labelText}`}
                onClick={() => onEdit(node)}
              >
                <PencilIcon size={14} />
              </button>
            )}
            {onDelete &&
              node.can_change_structure &&
              (!confirmDelete ? (
                <button
                  type="button"
                  className="arbor-row-delete"
                  data-testid={`delete-node-${node.name}`}
                  title="Delete node"
                  aria-label={`Delete ${labelText}`}
                  onClick={() => setConfirmDelete(true)}
                >
                  <TrashIcon size={14} />
                </button>
              ) : (
                <span className="arbor-row-confirm">
                  <span className="arbor-row-confirm-label">Delete {labelText}?</span>
                  <button
                    type="button"
                    className="arbor-row-delete-confirm"
                    data-testid={`delete-node-confirm-${node.name}`}
                    onClick={() => {
                      setConfirmDelete(false);
                      onDelete(node);
                    }}
                  >
                    Delete
                  </button>
                  <button
                    type="button"
                    className="arbor-row-delete-cancel"
                    aria-label="Cancel delete"
                    onClick={() => setConfirmDelete(false)}
                  >
                    Cancel
                  </button>
                </span>
              ))}
          </span>
        )}
      </td>
      {columns
        .filter((c) => !c.is_label)
        .map((c) => {
          const v = node.values[c.name];
          const isSplit =
            c.type === "single-select-split" || c.type === "multi-select-split";
          const empty = v == null || (Array.isArray(v) && v.length === 0);
          // Aggregate/parent rows don't carry a single per-node status; show a
          // quiet placeholder instead of an empty toggle so the column stays calm
          // and the parent-vs-leaf hierarchy reads clearly.
          const placeholder = hasChildren && isSplit && empty;
          return (
            <td
              key={c.name}
              className={`arbor-data-cell${c.type === "number" ? " is-numeric" : ""}`}
              data-column={c.name}
            >
              {placeholder ? (
                <span className="arbor-cell-empty" aria-hidden>
                  —
                </span>
              ) : (
                <Cell
                  column={c}
                  value={v}
                  pending={pendingCell(node.name, c.name)}
                  pendingTitle={pendingTitle?.(node.name, c.name)}
                  pendingCount={pendingCount?.(node.name, c.name)}
                  draft={draftCell?.(node.name, c.name)}
                  onCommit={(val) => onCommitCell(node, c, val)}
                />
              )}
            </td>
          );
        })}
    </tr>
  );
}

function renderLabel(node: SnapshotNode, labelColumn: string): string {
  const v = node.values[labelColumn] ?? node.label;
  if (v == null) return node.name; // fallback to node id, never a hardcoded field
  return Array.isArray(v) ? v.join(", ") : String(v);
}

// Re-export so a caller importing the row need not also import the snapshot type.
export type { Snapshot };
