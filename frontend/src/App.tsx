// The Arbor thin shell. Composes the snapshot-driven TreeTable, the schema
// editor, the import/export panel, and the agent sidebar over the capability
// client. Every mutating affordance funnels through useSheet.dispatch →
// executeAction (ARCHITECTURE §4.1(a)); the UI re-derives no ACL — affordances
// come from snapshot hints. Taste mirrors github.com/victor-develop/React-TreeTable-Demo.

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api as defaultClient,
  type ArborClient,
  type ChangeRequestView,
  type NotificationView,
  type Snapshot,
  type SnapshotColumn,
  type SnapshotNode,
} from "./api";
import { ChangeRequestPanel } from "./components/ChangeRequestPanel";
import { GovernancePanel } from "./components/GovernancePanel";
import { BulkActionBar } from "./components/BulkActionBar";
import { useSheet, cellKey } from "./hooks/useSheet";
import { useCrSelection } from "./hooks/useCrSelection";
import { TreeTable } from "./components/TreeTable";
import { AddColumnForm, ColumnSettings } from "./components/ColumnConfig";
import { AgentSidebar } from "./components/AgentSidebar";
import { ImportExport } from "./components/ImportExport";
import { SubscriptionControl, NotificationItem } from "./components/SubscriptionControl";
import { DelegationControl } from "./components/DelegationControl";
import { ViewMenu } from "./components/ViewMenu";
import { decodeView } from "./lib/view";

