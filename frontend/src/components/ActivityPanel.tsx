// Activity / change-history timeline — a DENSE, newest-first feed of what happened
// on the sheet (the "Activity" governance tab). SELF-CONTAINED: it owns its own
// fetching / keyset paging / filtering against client.listActivity (the NEW
// { events, next_cursor } contract). It re-derives no ACL — read-ACL is enforced
// server-side (the feed never carries raw cell values, and an unreadable column is
// already stripped from the summary). Each row is one event: a subtle type chip +
// the server-built summary one-liner + actor + an absolute-ish timestamp + a CR
// marker when present.
//
// Paging: page 1 is a fresh fetch (no cursor) on mount / sheet change / refreshKey
// change / filter change, REPLACING the list. "Load older" passes before=next_cursor
// and APPENDS the older page; the button is shown only while next_cursor != null.
// Filters: a TYPE <select> (the 11 known EventTypes + "all") and an ACTOR <select>
// (distinct actors currently loaded + "all"), AND-combined and passed to the client.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ActivityEvent, ArborClient } from "../api";

// Verb map keyed by EventType — mirrors api.py's _NOTIF_VERB idiom so the chip
// reads as a short, scannable label rather than the raw SCREAMING_CASE enum.
// The canonical 11 EventType values (arbor/core/types.py) — these KEYS are the
// exact values the TYPE filter sends to the backend, so they must match the enum
// or filtering returns nothing. The value is the short chip label. Unknown/new
// types fall back to a humanized form of the enum value.
const TYPE_LABEL: Record<string, string> = {
  NODE_CREATED: "node added",
  NODE_DELETED: "node deleted",
  NODE_MOVED: "node moved",
  NODE_VALUE_UPDATED: "cell edited",
  COLUMN_CONFIG_UPDATED: "column",
  CHANGE_PROPOSED: "proposed",
  CHANGE_APPROVED: "approved",
  CHANGE_REJECTED: "rejected",
  SUBSCRIPTION_CHANGED: "subscription",
  DELEGATION_CHANGED: "delegation",
  IMPORT_COMPLETED: "import",
};

// The 11 known EventType values, offered in the TYPE filter (plus an "all" sentinel).
const KNOWN_TYPES = Object.keys(TYPE_LABEL);

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

const ALL = "__all__";

export function ActivityPanel({
  client,
  sheet,
  refreshKey,
  onCount,
}: {
  client: ArborClient;
  sheet: string;
  // Bumped by the host whenever the sheet mutates, so the feed re-fetches page 1.
  refreshKey?: number;
  // Reports the loaded event count + whether older events remain (drives the badge).
  onCount?: (n: number, hasMore: boolean) => void;
}): JSX.Element {
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [type, setType] = useState<string>(ALL);
  const [actor, setActor] = useState<string>(ALL);
  const [loading, setLoading] = useState(false);
  // Guards against a stale in-flight response (a slower page-1 fetch resolving
  // after a newer reset) clobbering the current list — only the latest wins.
  const reqId = useRef(0);

  // Fetch a page. `before` undefined → page 1 (reset/replace); a cursor → append.
  const fetchPage = useCallback(
    async (before?: string) => {
      if (!client.listActivity) return;
      const id = ++reqId.current;
      const append = before != null;
      setLoading(true);
      try {
        const res = await client.listActivity(sheet, {
          before,
          ...(type !== ALL ? { type } : {}),
          ...(actor !== ALL ? { actor } : {}),
        });
        if (id !== reqId.current) return; // superseded by a newer fetch
        setEvents((prev) => (append ? [...prev, ...res.events] : res.events));
        setCursor(res.next_cursor);
      } catch {
        if (id !== reqId.current) return;
        if (!append) {
          setEvents([]);
          setCursor(null);
        }
      } finally {
        if (id === reqId.current) setLoading(false);
      }
    },
    [client, sheet, type, actor],
  );

  // Page 1 on mount / sheet change / refreshKey change / filter change. fetchPage
  // already closes over sheet+type+actor, so it changes identity when any of those
  // do; refreshKey is an explicit extra dep so a host mutation re-fetches too.
  useEffect(() => {
    void fetchPage();
  }, [fetchPage, refreshKey]);

  // Report count + hasMore to the host (tab badge).
  useEffect(() => {
    onCount?.(events.length, cursor != null);
  }, [events.length, cursor, onCount]);

  // The ACTOR filter options are the distinct actors currently loaded (the loaded
  // window is the only honest universe the FE has). Keep the active selection even
  // if it scrolled out of the loaded set so the control doesn't silently reset.
  const actorOptions = useMemo(() => {
    const set = new Set<string>(events.map((e) => e.actor));
    if (actor !== ALL) set.add(actor);
    return Array.from(set).sort();
  }, [events, actor]);

  const hasMore = cursor != null;

  return (
    <section
      className="arbor-activity"
      data-testid="activity-panel"
      data-count={events.length}
      data-has-more={hasMore}
    >
      <div className="arbor-activity-filters" data-testid="activity-filters">
        <label className="arbor-activity-filter">
          <span className="arbor-activity-filter-label">Type</span>
          <select
            data-testid="activity-filter-type"
            value={type}
            onChange={(e) => setType(e.target.value)}
          >
            <option value={ALL}>All types</option>
            {KNOWN_TYPES.map((t) => (
              <option key={t} value={t}>
                {typeLabel(t)}
              </option>
            ))}
          </select>
        </label>
        <label className="arbor-activity-filter">
          <span className="arbor-activity-filter-label">Actor</span>
          <select
            data-testid="activity-filter-actor"
            value={actor}
            onChange={(e) => setActor(e.target.value)}
          >
            <option value={ALL}>All actors</option>
            {actorOptions.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </label>
      </div>

      {events.length === 0 ? (
        <p className="arbor-activity-empty" data-testid="activity-empty">
          No activity yet.
        </p>
      ) : (
        <ol className="arbor-activity-feed">
          {/* Newest-first order is the server's; render as returned (no re-sort). */}
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
      )}

      {/* "Load older" is shown ONLY while the server reports more older events. */}
      {hasMore && (
        <button
          type="button"
          className="arbor-activity-load-older"
          data-testid="activity-load-older"
          disabled={loading}
          onClick={() => void fetchPage(cursor ?? undefined)}
        >
          {loading ? "Loading…" : "Load older"}
        </button>
      )}
    </section>
  );
}
