// Pure tree helpers — build the visible row set from a snapshot, compute
// drop-target geometry for drag-and-drop moves, and detect illegal moves
// (cycle prevention). No React, no network — unit-testable in isolation.

import type { SnapshotNode } from "../api";

export type TreeRow = {
  node: SnapshotNode;
  depth: number;
  hasChildren: boolean;
};

// Order nodes depth-first in NestedSet order (lft ascending == DFS preorder),
// then filter out any node whose ancestor is collapsed. Depth = ancestor count.
export function buildVisibleRows(
  nodes: SnapshotNode[],
  collapsed: ReadonlySet<string>,
): TreeRow[] {
  const byName = new Map(nodes.map((n) => [n.name, n]));
  // DFS preorder, but siblings under the same parent are ordered by `idx` (user
  // reordering), falling back to `lft` when idx ties. lft alone only encodes
  // nesting + a name-based sibling order, so a same-parent reorder isn't visible
  // through lft (NestedSet rebuilds sibling order by name, not idx).
  const sib = (a: SnapshotNode, b: SnapshotNode) =>
    (a.idx ?? 0) - (b.idx ?? 0) || a.lft - b.lft;
  const childrenOf = (parent: string | null) =>
    nodes.filter((n) => (n.parent ?? null) === parent).sort(sib);
  const ordered: SnapshotNode[] = [];
  const walk = (parent: string | null) => {
    for (const n of childrenOf(parent)) {
      ordered.push(n);
      walk(n.name);
    }
  };
  walk(null);

  const depthOf = (n: SnapshotNode): number => {
    let d = 0;
    let cur: SnapshotNode | undefined = n;
    while (cur && cur.parent) {
      cur = byName.get(cur.parent);
      d += 1;
    }
    return d;
  };

  const hasChildren = (n: SnapshotNode): boolean =>
    n.is_group ?? ordered.some((m) => m.parent === n.name);

  const isHidden = (n: SnapshotNode): boolean => {
    let cur = n.parent ? byName.get(n.parent) : undefined;
    while (cur) {
      if (collapsed.has(cur.name)) return true;
      cur = cur.parent ? byName.get(cur.parent) : undefined;
    }
    return false;
  };

  return ordered
    .filter((n) => !isHidden(n))
    .map((n) => ({ node: n, depth: depthOf(n), hasChildren: hasChildren(n) }));
}

export type DropPosition = "before" | "inside" | "after";

// Is `target` a descendant of (or equal to) `dragged`? A move into one's own
// subtree (or onto self) would create a cycle and is rejected client-side
// before any round-trip (WEB_UI-044, WEB_UI-045). Uses NestedSet ranges.
export function isDescendantOrSelf(dragged: SnapshotNode, target: SnapshotNode): boolean {
  return target.lft >= dragged.lft && target.rgt <= dragged.rgt;
}

export type MoveParams = {
  node: string;
  new_parent: string | null;
  after: string | null;
};

// Translate a (dragged, target, position) drop into moveNode params.
// - inside  → new_parent = target, after = null (drop to head of target)
// - before  → new_parent = target.parent, after = previous sibling of target
//             (null when target is the first child = head)
// - after   → new_parent = target.parent, after = target
// Returns null when the move is illegal (cycle / self) so the caller suppresses
// the executeAction call entirely.
export function computeMove(
  dragged: SnapshotNode,
  target: SnapshotNode,
  position: DropPosition,
  nodes: SnapshotNode[],
): MoveParams | null {
  if (dragged.name === target.name) return null; // drop onto self → no-op
  if (isDescendantOrSelf(dragged, target)) return null; // cycle

  if (position === "inside") {
    return { node: dragged.name, new_parent: target.name, after: null };
  }

  const newParent = target.parent;
  const siblings = nodes
    .filter((n) => n.parent === newParent && n.name !== dragged.name)
    .sort((a, b) => (a.idx ?? a.lft) - (b.idx ?? b.lft));

  if (position === "after") {
    return { node: dragged.name, new_parent: newParent, after: target.name };
  }

  // before: after = the sibling immediately preceding target (null if head)
  const targetIdx = siblings.findIndex((n) => n.name === target.name);
  const prev = targetIdx > 0 ? siblings[targetIdx - 1] : null;
  return { node: dragged.name, new_parent: newParent, after: prev ? prev.name : null };
}

// Which drop positions a row offers: leaves cannot accept an "inside" drop
// (WEB_UI-039). A row is a group when is_group or it actually has children.
export function dropZonesFor(row: TreeRow): DropPosition[] {
  return row.hasChildren ? ["before", "inside", "after"] : ["before", "after"];
}
