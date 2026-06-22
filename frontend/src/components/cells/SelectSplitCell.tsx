// Split-column cell (single/multi-select-split). Options come from the snapshot
// column config, never hardcoded (WEB_UI-031). Single enforces 1-cardinality;
// multi toggles independently. Owned columns commit directly; non-owned open
// suggest-mode — but the affordance gating is the caller's; this component just
// renders segments and reports the toggled array. a11y: radiogroup (single) /
// group + aria-pressed (multi) (WEB_UI-035).

import type { SelectOptions } from "../../api";
import { flattenOptions, toggleOption, unknownSelections } from "../../lib/cells";

export function SelectSplitCell({
  type,
  value,
  options,
  canEdit,
  onCommit,
}: {
  type: "single-select-split" | "multi-select-split";
  value: unknown;
  options?: SelectOptions | null;
  canEdit: boolean;
  onCommit: (next: string[]) => void;
}): JSX.Element {
  // A single-select value may arrive as a bare scalar (e.g. "done") rather than
  // an array; normalize so the matching segment renders as selected.
  const selected = Array.isArray(value)
    ? (value as string[])
    : value === null || value === undefined || value === ""
      ? []
      : [String(value)];
  const all = flattenOptions(options);
  const unknown = unknownSelections(value, options);
  const single = type === "single-select-split";

  const click = (opt: string) => {
    if (!canEdit) {
      // suggest-mode still produces an intent; caller decides routing
      onCommit(toggleOption(type, selected, opt));
      return;
    }
    onCommit(toggleOption(type, selected, opt));
  };

  return (
    <div
      role={single ? "radiogroup" : "group"}
      data-testid="split-cell"
      data-mode={canEdit ? "edit" : "suggest"}
      className={`arbor-split ${canEdit ? "is-editable" : "is-suggest"}`}
    >
      {all.map((opt) => {
        const active = selected.includes(opt);
        return (
          <button
            key={opt}
            type="button"
            role={single ? "radio" : undefined}
            aria-checked={single ? active : undefined}
            aria-pressed={single ? undefined : active}
            className={`arbor-segment ${active ? "is-active" : ""}`}
            data-testid={`segment-${opt}`}
            onClick={() => click(opt)}
          >
            {opt}
          </button>
        );
      })}
      {unknown.map((opt) => (
        <span
          key={`unknown-${opt}`}
          className="arbor-segment is-unknown"
          data-testid={`legacy-${opt}`}
          title="Value not in current options"
        >
          {opt} (legacy)
        </span>
      ))}
    </div>
  );
}
