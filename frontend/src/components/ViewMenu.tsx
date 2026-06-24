// Feature 2 (shareable views) — ViewMenu. Presentation-only: lists the
// snapshot-present (already read-ACL-filtered) NON-label columns and lets the
// user toggle visibility, reorder, and resize. EVERY interaction emits a new
// SheetView via onChange and issues ZERO executeAction calls — views never
// mutate the backend (the optional `client` prop exists only so tests can assert
// it is never touched).

import type { SnapshotColumn, ArborClient } from "../api";
import type { SheetView } from "../lib/view";

export type ViewMenuProps = {
  columns: SnapshotColumn[];
  view: SheetView;
  onChange: (view: SheetView) => void;
  // Presence-only: ViewMenu NEVER calls this. Tests pass a spy to prove views
  // are mutation-free.
  client?: ArborClient;
};

export function ViewMenu(props: ViewMenuProps): JSX.Element {
  const { columns, view, onChange } = props;
  // Only NON-label, snapshot-present columns are user-configurable (the label is
  // always visible and never reorderable).
  const dataColumns = columns.filter((c) => !c.is_label);

  // Render order = the view's order intersected with present data columns, then
  // any remaining data columns in snapshot order (so a newly-appeared column
  // still shows up to be configured).
  const present = new Map(dataColumns.map((c) => [c.name, c]));
  const ordered: SnapshotColumn[] = [];
  const taken = new Set<string>();
  for (const name of view.order) {
    const c = present.get(name);
    if (c && !taken.has(name)) {
      ordered.push(c);
      taken.add(name);
    }
  }
  for (const c of dataColumns) {
    if (!taken.has(c.name)) {
      ordered.push(c);
      taken.add(c.name);
    }
  }

  const hidden = new Set(view.hidden);

  const orderNames = (): string[] => ordered.map((c) => c.name);

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

  return (
    <div className="arbor-view-menu" data-testid="view-menu">
      <ul className="arbor-view-cols">
        {ordered.map((c, i) => (
          <li
            key={c.name}
            className="arbor-view-col"
            data-testid={`view-col-${c.name}`}
          >
            <button
              type="button"
              className="arbor-view-toggle"
              data-testid={`view-toggle-${c.name}`}
              aria-pressed={!hidden.has(c.name)}
              title={hidden.has(c.name) ? `Show ${c.label}` : `Hide ${c.label}`}
              onClick={() => toggle(c.name)}
            >
              {hidden.has(c.name) ? "☐" : "☑"} {c.label}
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
