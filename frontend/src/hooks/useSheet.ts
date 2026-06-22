// useSheet — owns snapshot state, expand/collapse view state, and the
// optimistic-commit / authoritative-Outcome reconciliation that every mutating
// affordance shares (the "Outcome-rendering contract" in web-ui.md):
//   executed  → commit; flash "Saved"; no CR banner
//   suggested → revert to snapshot; show "Suggestion sent to <approver>"
//   error     → revert; surface error; offer retry
// The UI NEVER re-derives ACL — it trusts the returned Outcome over any
// client-side prediction (WEB_UI-020, -086).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ArborClient, Outcome, Snapshot, SnapshotColumn, SnapshotNode } from "../api";
import {
  encodeView,
  resolveColumns,
  type SheetView,
} from "../lib/view";

export type Banner = {
  kind: "suggested" | "error" | "saved" | "conflict";
  message: string;
  change_request?: string;
  // Feature 1 — conflict banner extras (kind === "conflict"): the authoritative
  // server value, the user's rejected edit, and the cell it targets so
  // resolveConflict("redo") can reopen the editor on the fresh base.
  current_value?: unknown;
  rejected?: unknown;
  conflictKey?: string;
};

export type PendingMark = {
  // key = `${node}:${column}` for cells, or `move:${node}` for structural moves
  key: string;
  change_request?: string;
};

type SerializedMutation = Promise<Outcome>;

