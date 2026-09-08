// Feature 2 (shareable views) — ViewMenu. Presentation-only: lists the
// snapshot-present (already read-ACL-filtered) NON-label columns and lets the
// user toggle visibility, reorder, and resize. EVERY interaction emits a new
// SheetView via onChange and issues ZERO executeAction calls — views never
// mutate the backend (the optional `client` prop exists only so tests can assert
// it is never touched).

import { useState } from "react";
import type { SnapshotColumn, ArborClient } from "../api";
import { resolveColumnOrder, type SheetView } from "../lib/view";

// Pure reorder helper shared by the drag-and-drop path: move `from` so it lands
// at `to`'s current slot, returning a NEW array. Factored out so reordering has
// exactly ONE definition and is trivially unit-testable. No-ops when either name
// is absent or from === to. The ↑/↓ buttons keep their own adjacent-swap path.
export function reorderByDrag(
  order: string[],
  from: string,
  to: string,
): string[] {
  if (from === to) return order.slice();
  const fromIdx = order.indexOf(from);
  const toIdx = order.indexOf(to);
  if (fromIdx < 0 || toIdx < 0) return order.slice();
  const next = order.slice();
  next.splice(fromIdx, 1);
  next.splice(next.indexOf(to) + (toIdx > fromIdx ? 1 : 0), 0, from);
  return next;
}

export type ViewMenuProps = {
  columns: SnapshotColumn[];
  view: SheetView;
  onChange: (view: SheetView) => void;
  // Presence-only: ViewMenu NEVER calls this. Tests pass a spy to prove views
  // are mutation-free.
  client?: ArborClient;
  // "Save for everyone": promote the viewer's arrangement to the SHARED stored
  // order. ViewMenu still issues zero executeAction — it hands the host the
  // resolved left-to-right order (complete, non-label, column ids, exactly what
  // setColumnOrder requires) and the HOST dispatches. Omitted => no button.
  onSaveSharedOrder?: (order: string[]) => void;
};

