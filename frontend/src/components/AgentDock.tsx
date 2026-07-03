// The floating agent widget — a bubble pinned bottom-right that opens a popup
// panel wrapping AgentSidebar. Extracted from App so BOTH the sheet view (App)
// and the home page (SheetList, workspace mode) mount the SAME widget with the
// same z-index/stacking. The table/list always keeps full width — no docked
// column. The sidebar stays MOUNTED (the popup is CSS-hidden when closed) so the
// transcript survives close/reopen.

import { useState } from "react";
import type { AgentFrame, ArborClient } from "../api";
import { AgentSidebar } from "./AgentSidebar";

export function AgentDock({
  client,
  sheet,
  onCrChip,
  onActionObserved,
  onSheetCreated,
}: {
  client: ArborClient;
  // A string sheet → sheet-scoped session (App); null → workspace session
  // (SheetList home page). Threaded straight through to AgentSidebar.
  sheet: string | null;
  onCrChip?: (changeRequest: string) => void;
  onActionObserved?: (frame: Extract<AgentFrame, { type: "observation" }>) => void;
  onSheetCreated?: (sheet: string) => void;
}): JSX.Element {
  // open toggles the popup; the sidebar stays MOUNTED (CSS-hidden) so the
  // transcript survives close/reopen. Same pattern on desktop and mobile.
  const [open, setOpen] = useState(false);

  return (
    <div className={`arbor-agent-dock${open ? " is-open" : ""}`} data-testid="agent-dock">
      <div className="arbor-agent-popup" role="dialog" aria-label="Agent panel">
        <AgentSidebar
          client={client}
          sheet={sheet}
          onCrChip={onCrChip}
          onActionObserved={onActionObserved}
          onSheetCreated={onSheetCreated}
        />
      </div>
      <button
        type="button"
        className="arbor-agent-fab"
        data-testid="agent-fab"
        aria-expanded={open}
        aria-label={open ? "Close agent" : "Ask the agent"}
        title={open ? "Close agent" : "Ask the agent"}
        onClick={() => setOpen((o) => !o)}
      >
        {open ? (
          <span className="arbor-fab-glyph" aria-hidden="true">
            ✕
          </span>
        ) : (
          <svg
            className="arbor-fab-glyph"
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
        )}
      </button>
    </div>
  );
}
