// A tiny segmented control toggling the sheet between "Live" (today's editable
// view) and "Proposed" (a read-only overlay preview). Styled like the existing
// .arbor-density row-height control; aria-pressed marks the active mode.

export type ViewMode = "live" | "proposed";

export function ViewModeToggle({
  mode,
  onChange,
}: {
  mode: ViewMode;
  onChange: (mode: ViewMode) => void;
}): JSX.Element {
  return (
    <div
      className="arbor-view-mode"
      role="group"
      aria-label="View mode"
      data-testid="view-mode-toggle"
    >
      <button
        type="button"
        aria-pressed={mode === "live"}
        data-testid="view-mode-live"
        onClick={() => onChange("live")}
      >
        Live
      </button>
      <button
        type="button"
        aria-pressed={mode === "proposed"}
        data-testid="view-mode-proposed"
        onClick={() => onChange("proposed")}
      >
        Proposed
      </button>
    </div>
  );
}
