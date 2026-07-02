// The "Proposed" preview overlay — a PURE transform from the live snapshot +
// its open Change Requests into a hypothetical "if every proposal landed" tree.
// Two things are overlaid: (1) cell values from each node's server-authoritative
// (already ACL-filtered) `pending` marks, and (2) structural moves gathered from
// the open move CRs. The result feeds straight into buildVisibleRows, so the
// rebuilt NestedSet fields (parent/idx/lft/rgt) must be self-consistent.
//
// Never mutates the input (deep copy). Illegal moves (cycle / self / missing
// node or parent) are skipped, never thrown — a preview must always render.

import type { ChangeRequestView, SnapshotNode } from "../api";
import { cellKey } from "../hooks/useSheet";

export type OverlayResult = {
  nodes: SnapshotNode[];
  // cellKey(node, column) for every cell showing a proposed value.
  proposedCells: Set<string>;
  // node names relocated by an open move CR.
  movedNodes: Set<string>;
};

// One relocation gathered from a move CR: reparent `node` under `new_parent`,
// positioned immediately after `after` among the new siblings (null → head).
type Move = { node: string; new_parent: string | null; after: string | null };

// A payload is a "move" op iff it names a node + carries the moveNode marker
// (operation/target_kind hints OR the _action_id the backend stamps). We read
// defensively: any of the three signals qualifies it.
function moveFromPayload(
  operation: string | undefined,
  target_kind: string | undefined,
  payload: Record<string, unknown> | undefined,
): Move | null {
  if (!payload) return null;
  const isMove =
    operation === "move" ||
    target_kind === "node-structure" ||
    payload._action_id === "moveNode";
  if (!isMove) return null;
  const node = payload.node;
  if (typeof node !== "string") return null;
  const new_parent =
    typeof payload.new_parent === "string" ? payload.new_parent : null;
  const after = typeof payload.after === "string" ? payload.after : null;
  return { node, new_parent, after };
}

// Gather every move op from the OPEN CRs, in CR order then per-CR change order.
// Both the single-op top-level payload AND each multi-change changes[].payload
// are scanned (a CR may carry either shape).
function gatherMoves(crs: ChangeRequestView[]): Move[] {
  const moves: Move[] = [];
  for (const cr of crs) {
    if (cr.status !== "proposed") continue; // only OPEN proposals preview
    const top = moveFromPayload(cr.operation, cr.target_kind, cr.payload);
    if (top) moves.push(top);
    for (const ch of cr.changes ?? []) {
      const m = moveFromPayload(ch.operation, ch.target_kind, ch.payload);
      if (m) moves.push(m);
    }
  }
  return moves;
}

// Is `maybeAncestor` an ancestor of (or equal to) `node`, following the CURRENT
// (already-overlaid) parent pointers? Guards cycles/self before a reparent.
function isAncestorOrSelf(
  byName: Map<string, SnapshotNode>,
  maybeAncestor: string,
  node: string,
): boolean {
  let cur: string | null = node;
  while (cur) {
    if (cur === maybeAncestor) return true;
    cur = byName.get(cur)?.parent ?? null;
  }
  return false;
}

// Apply ONE move to the parent-pointer + idx model. Returns true when applied,
// false when the move is illegal / references a missing node and was skipped.
// idx is re-sequenced only among the destination's children so buildVisibleRows
// (which sorts siblings by idx then lft) renders the requested order.
function applyMove(byName: Map<string, SnapshotNode>, move: Move): boolean {
  const node = byName.get(move.node);
  if (!node) return false; // moved node gone
  if (move.new_parent !== null && !byName.has(move.new_parent)) return false; // parent gone
  if (move.new_parent === move.node) return false; // self-parent
  // Cycle: cannot move a node under its own descendant (or itself).
  if (move.new_parent !== null && isAncestorOrSelf(byName, move.node, move.new_parent)) {
    return false;
  }
  if (move.after !== null && !byName.has(move.after)) return false; // anchor gone

  node.parent = move.new_parent;

  // Re-index the destination sibling set so idx encodes the new order. Take the
  // current siblings (excluding the moved node), sort by their existing order,
  // then splice the moved node in right after `after` (or at the head).
  const siblings = [...byName.values()]
    .filter((n) => (n.parent ?? null) === (move.new_parent ?? null) && n.name !== move.node)
    .sort((a, b) => (a.idx ?? 0) - (b.idx ?? 0) || a.lft - b.lft);
  const anchorIdx = move.after === null ? -1 : siblings.findIndex((s) => s.name === move.after);
  siblings.splice(anchorIdx + 1, 0, node);
  siblings.forEach((s, i) => {
    s.idx = i;
  });
  return true;
}

// Rebuild lft/rgt (NestedSet) via a DFS preorder over the parent/idx model so
// isDescendantOrSelf + any lft-ordered consumer stay coherent. Siblings walk in
// idx order (idx was re-sequenced by applyMove; ties fall back to the prior lft).
function rebuildNestedSet(nodes: SnapshotNode[]): void {
  const childrenOf = (parent: string | null) =>
    nodes
      .filter((n) => (n.parent ?? null) === parent)
      .sort((a, b) => (a.idx ?? 0) - (b.idx ?? 0) || a.lft - b.lft);
  let counter = 0;
  const walk = (parent: string | null) => {
    for (const n of childrenOf(parent)) {
      n.lft = ++counter;
      walk(n.name);
      n.rgt = ++counter;
    }
  };
  walk(null);
}

export function applyProposedOverlay(
  nodes: SnapshotNode[],
  crs: ChangeRequestView[],
): OverlayResult {
  // Deep-copy so the live snapshot is never touched (values may be arrays/objects).
  const copy: SnapshotNode[] = nodes.map((n) => ({
    ...n,
    values: { ...n.values },
  }));
  const byName = new Map(copy.map((n) => [n.name, n]));
  const proposedCells = new Set<string>();
  const movedNodes = new Set<string>();

  // (1) Cell overlay — the LATEST pending mark's value per (node, column).
  for (const n of copy) {
    if (!n.pending) continue;
    for (const [col, marks] of Object.entries(n.pending)) {
      if (!marks || marks.length === 0) continue;
      const last = marks[marks.length - 1];
      n.values = { ...n.values, [col]: last.value };
      proposedCells.add(cellKey(n.name, col));
    }
  }

  // (2) Move overlay — apply each gathered move in order (last CR wins for a
  // node because a later apply overwrites the earlier reparent + re-index).
  const moves = gatherMoves(crs);
  for (const move of moves) {
    if (applyMove(byName, move)) movedNodes.add(move.node);
  }
  if (moves.length > 0) rebuildNestedSet(copy);

  return { nodes: copy, proposedCells, movedNodes };
}