export function ViewMenu(props: ViewMenuProps): JSX.Element {
  const { columns, view, onChange, onSaveSharedOrder } = props;
  // Only NON-label, snapshot-present columns are user-configurable (the label is
  // always visible and never reorderable).
  const dataColumns = columns.filter((c) => !c.is_label);

  // Render order = the view's order intersected with present data columns, then
  // any remaining data columns in snapshot order (so a newly-appeared column
  // still shows up to be configured). resolveColumnOrder is that one rule,
  // shared with the grid's header drag and with "save for everyone".
  const present = new Map(dataColumns.map((c) => [c.name, c]));
  const ordered: SnapshotColumn[] = resolveColumnOrder(columns, view)
    .map((name) => present.get(name))
    .filter((c): c is SnapshotColumn => c !== undefined);

  const hidden = new Set(view.hidden);

  const orderNames = (): string[] => ordered.map((c) => c.name);

  // Native HTML5 DnD state: the name being dragged and the row hovered as a drop
  // target (drives the drop-indicator border). Reordering flows through the SAME
  // order-emitting path as the ↑/↓ buttons — reorderByDrag → onChange.
  const [dragName, setDragName] = useState<string | null>(null);
  const [overName, setOverName] = useState<string | null>(null);

  const dropOnto = (target: string): void => {
    const from = dragName;
    setDragName(null);
    setOverName(null);
    if (!from) return;
    const next = reorderByDrag(orderNames(), from, target);
    if (next.some((n, k) => n !== orderNames()[k])) {
      onChange({ ...view, order: next });
    }
  };

  const toggle = (name: string): void => {
    const next = new Set(hidden);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    onChange({ ...view, hidden: Array.from(next) });
  };

  const move = (name: string, dir: -1 | 1): void => {
    const order = orderNames();
    const i = order.indexOf(name);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    onChange({ ...view, order });
  };

  const setWidth = (name: string, raw: string): void => {
    const next = { ...(view.width ?? {}) };
    const n = Number(raw);
    if (raw === "" || Number.isNaN(n)) delete next[name];
    else next[name] = n;
    onChange({ ...view, width: next });
  };

  // "Save for everyone" is offered ONLY when it would do something: the local
  // arrangement has to actually differ from the shared (snapshot) order. A dead
  // button that silently no-ops is worse than no button. `ordered` is the
  // COMPLETE non-label set (hidden columns included) — the shared order is a
  // stored property of the sheet, not a function of what this viewer hides.
  const sharedOrder = dataColumns.map((c) => c.name);
  const localOrder = orderNames();
  const differsFromShared = localOrder.some((n, i) => n !== sharedOrder[i]);

  return (
    <div className="arbor-view-menu" data-testid="view-menu">
      {onSaveSharedOrder && differsFromShared && (
        <div className="arbor-view-share">
          <button
            type="button"
            className="arbor-view-save-order"
            data-testid="view-save-order"
            title="Make this column order the one everyone sees"
            onClick={() => onSaveSharedOrder(localOrder)}
          >
            Save order for everyone
          </button>
        </div>
      )}
      <ul className="arbor-view-cols">
        {ordered.map((c, i) => (
          <li
            key={c.name}
            className={
              "arbor-view-col" +
              (dragName === c.name ? " is-dragging" : "") +
              (overName === c.name && dragName && dragName !== c.name
                ? " is-drop-target"
                : "")
            }
            data-testid={`view-col-${c.name}`}
            draggable
            onDragStart={(e) => {
              setDragName(c.name);
              // dataTransfer is absent under jsdom; guard so handlers never throw.
              if (e.dataTransfer) {
                e.dataTransfer.effectAllowed = "move";
                // Firefox refuses to start a drag without payload set.
                e.dataTransfer.setData("text/plain", c.name);
              }
            }}
            onDragEnter={() => setOverName(c.name)}
            onDragOver={(e) => {
              e.preventDefault();
              if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
            }}
            onDrop={(e) => {
              e.preventDefault();
              dropOnto(c.name);
            }}
            onDragEnd={() => {
              setDragName(null);
              setOverName(null);
            }}
          >
            <span
              className="arbor-view-handle"
              data-testid={`view-handle-${c.name}`}
              aria-hidden="true"
              title={`Drag to reorder ${c.label}`}
            >
              ⠿
            </span>
            <button
              type="button"
              className="arbor-view-toggle"
              data-testid={`view-toggle-${c.name}`}
              aria-pressed={!hidden.has(c.name)}
              // The visible text is just the bare label; visible/hidden state is
              // conveyed structurally by the .arbor-view-check checkbox and
              // aria-pressed. An explicit aria-label keeps the state in the
              // accessible name for screen-reader users (the bare visible word
              // would be redundant on screen and was only ever present to keep
              // RTL getByText("Budget") off this node — tests now scope to the
              // table header instead).
              aria-label={`${c.label} — ${hidden.has(c.name) ? "hidden" : "visible"}`}
              title={hidden.has(c.name) ? `Show ${c.label}` : `Hide ${c.label}`}
              onClick={() => toggle(c.name)}
            >
              <span className="arbor-view-check" aria-hidden="true" />
              <span className="arbor-view-label">{c.label}</span>
            </button>
            <button
              type="button"
              className="arbor-view-up"
              data-testid={`view-up-${c.name}`}
              aria-label={`Move ${c.label} up`}
              disabled={i === 0}
              onClick={() => move(c.name, -1)}
            >
              ↑
            </button>
            <button
              type="button"
              className="arbor-view-down"
              data-testid={`view-down-${c.name}`}
              aria-label={`Move ${c.label} down`}
              disabled={i === ordered.length - 1}
              onClick={() => move(c.name, 1)}
            >
              ↓
            </button>
            <input
              type="number"
              className="arbor-view-width"
              data-testid={`view-width-${c.name}`}
              aria-label={`Width of ${c.label}`}
              placeholder="auto"
              min={40}
              value={view.width?.[c.name] ?? ""}
              onChange={(e) => setWidth(c.name, e.currentTarget.value)}
            />
          </li>
        ))}
      </ul>
    </div>
  );
}
