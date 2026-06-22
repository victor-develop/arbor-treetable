// AI agent sidebar — a THIN shell over agent.chat (ARCHITECTURE §8). It streams
// Re-Act frames in arrival order and owns ZERO mutation logic: its only action
// network call is agent.chat (WEB_UI-068). Suggested observations link a CR chip
// that opens the same ChangeRequestPanel used elsewhere. The "what can the agent
// do" list excludes internalReset (WEB_UI-070).

import { useMemo, useState } from "react";
import type { AgentFrame, ArborClient } from "../api";
import { llmExposedCapabilities } from "../lib/capabilities";

export function AgentSidebar({
  client,
  sheet,
  onCrChip,
  onActionObserved,
}: {
  client: ArborClient;
  sheet: string;
  onCrChip?: (changeRequest: string) => void;
  // fired per executed/suggested observation so the host can refetch the grid
  onActionObserved?: (frame: Extract<AgentFrame, { type: "observation" }>) => void;
}): JSX.Element {
  const [input, setInput] = useState("");
  const [transcript, setTranscript] = useState<
    ({ role: "user"; text: string } | { role: "agent"; frames: AgentFrame[] })[]
  >([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showTools, setShowTools] = useState(false);

  const tools = useMemo(() => llmExposedCapabilities(), []);
  const canSend = input.trim() !== "" && !busy;

  // A few starter prompts shown in the empty-state so the rail's space reads as a
  // helpful jumping-off point rather than a void. Clicking one seeds the composer
  // (the user still presses Send) — it never auto-fires a turn.
  const suggestedPrompts = useMemo(
    () => [
      "Summarize what changed recently",
      "Roll up the budget by branch",
      "What can I edit on this sheet?",
    ],
    [],
  );

  const send = async () => {
    if (!canSend) return; // empty/whitespace not sent (WEB_UI-071)
    const message = input.trim();
    setInput("");
    setError(null);
    setBusy(true);
    setTranscript((t) => [...t, { role: "user", text: message }]);
    const agentTurn: { role: "agent"; frames: AgentFrame[] } = { role: "agent", frames: [] };
    setTranscript((t) => [...t, agentTurn]);

    try {
      await client.agentChat(sheet, message, (frame) => {
        agentTurn.frames = [...agentTurn.frames, frame];
        setTranscript((t) => [...t.slice(0, -1), { ...agentTurn }]);
        if (frame.type === "observation") onActionObserved?.(frame);
      });
    } catch (e) {
      // prior frames remain visible; offer retry (WEB_UI-069)
      setError(e instanceof Error ? e.message : "Agent error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <aside className="arbor-agent" data-testid="agent-sidebar">
      <header>
        <h2>Agent</h2>
        <button type="button" data-testid="agent-tools-toggle" onClick={() => setShowTools((s) => !s)}>
          What can the agent do?
        </button>
      </header>

      {showTools && (
        <div className="arbor-agent-tools-group">
          <span className="arbor-agent-tools-label">Capabilities</span>
          <ul data-testid="agent-tools">
            {tools.map((t) => (
              <li key={t.id} data-testid={`tool-${t.id}`}>
                {t.name}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="arbor-transcript" data-testid="agent-transcript">
        {transcript.length === 0 && (
          // Richer empty state fills the full-height rail (UX: kill the dead
          // gutter). Quiet prompt to act + clickable starter prompts that seed the
          // composer; the user still presses Send (no auto-fire).
          <div className="arbor-agent-empty" data-testid="agent-empty">
            <span className="arbor-agent-empty-mark" aria-hidden="true">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
            </span>
            <p className="arbor-agent-empty-lead">
              Ask the agent to read, summarize, or propose changes to this sheet.
            </p>
            <span className="arbor-agent-empty-label">Try</span>
            <ul className="arbor-agent-suggestions" data-testid="agent-suggestions">
              {suggestedPrompts.map((p) => (
                <li key={p}>
                  <button
                    type="button"
                    className="arbor-agent-suggestion"
                    data-testid="agent-suggestion"
                    onClick={() => setInput(p)}
                  >
                    {p}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {transcript.map((turn, ti) => (
          <div key={ti} className={`arbor-turn is-${turn.role}`} data-testid={`turn-${ti}`}>
            {turn.role === "user" ? (
              <p className="arbor-frame is-user" data-testid="turn-user">
                {turn.text}
              </p>
            ) : (
              turn.frames.map((f, fi) => <FrameView key={fi} frame={f} onCrChip={onCrChip} />)
            )}
          </div>
        ))}
      </div>

      {error && (
        <div className="arbor-agent-error" role="alert" data-testid="agent-error">
          {error}
          <button type="button" data-testid="agent-retry" onClick={send}>
            Retry
          </button>
        </div>
      )}

      <div className="arbor-agent-input">
        <textarea
          data-testid="agent-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask the agent…"
        />
        <button type="button" data-testid="agent-send" disabled={!canSend} onClick={send}>
          Send
        </button>
      </div>
    </aside>
  );
}

function FrameView({
  frame,
  onCrChip,
}: {
  frame: AgentFrame;
  onCrChip?: (changeRequest: string) => void;
}): JSX.Element {
  switch (frame.type) {
    case "thought":
      return (
        <p className="arbor-frame is-thought" data-testid="frame-thought">
          {frame.content}
        </p>
      );
    case "action":
      return (
        <p className="arbor-frame is-action" data-testid="frame-action">
          <span data-testid="frame-action-tool">{frame.tool}</span>
          <code>{JSON.stringify(frame.arguments)}</code>
        </p>
      );
    case "observation":
      return (
        <p className="arbor-frame is-observation" data-testid="frame-observation" data-outcome={frame.outcome}>
          {frame.outcome}
          {frame.change_request && (
            <button
              type="button"
              className="arbor-cr-chip"
              data-testid={`cr-chip-${frame.change_request}`}
              onClick={() => onCrChip?.(frame.change_request!)}
            >
              CR {frame.change_request}
            </button>
          )}
        </p>
      );
    case "final":
      return (
        <p className="arbor-frame is-final" data-testid="frame-final">
          {frame.content}
        </p>
      );
  }
}
