// useSheet — owns snapshot state, expand/collapse view state, and the
// optimistic-commit / authoritative-Outcome reconciliation that every mutating
// affordance shares (the "Outcome-rendering contract" in web-ui.md):
//   executed  → commit; flash "Saved"; no CR banner
//   suggested → revert to snapshot; show "Suggestion sent to <approver>"
//   error     → revert; surface error; offer retry
// The UI NEVER re-derives ACL — it trusts the returned Outcome over any
// client-side prediction (WEB_UI-020, -086).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ArborClient,
  CellCommentSummary,
  Outcome,
  Snapshot,
  SnapshotColumn,
  SnapshotNode,
} from "../api";
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

// Draft flow — one entry in the local draft layer, keyed by cellKey. Mirrors a
// server CellDraft row (the draft box is server-persisted): the proposed value,
// the optimistic-concurrency base captured at save time, and the (node, column)
// so the modal can group/diff without re-parsing the key.
export type DraftEntry = {
  value: unknown;
  base_version?: number;
  node: string;
  column: string;
};

// Draft flow — the modal's per-row view of a draft (the live cellKey + the
// targeting + the proposed value). The shell resolves the column LABEL,
// old value, and approver (column_owner) from the snapshot at render time.
export type DraftView = {
  key: string;
  node: string;
  column: string;
  value: unknown;
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
  // Draft flow — the local mirror of the actor's server-persisted draft box,
  // keyed by cellKey. A non-owner edit (can_edit === false) writes here instead
  // of instantly filing a CR: the value renders live (overlaid like optimistic)
  // AND is persisted via saveCellDraft, so it survives reload / device change.
  // Unlike `optimistic`, drafts SURVIVE a refetch (they're re-hydrated from the
  // server) — only a submit (or discard) clears them.
  const [drafts, setDrafts] = useState<Record<string, DraftEntry>>({});
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
    // Draft flow — RE-HYDRATE (don't clear) the draft box from the server: drafts
    // are server-persisted, so they must survive a refetch / reload / device
    // change. Only a submit (server-side) or an explicit discard removes them.
    // A client without the endpoint (test/mocked) simply keeps an empty box.
    if (client.listCellDrafts) {
      try {
        const rows = await client.listCellDrafts(sheetName);
        const next: Record<string, DraftEntry> = {};
        for (const d of rows) {
          next[cellKey(d.node, d.column)] = {
            value: d.value,
            base_version: d.base_version,
            node: d.node,
            column: d.column,
          };
        }
        setDrafts(next);
      } catch {
        // A failed draft hydrate must never block the snapshot render — the bar
        // just won't show until the next successful refetch.
        setDrafts({});
      }
    }
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

  // Snapshot nodes with optimistic + draft cell overrides layered on (for
  // rendering). Drafts (the non-owner draft box) overlay exactly like optimistic
  // overrides so a drafted cell shows the proposed value LIVE; a draft wins over
  // the snapshot, and a same-cell optimistic override (the owner direct-commit
  // path) wins over a draft (an owner can't also draft, so they never collide).
  const nodes: SnapshotNode[] = useMemo(() => {
    if (!snapshot) return [];
    const draftKeys = Object.keys(drafts);
    const optimisticKeys = Object.keys(optimistic);
    if (draftKeys.length === 0 && optimisticKeys.length === 0) return snapshot.nodes;
    return snapshot.nodes.map((n) => {
      let values = n.values;
      // Drafts first (overlay by their stored node/column), then optimistic so a
      // same-cell optimistic override wins.
      for (const key of draftKeys) {
        const d = drafts[key];
        if (d.node === n.name) values = { ...values, [d.column]: d.value };
      }
      for (const key of optimisticKeys) {
        const [node, column] = key.split(" ");
        if (node === n.name && column) {
          values = { ...values, [column]: optimistic[key] };
        }
      }
      return values === n.values ? n : { ...n, values };
    });
  }, [snapshot, optimistic, drafts]);

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

  // ---- Draft flow (non-owner cell editing) --------------------------------
  // A non-owner cell edit (can_edit === false) does NOT instantly file a CR
  // anymore (the old "Suggestion sent" + revert + dot the user called "weird").
  // Instead it writes to the actor's server-persisted draft box: the value shows
  // LOCALLY immediately (overlaid in the `nodes` memo, feels real-time) AND is
  // persisted via saveCellDraft so it survives reload / device change. Owners
  // keep the direct-commit dispatch("updateCell") path entirely untouched.
  const commitDraft = useCallback(
    async (node: string, column: string, value: unknown): Promise<void> => {
      setError(null);
      const key = cellKey(node, column);
      // Capture the optimistic-concurrency base the same way an owner write does,
      // so a later submit threads a coherent base_version per change.
      const base_version = baseVersionFor(node, column);
      // Show the value instantly; keep the prior entry so we can revert on error.
      let prior: DraftEntry | undefined;
      setDrafts((d) => {
        prior = d[key];
        return { ...d, [key]: { value, base_version, node, column } };
      });
      try {
        if (client.saveCellDraft) {
          await client.saveCellDraft(sheetName, node, column, value, base_version);
        }
      } catch (e) {
        // Revert the local draft to its prior state and surface the failure.
        setDrafts((d) => {
          const next = { ...d };
          if (prior === undefined) delete next[key];
          else next[key] = prior;
          return next;
        });
        const msg = e instanceof Error ? e.message : "Could not save draft";
        setError(msg);
        setBanner({ kind: "error", message: msg });
      }
    },
    [client, sheetName, baseVersionFor],
  );

  // Predicate: does this cell currently carry a local draft? (drives the cell's
  // "unsaved draft" marker, distinct from the pending-approval dot).
  const draftKey = useCallback(
    (node: string, column: string): boolean => cellKey(node, column) in drafts,
    [drafts],
  );
  const draftCount = Object.keys(drafts).length;

  // Per-cell comment summary accessor for the cell glyph. Reads the snapshot's
  // sparse per-node `comments` map (server-sourced + read-ACL filtered), so a
  // column the viewer can't read never surfaces a glyph. Undefined when the cell
  // has no comments (no glyph). Pure lookup over the authoritative snapshot.
  const commentSummary = useCallback(
    (node: string, column: string): CellCommentSummary | undefined => {
      const n = snapshot?.nodes.find((x) => x.name === node);
      return n?.comments?.[column];
    },
    [snapshot],
  );
  // The modal's row view of the draft box (stable cellKey + targeting + value).
  const draftList: DraftView[] = useMemo(
    () =>
      Object.entries(drafts).map(([key, d]) => ({
        key,
        node: d.node,
        column: d.column,
        value: d.value,
      })),
    [drafts],
  );

  // Discard a single cell's draft (optimistically drop it, then tell the server).
  const discardDraft = useCallback(
    async (node: string, column: string): Promise<void> => {
      const key = cellKey(node, column);
      setDrafts((d) => {
        const next = { ...d };
        delete next[key];
        return next;
      });
      if (client.discardCellDraft) {
        try {
          await client.discardCellDraft(sheetName, node, column);
        } catch {
          // A failed server discard is non-fatal — a later refetch re-hydrates
          // the true draft box; we don't resurrect the row mid-flight.
        }
      }
    },
    [client, sheetName],
  );

  // Discard the whole draft box (the modal's "Discard all").
  const discardAllDrafts = useCallback(async (): Promise<void> => {
    setDrafts({});
    if (client.discardCellDrafts) {
      try {
        await client.discardCellDrafts(sheetName);
      } catch {
        // Non-fatal — see discardDraft.
      }
    }
  }, [client, sheetName]);

  // Submit the draft box as ONE multi-change suggestChanges CR. On a suggested
  // outcome: raise the suggested banner, convert EACH drafted cell into a
  // pending-approval mark carrying the CR id, clear the local drafts, then
  // refetch (the server already deleted the submitted drafts, so the re-hydrate
  // comes back empty and the snapshot now carries the authoritative pending
  // marks). Returns the Outcome so the caller can branch (e.g. refresh the CR
  // inbox / activity).
  const submitDrafts = useCallback(async (): Promise<Outcome> => {
    if (!client.submitCellDrafts) return { kind: "read" };
    const submittedKeys = Object.keys(drafts);
    setError(null);
    let outcome: Outcome;
    try {
      outcome = await client.submitCellDrafts(sheetName);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Could not submit drafts";
      setError(msg);
      setBanner({ kind: "error", message: msg });
      throw e;
    }
    if (outcome.error) {
      setError(outcome.error);
      setBanner({ kind: "error", message: outcome.error });
      return outcome;
    }
    if (outcome.kind === "suggested") {
      const approver = outcome.resolved_approver ?? "approver";
      const co = outcome.co_approvers?.length
        ? ` (co-approver: ${outcome.co_approvers.join(", ")})`
        : "";
      setBanner({
        kind: "suggested",
        message: `Suggestion sent to ${approver}${co}`,
        change_request: outcome.change_request,
      });
    }
    // Clear the local box (the server deleted the rows) and re-sync the snapshot.
    // The refetch resets `pending` (a fresh snapshot is authoritative), so we add
    // the per-cell optimistic pending marks AFTER it — this keeps the just-filed
    // suggestion visible immediately (the same role dispatch's suggested path
    // plays, only here for a multi-change submit), surviving until the next
    // snapshot carries the authoritative server-side marks.
    setDrafts({});
    await refetch();
    if (outcome.kind === "suggested") {
      setPending((p) => [
        ...p.filter((m) => !submittedKeys.includes(m.key)),
        ...submittedKeys.map((key) => ({ key, change_request: outcome.change_request })),
      ]);
    }
    return outcome;
  }, [client, sheetName, drafts, refetch]);

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
    // Draft flow (non-owner cell editing).
    drafts,
    draftKey,
    draftCount,
    draftList,
    // Comments — per-cell summary accessor for the cell glyph.
    commentSummary,
    commitDraft,
    discardDraft,
    discardAllDrafts,
    submitDrafts,
  };
}

// Cell optimistic key uses a NUL separator so column names with ':' don't clash.
export function cellKey(node: string, column: string): string {
  return `${node} ${column}`;
}
