// Per-user cross-sheet notification Inbox (Feature: process). Fetches the viewer's
// notifications via client.inbox() (self-scoped server-side — a user only ever
// sees their own), groups them by SOURCE (tree_event / comment / process / sla),
// fires an ack for requires_ack rows (reusing the acknowledge capability via the
// onAck callback), and deep-links each row to its originating sheet/cell via onOpen.
//
// SELF-CONTAINED fetch on mount / refreshKey change. It re-derives no ACL — inbox()
// is self-scoped and acknowledge enforces recipient == actor server-side. The
// source is derived from the event_type discriminator the server carries (which may
// be a display string like "COMMENT_ADDED" that is NOT one of the 11 EventTypes).

import { useCallback, useEffect, useRef, useState } from "react";
import type { ArborClient, InboxItem } from "../api";

// The four inbox sources, in display order. Each row is bucketed into exactly one.
type Source = "tree_event" | "comment" | "process" | "sla";

const SOURCE_ORDER: Source[] = ["sla", "process", "comment", "tree_event"];

const SOURCE_LABEL: Record<Source, string> = {
  sla: "SLA breaches",
  process: "Process",
  comment: "Comments",
  tree_event: "Activity",
};

// Bucket a row by its event_type discriminator. The server may send a display
// string (e.g. "COMMENT_ADDED", "PROCESS_STAGE_ASSIGNED", "PROCESS_SLA_BREACHED")
// that is NOT one of the 11 canonical EventTypes — match on those before falling
// back to the generic tree_event bucket.
function sourceOf(eventType: string): Source {
  const t = eventType.toUpperCase();
  if (t.includes("SLA")) return "sla";
  if (t.startsWith("PROCESS")) return "process";
  if (t.includes("COMMENT")) return "comment";
  return "tree_event";
}

export function InboxPage({
  client,
  refreshKey,
  onAck,
  onOpen,
}: {
  client: ArborClient;
  // Bumped by the host to force a refetch (e.g. after acking elsewhere).
  refreshKey?: number;
  // Acknowledge a requires_ack row (the host wires this to the acknowledge
  // capability through executeAction). Absent → no ack affordance.
  onAck?: (notification: string) => void;
  // Deep-link into the originating sheet (and highlight the node when present).
  onOpen?: (target: { sheet: string; node: string | null }) => void;
}): JSX.Element {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [loading, setLoading] = useState(false);
  const reqId = useRef(0);

  const fetchInbox = useCallback(async () => {
    if (!client.inbox) return;
    const id = ++reqId.current;
    setLoading(true);
    try {
      const res = await client.inbox();
      if (id !== reqId.current) return;
      setItems(res);
    } catch {
      if (id === reqId.current) setItems([]);
    } finally {
      if (id === reqId.current) setLoading(false);
    }
  }, [client]);

  useEffect(() => {
    void fetchInbox();
  }, [fetchInbox, refreshKey]);

  // Bucket rows by source, preserving the server's (newest-first) arrival order
  // within each group.
  const grouped: Record<Source, InboxItem[]> = {
    sla: [],
    process: [],
    comment: [],
    tree_event: [],
  };
  for (const it of items) grouped[sourceOf(it.event_type)].push(it);

  const nonEmpty = SOURCE_ORDER.filter((s) => grouped[s].length > 0);

  return (
    <section className="arbor-inbox" data-testid="inbox-page" data-count={items.length}>
      <header className="arbor-inbox-header">
        <h1>Inbox</h1>
      </header>

      {items.length === 0 ? (
        <p className="arbor-inbox-empty" data-testid="inbox-empty">
          {loading ? "Loading…" : "No notifications."}
        </p>
      ) : (
        nonEmpty.map((source) => (
          <div key={source} className="arbor-inbox-group" data-testid={`inbox-group-${source}`}>
            <h2 className="arbor-inbox-group-label">
              {SOURCE_LABEL[source]} <span className="arbor-count">{grouped[source].length}</span>
            </h2>
            <ul className="arbor-inbox-rows">
              {grouped[source].map((it) => (
                <li
                  key={it.name}
                  className="arbor-inbox-row"
                  data-testid={`inbox-row-${it.name}`}
                  data-source={source}
                  data-acked={it.acked}
                >
                  <button
                    type="button"
                    className="arbor-inbox-open"
                    data-testid={`inbox-open-${it.name}`}
                    onClick={() => onOpen?.({ sheet: it.sheet, node: it.node ?? null })}
                  >
                    <span className="arbor-inbox-msg">{it.message}</span>
                    <span className="arbor-inbox-meta">
                      <span className="arbor-inbox-sheet" data-testid="inbox-sheet">
                        {it.sheet}
                      </span>
                    </span>
                  </button>
                  {it.requires_ack &&
                    (it.acked ? (
                      <span className="arbor-inbox-acked" data-testid={`inbox-acked`} aria-label="acknowledged">
                        ✓ acked
                      </span>
                    ) : (
                      onAck && (
                        <button
                          type="button"
                          className="arbor-inbox-ack"
                          data-testid={`inbox-ack-${it.name}`}
                          onClick={() => onAck(it.name)}
                        >
                          Acknowledge
                        </button>
                      )
                    ))}
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </section>
  );
}