// Trigger a browser download of `text` as `filename` (the ImportExport component
// stays host-agnostic; the shell owns the actual file I/O).
function downloadJson(filename: string, text: string): void {
  const blob = new Blob([text], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export type AppProps = {
  // sheet to load; the client fetches the snapshot. When `snapshot` is provided
  // directly (tests / SSR), it is used as the initial seed without a fetch.
  sheetName?: string;
  client?: ArborClient;
  snapshot?: Snapshot;
};

export default function App({ sheetName, client, snapshot }: AppProps = {}): JSX.Element {
  // Back-compat seed path: a directly-supplied snapshot renders standalone.
  if (snapshot && !client && !sheetName) {
    return <SeededShell snapshot={snapshot} />;
  }
  // Idle path: with no sheet to load and no client, render a placeholder rather
  // than auto-connecting (keeps the no-args render side-effect-free).
  if (!sheetName && !client) {
    return (
      <main className="arbor-app">
        <h1>Arbor</h1>
        <p>Governed, API-first, agent-native tree tables.</p>
        <p data-testid="sheet-name">Sheet: (none)</p>
        <p>No snapshot loaded.</p>
      </main>
    );
  }
  return <ConnectedShell client={client ?? defaultClient} sheetName={sheetName ?? "(none)"} />;
}

// The connected shell drives the live capability API through useSheet.
function ConnectedShell({ client, sheetName }: { client: ArborClient; sheetName: string }): JSX.Element {
  // Feature 2 — parse the shared ?v= token ONCE on mount (a malformed/oversize/
  // unknown-version token decodes to null → the default view). useSheet seeds its
  // view + collapsed set from this and thereafter owns the URL via replaceState.
  const [initialView] = useState(() => {
    if (typeof window === "undefined") return null;
    return decodeView(new URLSearchParams(window.location.search).get("v"));
  });
  const sheet = useSheet(client, sheetName, initialView);
  const snap = sheet.snapshot;

  // Change Request review inbox: the sheet's proposed CRs (single- and
  // multi-change), refreshed whenever the snapshot changes.
  const [crs, setCrs] = useState<ChangeRequestView[]>([]);
  const refreshCRs = useCallback(() => {
    if (!client.listChangeRequests) return;
    client
      .listChangeRequests(sheetName)
      .then(setCrs)
      .catch(() => setCrs([]));
  }, [client, sheetName]);
  // Notification inbox: the viewer's in-app notifications for this sheet.
  const [notifications, setNotifications] = useState<NotificationView[]>([]);
  const refreshNotifications = useCallback(() => {
    if (!client.listNotifications) return;
    client
      .listNotifications(sheetName)
      .then(setNotifications)
      .catch(() => setNotifications([]));
  }, [client, sheetName]);
  useEffect(() => {
    if (snap) {
      refreshCRs();
      refreshNotifications();
    }
  }, [refreshCRs, refreshNotifications, snap]);
  const decideCR = (action: string, name: string) => {
    void sheet.dispatch(action, { change_request: name }).then(() => {
      void sheet.refetch();
      refreshCRs();
    });
  };
  const acknowledge = (notification: string) => {
    void sheet.dispatch("acknowledge", { notification }).then(() => refreshNotifications());
  };

  // Bulk CR triage (SPEC P2). Selection is authority-scoped by the hook (only
  // CRs with viewer_is_approver can enter the set). `processingIds` marks rows
  // mid-flight so the panel can render data-processing on them.
  const crSelection = useCrSelection(crs);
  const [processingIds, setProcessingIds] = useState<string[]>([]);
  // Per-CR bulk dispatch: ONE approveChange/rejectChange per id (independent
  // calls — the same capability the per-row buttons use). dispatch RESOLVES even
  // on a server-level error (it returns the failed Outcome), so we re-throw when
  // the outcome carries `.error`; that is how BulkActionBar's loop counts this id
  // as a failure (vs. a thrown network error, which already rejects).
  const decideBulk = useCallback(
    async (action: string, params: Record<string, unknown>) => {
      const outcome = await sheet.dispatch(action, params);
      if (outcome.error) throw new Error(outcome.error);
      return outcome;
    },
    [sheet],
  );
  const bulkApprove = useCallback(
    (name: string) => decideBulk("approveChange", { change_request: name }),
    [decideBulk],
  );
  const bulkReject = useCallback(
    (name: string, reason?: string) =>
      decideBulk("rejectChange", {
        change_request: name,
        ...(reason ? { comment: reason } : {}),
      }),
    [decideBulk],
  );
  // After a bulk run settles: refetch the snapshot + refresh the CR list so
  // applied CRs leave the queue, and clear the selection (the hook also prunes
  // stale ids, but clearing keeps the bar from lingering).
  const onBulkComplete = useCallback(() => {
    void sheet.refetch();
    refreshCRs();
    crSelection.clear();
  }, [sheet, refreshCRs, crSelection]);

  // A cell is "pending" if the local session just filed a suggestion for it
  // (optimistic, pre-refetch) OR the authoritative snapshot carries an open CR
  // targeting it. The snapshot source is what makes the marker SURVIVE refresh
  // and show for OTHER viewers (e.g. Dev B), not only the suggester's session.
  const pendingCell = useMemo(
    () => (node: string, column: string): boolean => {
      if (sheet.pending.some((p) => p.key === cellKey(node, column))) return true;
      const n = sheet.snapshot?.nodes.find((x) => x.name === node);
      return !!n?.pending?.[column]?.length;
    },
    [sheet.pending, sheet.snapshot],
  );
  // Tooltip for the pending marker: "N pending · <requester> → <value>".
  const pendingTitle = useMemo(
    () => (node: string, column: string): string | undefined => {
      const marks = sheet.snapshot?.nodes.find((x) => x.name === node)?.pending?.[column];
      if (marks && marks.length) {
        const fmt = (m: { requester?: string; value?: unknown }) => {
          const who = m.requester ?? "someone";
          const v = m.value;
          const val = v === undefined || v === null || v === "" ? "(empty)" : String(v);
          return `${who} → ${val}`;
        };
        const noun = marks.length === 1 ? "suggestion" : "suggestions";
        const head = marks.slice(0, 3).map(fmt).join("; ");
        const more = marks.length > 3 ? ` +${marks.length - 3} more` : "";
        return `${marks.length} pending ${noun} · ${head}${more}`;
      }
      return sheet.pending.some((p) => p.key === cellKey(node, column))
        ? "Suggestion pending"
        : undefined;
    },
    [sheet.snapshot, sheet.pending],
  );
  // How many open suggestions target this cell (for the count badge). Server
  // marks are authoritative; fall back to 1 for a just-filed local suggestion.
  const pendingCount = useMemo(
    () => (node: string, column: string): number => {
      const n = sheet.snapshot?.nodes.find((x) => x.name === node)?.pending?.[column]?.length;
      if (n) return n;
      return sheet.pending.some((p) => p.key === cellKey(node, column)) ? 1 : 0;
    },
    [sheet.snapshot, sheet.pending],
  );
  const isPendingMove = useMemo(
    () => (node: string) => sheet.pending.some((p) => p.key === `move:${node}`),
    [sheet.pending],
  );

  const commitCell = (node: SnapshotNode, column: SnapshotColumn, value: unknown) => {
    void sheet.dispatch(
      "updateCell",
      { sheet: sheetName, node: node.name, column: column.name, value },
      { optimisticKey: cellKey(node.name, column.name), optimisticValue: value },
    );
  };

  // Schema editor: which data column (if any) the viewer is configuring. The
  // ColumnSettings surface (configure / delete / reassign ownership) was built +
  // tested but never mounted; the header gear opens it here.
  const [editingColumn, setEditingColumn] = useState<SnapshotColumn | null>(null);
  const columnOp = (action: string, params: Record<string, unknown>) => {
    // updateColumn/deleteColumn/grantColumn funnel through dispatch like every
    // other mutation. An executed op refetches (label/width/ownership/removal
    // change the snapshot); a suggested op files a CR, so refresh the inbox too.
    void sheet.dispatch(action, params).then((o) => {
      if (o.kind === "executed") void sheet.refetch();
      refreshCRs();
    });
    setEditingColumn(null);
  };

  // Branch delegation (delegateBranch / revokeDelegation). Both are structural
  // control capabilities executed directly; on success refetch so the snapshot's
  // branch_grants + structural affordances reflect the new delegation.
  const delegate = (params: Record<string, unknown>) => {
    void sheet.dispatch("delegateBranch", params).then(() => void sheet.refetch());
  };
  const revoke = (params: Record<string, unknown>) => {
    void sheet.dispatch("revokeDelegation", params).then(() => void sheet.refetch());
  };

  const del = (node: SnapshotNode) => {
    // deleteNode funnels through dispatch like every mutation. Executed → refetch
    // (the subtree is gone); suggested → a CR is filed, so refresh the inbox.
    void sheet.dispatch("deleteNode", { sheet: sheetName, node: node.name }).then((o) => {
      if (o.kind === "executed") void sheet.refetch();
      refreshCRs();
    });
  };

  const move = (params: { node: string; new_parent: string | null; after: string | null }) => {
    // A structural move has no optimistic cell value to keep, so on an executed
    // move refetch to re-render the new tree shape (depth/sibling order). A
    // suggested move leaves the tree as-is (the change awaits approval).
    void sheet
      .dispatch("moveNode", { sheet: sheetName, ...params }, { optimisticKey: `move:${params.node}` })
      .then((o) => {
        if (o.kind === "executed") void sheet.refetch();
      });
  };

  // Pre-build the three governance tab bodies as slots. GovernancePanel mounts
  // ONLY the active one, but the content is unchanged from the old stacked
  // sections — every existing testid (cr-inbox, notification-inbox,
  // delegation-control, and their children) still renders, just inside a tab.
  const crSlot = snap ? (
    <div className="arbor-cr-zone">
      {/* "Select all I can approve" is a PANEL HEADER control (SPEC P2), so it
          lives ABOVE the cr-inbox list, not inside it — keeping the inbox's
          [data-testid^="cr-select-"] surface to exactly the per-row checkboxes.
          Toggles ONLY the actionable subset, so the selection set can never hold
          a CR the viewer cannot decide. Hidden when nothing is actionable. */}
      {crs.length > 0 && crSelection.actionableIds.length > 0 && (
        <label className="arbor-cr-select-all">
          <input
            type="checkbox"
            data-testid="cr-select-all"
            aria-label="Select all I can approve"
            checked={crSelection.allActionableSelected}
            disabled={processingIds.length > 0}
            onChange={() => crSelection.toggleAll()}
          />
          <span>Select all I can approve</span>
        </label>
      )}
      {crs.length > 0 && (
        // Count + scroll affordance: the list scrolls inside a capped region so a
        // queue of 30-50 CRs stays bounded instead of an endless page scroll.
        <div className="arbor-cr-inbox-head" data-testid="cr-inbox-count">
          {crs.length} open · {crSelection.actionableIds.length} you can approve
        </div>
      )}
      <section className="arbor-cr-inbox" data-testid="cr-inbox" data-count={crs.length}>
        {crs.length === 0 ? (
          <p data-testid="cr-inbox-empty">No open change requests.</p>
        ) : (
          crs.map((cr) => (
            <ChangeRequestPanel
              key={cr.name}
              cr={cr}
              viewer={snap.actor ?? ""}
              onApprove={(n) => decideCR("approveChange", n)}
              onReject={(n) => decideCR("rejectChange", n)}
              onWithdraw={(n) => decideCR("withdrawChange", n)}
              selectable={cr.viewer_is_approver === true}
              selected={crSelection.isSelected(cr.name)}
              onToggleSelect={(n) => crSelection.toggle(n)}
              processing={processingIds.includes(cr.name)}
            />
          ))
        )}
      </section>
      {/* Sticky bulk bar docks at the panel bottom; the component self-guards
          (returns null unless >=1 selected OR a settled summary is pending). It
          must stay MOUNTED across the post-run queue drain (crs → empty) so its
          "X approved · Y failed" summary survives long enough to read + Retry —
          hence no crs.length gate here. Per-row buttons coexist. */}
      <BulkActionBar
        selected={Array.from(crSelection.selected)}
        onApprove={bulkApprove}
        onReject={bulkReject}
        onClear={crSelection.clear}
        onComplete={onBulkComplete}
        onProcessingChange={setProcessingIds}
      />
    </div>
  ) : null;

  const notificationSlot = (
    <section
      className="arbor-notif-inbox"
      data-testid="notification-inbox"
      data-count={notifications.length}
    >
      {notifications.length === 0 ? (
        <p data-testid="notification-inbox-empty">No notifications.</p>
      ) : (
        <ul className="arbor-notifications">
          {notifications.map((n) => (
            <NotificationItem
              key={n.name}
              notification={n}
              onAcknowledge={(p) => acknowledge(p.notification as string)}
            />
          ))}
        </ul>
      )}
    </section>
  );

  const delegationSlot = snap ? (
    <DelegationControl
      sheet={sheetName}
      grants={snap.viewer?.branch_grants ?? []}
      delegatableNodes={snap.nodes.filter((n) => n.can_change_structure)}
      nodeLabel={(node) => {
        const n = snap.nodes.find((x) => x.name === node);
        const lbl = n?.label;
        return (Array.isArray(lbl) ? lbl.join(", ") : lbl) || node;
      }}
      onDelegate={delegate}
      onRevoke={revoke}
    />
  ) : null;

  const delegationCount = snap?.viewer?.branch_grants?.length ?? 0;

  return (
    <main className="arbor-app">
      <header className="arbor-header">
        <div className="arbor-header-titles">
          <h1>Arbor</h1>
          <div className="arbor-header-meta">
            <span data-testid="sheet-name">Sheet: {snap?.sheet.name ?? sheetName}</span>
            {snap && <span data-testid="node-count">{snap.nodes.length} nodes</span>}
          </div>
        </div>
        {snap && (
          <div className="arbor-header-controls">
            <SubscriptionControl
              sheet={sheetName}
              subscribed={snap.viewer?.subscribed ?? false}
              subscriptionName={snap.viewer?.subscription ?? undefined}
              onSubscribe={(params) =>
                void sheet
                  .dispatch("subscribe", {
                    scope: "sheet",
                    target: sheetName,
                    event_types: ["CHANGE_PROPOSED", "CHANGE_APPROVED"],
                    delivery: "in-app",
                    ...params,
                  })
                  .then(() => sheet.refetch())
              }
              onUnsubscribe={(params) =>
                void sheet.dispatch("unsubscribe", params).then(() => sheet.refetch())
              }
            />
            {/* ImportExport moves out of the main stack into a collapsible "Data"
                disclosure here — reclaims the prime post-table slot for governance.
                The ImportExport API is unchanged; only its mount point moves. */}
            <details className="arbor-data-disclosure" data-testid="data-disclosure">
              <summary>Data</summary>
              <div className="arbor-data-disclosure-body">
                <ImportExport
                  snapshot={snap}
                  targetSheet={sheetName}
                  onExport={(text) => downloadJson(`${snap.sheet.name}.json`, text)}
                  onConfirmImport={async (steps) => {
                    // Replay the governed plan in order (columns → nodes), awaiting
                    // each. Source node names don't exist in the target sheet, so map
                    // each source name (_src) to the new node id and rewrite child
                    // parent references as we go. Then refetch + signal completion.
                    const idMap: Record<string, string> = {};
                    for (const step of steps) {
                      if (step.action === "addNode") {
                        const { _src, parent, ...rest } = step.params as Record<string, unknown>;
                        const realParent =
                          typeof parent === "string" ? (idMap[parent] ?? null) : null;
                        const out = await sheet.dispatch("addNode", { ...rest, parent: realParent });
                        const newId = (out.data as { node?: string } | undefined)?.node;
                        if (typeof _src === "string" && newId) idMap[_src] = newId;
                      } else {
                        await sheet.dispatch(step.action, step.params);
                      }
                    }
                    await sheet.refetch();
                    sheet.setBanner({ kind: "saved", message: "Import completed" });
                  }}
                />
              </div>
            </details>
          </div>
        )}
        {sheet.banner && (
          <div
            className={`arbor-banner is-${sheet.banner.kind}`}
            role={sheet.banner.kind === "error" ? "alert" : "status"}
            data-testid="banner"
            data-kind={sheet.banner.kind}
          >
            {sheet.banner.message}
            {sheet.banner.change_request && (
              <span data-testid="banner-cr"> ({sheet.banner.change_request})</span>
            )}
          </div>
        )}
      </header>

      {snap ? (
        <div className="arbor-body">
          <section className="arbor-main">
            <div className="arbor-toolbar">
              <AddColumnForm
                sheet={sheetName}
                existingFields={snap.columns.map((c) => c.field)}
                canAdd={snap.viewer?.can_add_column ?? false}
                onSubmit={(params) => void sheet.dispatch("addColumn", params)}
              />
              {/* Feature 2 — presentation-only view controls (hide/reorder/
                  resize). Lists ONLY the read-ACL-filtered snapshot columns and
                  emits a SheetView; zero executeAction calls. */}
              <details className="arbor-view-disclosure" data-testid="view-disclosure">
                <summary>View</summary>
                <ViewMenu
                  columns={snap.columns}
                  view={sheet.view}
                  onChange={sheet.setView}
                />
              </details>
            </div>
            <div className="arbor-tree-card">
              <TreeTable
                columns={sheet.columns}
                nodes={sheet.nodes}
                labelColumn={snap.label_column}
                collapsed={sheet.collapsed}
                onToggle={sheet.toggle}
                pendingCell={pendingCell}
                pendingTitle={pendingTitle}
                pendingCount={pendingCount}
                isPendingMove={isPendingMove}
                onCommitCell={commitCell}
                onMove={move}
                onColumnSettings={setEditingColumn}
                onDeleteNode={del}
              />
            </div>
            {editingColumn && (
              <div
                className="arbor-modal-backdrop"
                data-testid="column-settings-modal"
                onClick={(e) => {
                  // Backdrop click (outside the panel) closes the editor.
                  if (e.target === e.currentTarget) setEditingColumn(null);
                }}
              >
                <div className="arbor-modal">
                  <header className="arbor-modal-head">
                    <span>Column: {editingColumn.label}</span>
                    <button
                      type="button"
                      data-testid="cs-close"
                      aria-label="Close"
                      onClick={() => setEditingColumn(null)}
                    >
                      ✕
                    </button>
                  </header>
                  <ColumnSettings
                    sheet={sheetName}
                    column={editingColumn}
                    canConfigure={editingColumn.can_edit}
                    canGrant={
                      snap.actor === editingColumn.column_owner ||
                      snap.actor === snap.sheet.structural_owner
                    }
                    onUpdate={(params) => columnOp("updateColumn", params)}
                    onDelete={(params) => columnOp("deleteColumn", params)}
                    onGrant={(params) => columnOp("grantColumn", params)}
                  />
                </div>
              </div>
            )}
            {/* ONE consolidated Governance panel replaces the three stacked
                sections. Each tab renders the EXISTING inbox content unchanged. */}
            <GovernancePanel
              changeRequestCount={crs.length}
              notificationCount={notifications.length}
              delegationCount={delegationCount}
              changeRequests={crSlot}
              notifications={notificationSlot}
              delegations={delegationSlot}
            />
          </section>
          {/* Sticky full-height agent rail (see .arbor-rail in styles.css). */}
          <div className="arbor-rail">
            <AgentSidebar
              client={client}
              sheet={sheetName}
              onActionObserved={() => void sheet.refetch()}
            />
          </div>
        </div>
      ) : (
        <p data-testid="empty-shell">Loading…</p>
      )}
    </main>
  );
}

// Minimal seeded render used by the back-compat test path.
function SeededShell({ snapshot }: { snapshot: Snapshot }): JSX.Element {
  return (
    <main className="arbor-app">
      <h1>Arbor</h1>
      <p>Governed, API-first, agent-native tree tables.</p>
      <p data-testid="sheet-name">Sheet: {snapshot.sheet.name}</p>
      <p data-testid="node-count">{snapshot.nodes.length} nodes</p>
      <TreeTable
        columns={snapshot.columns}
        nodes={snapshot.nodes}
        labelColumn={snapshot.label_column}
        collapsed={new Set()}
        onToggle={() => {}}
        pendingCell={() => false}
        isPendingMove={() => false}
        onCommitCell={() => {}}
        onMove={() => {}}
      />
    </main>
  );
}
