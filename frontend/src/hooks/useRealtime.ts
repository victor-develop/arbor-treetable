// Realtime signals over SSE. The server pushes DIRTY MARKERS, never content
// ("the comments on this sheet changed"), and this hook turns each one into a
// coalesced callback the shell answers with an ordinary refetch. That is the
// whole design: the refetch goes through the read endpoints, so their read-ACL
// filtering is inherited instead of re-implemented on a push path.
//
// EventSource, not WebSocket: the traffic is one-way, reconnection is built in,
// and it authenticates with the session cookie the browser already sends.

import { useEffect, useRef, useState } from "react";

// Signals arriving closer together than this are answered by ONE refetch. A
// burst is normal (a thread of replies, a paste of several comments) and each
// marker means the same thing, so refetching per marker is pure waste.
const COALESCE_MS = 400;

// How long a stream may keep failing before this hook stops letting the browser
// retry. Time, NOT a failure count: a count of 3 is ~6-9s of browser retries,
// which a container restart or a rolling deploy exceeds — so counting would
// permanently kill realtime in every open tab after any routine deploy. A
// server that answers non-200 fails the connection outright (the browser does
// not retry that at all), so the only thing this budget governs is a genuinely
// prolonged outage.
const GIVE_UP_AFTER_MS = 5 * 60 * 1000;

// Kinds name WHAT THE CLIENT MUST REFETCH, not what changed — two server-side
// changes that oblige the same fetch share a kind, which keeps this layer free
// of any per-event-type knowledge.
//   comments -> the cell comment badges (and the open thread)
//   sheet    -> the snapshot: cell values, structure, column config and order
//   crs      -> the change-request inbox
export type RealtimeKind = "comments" | "sheet" | "crs";

const KINDS: RealtimeKind[] = ["comments", "sheet", "crs"];

export function useRealtime(
  sheet: string | null | undefined,
  onSignal: (kind: RealtimeKind) => void,
): void {
  // Keep the callback in a ref so a new closure each render does not tear down
  // and re-open the stream (which would drop signals on every parent render).
  const handler = useRef(onSignal);
  useEffect(() => {
    handler.current = onSignal;
  }, [onSignal]);

  // Bumping this re-runs the subscribe effect. Used to re-arm after the hook
  // gave up: a tab that was hidden through a deploy should recover when the
  // user looks at it again, rather than staying silently dead until reload.
  const [generation, setGeneration] = useState(0);
  const gaveUp = useRef(false);
  useEffect(() => {
    const rearm = () => {
      if (gaveUp.current && document.visibilityState === "visible") {
        gaveUp.current = false;
        setGeneration((g) => g + 1);
      }
    };
    document.addEventListener("visibilitychange", rearm);
    window.addEventListener("focus", rearm);
    return () => {
      document.removeEventListener("visibilitychange", rearm);
      window.removeEventListener("focus", rearm);
    };
  }, []);

  useEffect(() => {
    if (!sheet) return;
    // Absent in jsdom (and in any non-browser host) — realtime is optional, so
    // its absence must be a silent no-op rather than a crash.
    if (typeof EventSource === "undefined") return;

    let closed = false;
    // Null until this stream has ever connected. Treating "never connected" as
    // "failing since mount" is what makes the budget measure an outage rather
    // than a cold start.
    let healthySince: number | null = null;
    let failingSince: number | null = null;
    // One timer PER KIND. A single shared timer would let a burst of mixed
    // kinds cancel each other, so the only refetch that ever happened would be
    // the last kind to arrive — e.g. a cell edit landing right after a comment
    // would swallow the comment refresh.
    const timers = new Map<RealtimeKind, ReturnType<typeof setTimeout>>();
    const url = `/api/method/arbor.events?sheet=${encodeURIComponent(sheet)}`;
    const source = new EventSource(url, { withCredentials: true });

    const fire = (kind: RealtimeKind) => {
      const existing = timers.get(kind);
      if (existing) clearTimeout(existing);
      timers.set(
        kind,
        setTimeout(() => {
          timers.delete(kind);
          if (!closed) handler.current(kind);
        }, COALESCE_MS),
      );
    };

    const markHealthy = () => {
      healthySince = Date.now();
      failingSince = null;
    };
    source.addEventListener("open", markHealthy);
    for (const kind of KINDS) {
      source.addEventListener(kind, () => {
        markHealthy(); // a delivered signal proves the stream is alive
        fire(kind);
      });
    }
    source.addEventListener("error", () => {
      const now = Date.now();
      if (failingSince === null) failingSince = healthySince ?? now;
      if (now - failingSince >= GIVE_UP_AFTER_MS) {
        closed = true;
        gaveUp.current = true;
        source.close();
      }
    });

    return () => {
      closed = true;
      timers.forEach(clearTimeout);
      timers.clear();
      source.close();
    };
  }, [sheet, generation]);
}