export function useSheet(
  client: ArborClient,
  sheetName: string,
  // Feature 2 — an optional initial SheetView parsed from the ?v= link on mount.
  // null/undefined => the default view (all readable columns, snapshot order).
  initialView?: SheetView | null,
) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  // Feature 2 — presentation-only view overlay (hidden/order/width/collapsed).
  // Seeded from the shared link; every change is mirrored to the URL (below).
  const [view, setView] = useState<SheetView>(
    () => initialView ?? { v: 1, hidden: [], order: [] },
  );
  const [collapsed, setCollapsed] = useState<Set<string>>(
    () => new Set(initialView?.collapsed ?? []),
  );
  const [banner, setBanner] = useState<Banner | null>(null);
  const [pending, setPending] = useState<PendingMark[]>([]);
  const [error, setError] = useState<string | null>(null);
  // Per-cell optimistic overrides applied on top of the snapshot.
  const [optimistic, setOptimistic] = useState<Record<string, unknown>>({});
  // Feature 1 — per-cell base_version overrides folded from successful writes,
  // keyed by cellKey. Seeded from the snapshot; a successful executed write
  // bumps the entry so a consecutive same-cell edit never self-conflicts.
  const [versionOverrides, setVersionOverrides] = useState<Record<string, number>>({});
  const versionsRef = useRef<Record<string, number>>({});
  versionsRef.current = versionOverrides;
  // Mirror the snapshot in a ref so the (client-only-memoized) dispatch closure
  // always reads the latest per-cell versions, never the mount-time null.
  const snapshotRef = useRef<Snapshot | null>(null);
  snapshotRef.current = snapshot;
  // Serialize mutations so rapid commits never interleave (WEB_UI-025).
  const tail = useRef<SerializedMutation>(Promise.resolve({ kind: "read" }));

  const refetch = useCallback(async () => {
    const snap = await client.getSheetSnapshot(sheetName);
    setSnapshot(snap);
    // A fresh snapshot is authoritative: clear stale optimistic + pending marks
    // whose target now matches (WEB_UI-022), and drop folded version overrides
    // (the snapshot's versions are now the source of truth).
    setOptimistic({});
    setPending([]);
    setVersionOverrides({});
  }, [client, sheetName]);

  // Resolve a cell's current base_version: a folded override wins, else the
  // snapshot's per-cell version, else 0 (empty cell).
  const baseVersionFor = useCallback((node: string, column: string): number => {
    const key = cellKey(node, column);
    if (key in versionsRef.current) return versionsRef.current[key];
    const n = snapshotRef.current?.nodes.find((x) => x.name === node);
    return n?.versions?.[column] ?? 0;
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const toggle = useCallback((node: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(node)) next.delete(node);
      else next.add(node);
      return next;
    });
  }, []);

  // The shared dispatch: runs executeAction, applies the Outcome contract, and
  // serializes against the previous in-flight mutation.
  const dispatch = useCallback(
    (
      actionId: string,
      params: Record<string, unknown>,
      opts?: { optimisticKey?: string; optimisticValue?: unknown },
    ): Promise<Outcome> => {
      const run = async (): Promise<Outcome> => {
        setError(null);
        if (opts?.optimisticKey !== undefined) {
          setOptimistic((o) => ({ ...o, [opts.optimisticKey!]: opts.optimisticValue }));
        }
        // Feature 1 — opt-in optimistic concurrency: thread base_version for cell
        // edits from the folded/snapshot version map (unless the caller set one).
        let sendParams = params;
        if (
          actionId === "updateCell" &&
          params.base_version === undefined &&
          typeof params.node === "string" &&
          typeof params.column === "string"
        ) {
          sendParams = {
            ...params,
            base_version: baseVersionFor(params.node, params.column),
          };
        }
        let outcome: Outcome;
        try {
          outcome = await client.executeAction(actionId, sendParams);
        } catch (e) {
          // network/throw → revert optimistic, offer retry (WEB_UI-087)
          if (opts?.optimisticKey !== undefined) clearOptimistic(opts.optimisticKey);
          const msg = e instanceof Error ? e.message : "Request failed";
          setError(msg);
          setBanner({ kind: "error", message: msg });
          throw e;
        }
        if (outcome.error) {
          if (opts?.optimisticKey !== undefined) clearOptimistic(opts.optimisticKey);
          if (outcome.error === "VERSION_CONFLICT") {
            // Feature 1 — lost-update conflict: do NOT commit; raise a conflict
            // banner carrying the authoritative current value + the rejected edit
            // so the user can redo on the fresh base or discard (WEB_UI-023).
            const data = (outcome.data ?? {}) as Record<string, unknown>;
            setError(outcome.error);
            setBanner({
              kind: "conflict",
              message: "This cell changed since you started editing",
              current_value: data.current_value,
              rejected: opts?.optimisticValue,
              conflictKey: opts?.optimisticKey,
            });
            return outcome;
          }
          // other server-level error codes (WEB_UI-085)
          setError(outcome.error);
          setBanner({ kind: "error", message: outcome.error });
          return outcome;
        }
        if (outcome.kind === "executed") {
          // commit stays (optimistic value becomes truth until refetch)
          setBanner({ kind: "saved", message: "Saved" });
          // Feature 1 — fold the returned version so a consecutive same-cell edit
          // carries the bumped base and never self-conflicts.
          const newVersion = (outcome.data as { version?: number } | undefined)?.version;
          if (
            actionId === "updateCell" &&
            typeof newVersion === "number" &&
            typeof params.node === "string" &&
            typeof params.column === "string"
          ) {
            const key = cellKey(params.node, params.column);
            setVersionOverrides((v) => ({ ...v, [key]: newVersion }));
          }
        } else if (outcome.kind === "suggested") {
          if (opts?.optimisticKey !== undefined) clearOptimistic(opts.optimisticKey);
          const approver = outcome.resolved_approver ?? "approver";
          const co = outcome.co_approvers?.length
            ? ` (co-approver: ${outcome.co_approvers.join(", ")})`
            : "";
          setBanner({
            kind: "suggested",
            message: `Suggestion sent to ${approver}${co}`,
            change_request: outcome.change_request,
          });
          if (opts?.optimisticKey !== undefined) {
            setPending((p) => [
              ...p.filter((m) => m.key !== opts.optimisticKey),
              { key: opts.optimisticKey!, change_request: outcome.change_request },
            ]);
          }
        }
        return outcome;
      };

      const clearOptimistic = (key: string) =>
        setOptimistic((o) => {
          const next = { ...o };
          delete next[key];
          return next;
        });

      const next = tail.current.then(run, run);
      // keep the chain alive even if a link rejects
      tail.current = next.catch(() => ({ kind: "read" }) as Outcome);
      return next;
    },
    [client],
  );

  // Snapshot nodes with optimistic cell overrides layered on (for rendering).
  const nodes: SnapshotNode[] = useMemo(() => {
    if (!snapshot) return [];
    if (Object.keys(optimistic).length === 0) return snapshot.nodes;
    return snapshot.nodes.map((n) => {
      let values = n.values;
      for (const key of Object.keys(optimistic)) {
        const [node, column] = key.split(" ");
        if (node === n.name && column) {
          values = { ...values, [column]: optimistic[key] };
        }
      }
      return values === n.values ? n : { ...n, values };
    });
  }, [snapshot, optimistic]);

  // Feature 2 — the columns the table actually renders: the read-ACL-filtered
  // snapshot columns resolved through the view (hidden/order/width). PURE +
  // reveal-impossible (a column absent from the snapshot can never appear).
  const columns: SnapshotColumn[] = useMemo(
    () => (snapshot ? resolveColumns(snapshot.columns, view) : []),
    [snapshot, view],
  );

  // Feature 2 — keep the URL's ?v= in sync with the live view via replaceState
  // (no history entry, no navigation). Presentation only — zero backend calls.
  useEffect(() => {
    if (typeof window === "undefined" || !window.history?.replaceState) return;
    const token = encodeView(view);
    const params = new URLSearchParams(window.location.search);
    params.set("v", token);
    // A path-relative target (?v=...) keeps jsdom happy and avoids any
    // cross-origin replaceState rejection in the real browser too.
    const target = `${window.location.pathname}?${params.toString()}`;
    window.history.replaceState(window.history.state, "", target);
  }, [view]);

  const pendingKey = useCallback(
    (key: string) => pending.find((p) => p.key === key),
    [pending],
  );

  // Feature 1 — resolve a VERSION_CONFLICT banner. "redo" refetches the whole
  // snapshot (so the editor can reopen on the fresh authoritative base) and
  // clears the conflict; "discard" just drops the conflict + the rejected edit.
  const resolveConflict = useCallback(
    async (mode: "redo" | "discard") => {
      const key = banner?.conflictKey;
      if (key !== undefined) {
        setOptimistic((o) => {
          const next = { ...o };
          delete next[key];
          return next;
        });
      }
      setBanner(null);
      setError(null);
      if (mode === "redo") await refetch();
    },
    [banner, refetch],
  );

  return {
    snapshot,
    nodes,
    // Feature 2 — view-resolved, read-ACL-filtered columns the table renders.
    columns,
    view,
    setView,
    collapsed,
    banner,
    error,
    pending,
    pendingKey,
    setBanner,
    toggle,
    dispatch,
    refetch,
    resolveConflict,
  };
}

// Cell optimistic key uses a NUL separator so column names with ':' don't clash.
export function cellKey(node: string, column: string): string {
  return `${node} ${column}`;
}
