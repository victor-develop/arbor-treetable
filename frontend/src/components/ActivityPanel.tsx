// Activity / change-history timeline — a DENSE, newest-first feed of what happened
// on the sheet (the "Activity" governance tab). Presentational ONLY: the host
// fetches arbor.list_activity and hands the events in; this panel re-derives no
// state and performs no ACL (read-ACL is enforced server-side — the feed never
// carries raw cell values, and an unreadable column is already stripped from the
// summary). Each row is one event: actor + summary one-liner + a subtle type chip
// + an absolute-ish timestamp + a CR marker when present.

import type { ActivityEvent } from "../api";

// Verb map keyed by EventType — mirrors api.py's _NOTIF_VERB idiom so the chip
// reads as a short, scannable label rather than the raw SCREAMING_CASE enum.
// Unknown/new types fall back to a humanized form of the enum value.
const TYPE_LABEL: Record<string, string> = {
  CELL_UPDATED: "edited",
  NODE_ADDED: "added",
  NODE_MOVED: "moved",
  NODE_DELETED: "deleted",
  COLUMN_ADDED: "column",
  COLUMN_UPDATED: "column",
  COLUMN_DELETED: "column",
  CHANGE_PROPOSED: "proposed",
  CHANGE_APPROVED: "approved",
  CHANGE_REJECTED: "rejected",
  ROLE_GRANTED: "role",
};

function typeLabel(type: string): string {
  return TYPE_LABEL[type] ?? type.toLowerCase().replace(/_/g, " ");
}

// Absolute-ish timestamp: a compact local "Jun 20, 10:00" rendering. The raw ISO
// string stays in the <time datetime> attribute for tooltip/title + machine use.
function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function ActivityPanel({ events }: { events: ActivityEvent[] }): JSX.Element {
  if (events.length === 0) {
    return (
      <section className="arbor-activity" data-testid="activity-panel" data-count={0}>
        <p className="arbor-activity-empty" data-testid="activity-empty">
          No activity yet.
        </p>
      </section>
    );
  }

  return (
    <section className="arbor-activity" data-testid="activity-panel" data-count={events.length}>
      <ol className="arbor-activity-feed">
        {/* Newest-first order is the server's; render as handed in (no re-sort). */}
        {events.map((e) => (
          <li
            key={e.event_id}
            className="arbor-activity-row"
            data-testid={`activity-row-${e.event_id}`}
            data-type={e.type}
          >
            <span
              className="arbor-activity-type"
              data-testid="activity-type"
              data-type={e.type}
              title={e.type}
            >
              {typeLabel(e.type)}
            </span>
            <span className="arbor-activity-main">
              <span className="arbor-activity-summary">{e.summary}</span>
              <span className="arbor-activity-meta">
                <span className="arbor-activity-actor" data-testid="activity-actor">
                  {e.actor}
                </span>
                <time
                  className="arbor-activity-time"
                  data-testid="activity-time"
                  dateTime={e.timestamp}
                  title={e.timestamp}
                >
                  {formatTimestamp(e.timestamp)}
                </time>
                {e.change_request && (
                  <span
                    className="arbor-activity-cr"
                    data-testid="activity-cr"
                    title={`Change request ${e.change_request}`}
                  >
                    {e.change_request}
                  </span>
                )}
              </span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
