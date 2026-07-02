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
  type RoleApplicationView,
  type RoleGrantView,
  type RoleView,
  type Snapshot,
  type SnapshotColumn,
  type SnapshotNode,
} from "./api";
import { ActivityPanel } from "./components/ActivityPanel";
import { ChangeRequestPanel } from "./components/ChangeRequestPanel";
import { GovernancePanel } from "./components/GovernancePanel";
import { RolesModal } from "./components/RolesModal";
import { RequestRoleControl } from "./components/RequestRoleControl";
import { BulkActionBar } from "./components/BulkActionBar";
import { DraftReviewBar } from "./components/DraftReviewBar";
import { DraftReviewModal, type DraftRow } from "./components/DraftReviewModal";
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
  // Activity / change-history feed: the ActivityPanel now owns its own fetching /
  // keyset paging / filtering. The shell only (a) bumps a refreshKey so the panel
  // re-fetches page 1 whenever the viewer (or anyone) mutates, and (b) records the
  // loaded count + hasMore for the tab badge.
  const [activityCount, setActivityCount] = useState(0);
  const [activityHasMore, setActivityHasMore] = useState(false);
  // Role management (Feature: roles): the catalog (+ per-viewer flags), the
  // viewer-relevant applications (admin sees pending; everyone sees their own),
  // and — for admins — the active grants. All refreshed with the snapshot.
  const [roles, setRoles] = useState<RoleView[]>([]);
  const [roleApplications, setRoleApplications] = useState<RoleApplicationView[]>([]);
  const [roleGrants, setRoleGrants] = useState<RoleGrantView[]>([]);
  const isAdmin = snap?.viewer?.is_admin ?? false;
  const viewerUser = snap?.actor ?? "";
  const refreshRoles = useCallback(() => {
    if (client.listRoles) client.listRoles().then(setRoles).catch(() => setRoles([]));
    if (client.listRoleApplications) {
      // Admins triage the pending queue; everyone sees their own applications.
      const load = isAdmin
        ? client.listRoleApplications("proposed")
        : client.listRoleApplications(undefined, viewerUser);
      load.then(setRoleApplications).catch(() => setRoleApplications([]));
    }
    if (isAdmin && client.listRoleGrants) {
      client.listRoleGrants().then(setRoleGrants).catch(() => setRoleGrants([]));
    } else {
      setRoleGrants([]);
    }
  }, [client, isAdmin, viewerUser]);
  // A monotonically-bumped key handed to ActivityPanel: every time the snapshot
  // settles after a mutation (snap identity changes), bump it so the panel re-fetches
  // its page 1 — keeping the timeline in sync with the live tree without the shell
  // owning the activity fetch itself.
  const [activityRefreshKey, setActivityRefreshKey] = useState(0);
  useEffect(() => {
    if (snap) {
      refreshCRs();
      refreshNotifications();
      refreshRoles();
      setActivityRefreshKey((k) => k + 1);
    }
  }, [refreshCRs, refreshNotifications, refreshRoles, snap]);
  // Every role mutation funnels through dispatch then refreshes the role views
  // (and the snapshot, since a grant can change the viewer's ACL affordances).
  const roleOp = (action: string, params: Record<string, unknown>) => {
    void sheet.dispatch(action, params).then((o) => {
      if (!o.error) {
        refreshRoles();
        void sheet.refetch();
      }
    });
  };
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

  // Decision 1A — branch the cell commit on the snapshot's per-column ACL hint
  // (never re-deriving ACL): an OWNER (can_edit === true) commits directly via the
  // existing updateCell dispatch (real-time, unchanged); a NON-OWNER (can_edit ===
  // false) writes to the server-persisted draft box instead — the value shows
  // locally immediately and is staged for ONE multi-change CR via the review bar.
  // No more instant "Suggestion sent" toast + revert + dot on a single edit.
  const commitCell = (node: SnapshotNode, column: SnapshotColumn, value: unknown) => {
    if (column.can_edit) {
      void sheet.dispatch(
        "updateCell",
        { sheet: sheetName, node: node.name, column: column.name, value },
        { optimisticKey: cellKey(node.name, column.name), optimisticValue: value },
      );
    } else {
      void sheet.commitDraft(node.name, column.name, value);
    }
  };
  // Draft flow — the per-cell "has an unsubmitted draft" predicate handed down to
  // rows/cells for the "unsaved draft" treatment.
  const draftCell = useCallback(
    (node: string, column: string): boolean => sheet.draftKey(node, column),
    [sheet],
  );

  // Schema editor: which data column (if any) the viewer is configuring. The
  // ColumnSettings surface (configure / delete / reassign ownership) was built +
  // tested but never mounted; the header gear opens it here.
  const [editingColumn, setEditingColumn] = useState<SnapshotColumn | null>(null);
  // Row density for long-text cells (line-clamp): compact/comfortable/expand —
  // the pro "row height" control. Drives data-density on the tree card.
  const [density, setDensity] = useState<"compact" | "comfortable" | "expand">("comfortable");
  // Inline label edit (the per-row edit-pencil): which node's label cell to open
  // and a monotonic signal bumped on each click so even re-clicking the SAME row
  // re-opens its editor. TreeTable hands the signal to the matching row's label
  // Cell, which enters edit mode + focuses.
  const [editingNode, setEditingNode] = useState<string | null>(null);
  const [editSignal, setEditSignal] = useState(0);
  const startEditNode = useCallback((node: SnapshotNode) => {
    setEditingNode(node.name);
    setEditSignal((s) => s + 1);
  }, []);
  // Mobile: the agent rail collapses to a bottom drawer toggled by a FAB so the
  // table owns the screen by default (desktop always shows the rail).
  // The agent is a floating chat widget (a bubble bottom-right that opens a popup
  // panel), so it never hogs a docked column — the table always keeps full width.
  // agentOpen toggles the popup; the sidebar stays MOUNTED (CSS-hidden) so the
  // transcript survives close/reopen. Same pattern on desktop and mobile.
  const [agentOpen, setAgentOpen] = useState(false);
  // Global Roles admin modal (admin-only, header-launched). Open/close lives here
  // so the header button toggles it and the modal renders only when open.
  const [rolesOpen, setRolesOpen] = useState(false);
  // Draft flow — whether the Draft Review modal is open (the bar opens it).
  const [draftReviewOpen, setDraftReviewOpen] = useState(false);

  // Draft flow — resolve each staged DraftView into a presentation DraftRow: the
  // column LABEL, node LABEL, the OLD (authoritative snapshot) value, and the
  // resolved approver (the target column's column_owner). The modal then groups
  // by approver and renders the old → new diff.
  const draftRows: DraftRow[] = useMemo(() => {
    if (!snap) return [];
    return sheet.draftList.map((d) => {
      const col = snap.columns.find((c) => c.name === d.column);
      const node = snap.nodes.find((n) => n.name === d.node);
      const lbl = node?.label;
      const nodeLabel = (Array.isArray(lbl) ? lbl.join(", ") : lbl) || d.node;
      return {
        key: d.key,
        node: d.node,
        column: d.column,
        columnLabel: col?.label ?? d.column,
        nodeLabel,
        // The authoritative snapshot value (NOT the draft-overlaid one).
        oldValue: node?.values?.[d.column],
        newValue: d.value,
        approver: col?.column_owner ?? "approver",
      };
    });
  }, [snap, sheet.draftList]);

  // Draft flow — submit the whole draft box as ONE multi-change CR, then close the
  // modal and refresh the CR inbox / activity so the new suggestion shows up.
  const submitDrafts = useCallback(() => {
    void sheet.submitDrafts().then((o) => {
      if (!o.error) {
        setDraftReviewOpen(false);
        refreshCRs();
        setActivityRefreshKey((k) => k + 1);
      }
    });
  }, [sheet, refreshCRs]);

  // Draft flow — nav-away guard: warn before leaving while drafts are unsubmitted
  // (they're persisted server-side, but the user almost certainly meant to submit
  // them). Active only while draftCount > 0; the modern API needs preventDefault +
  // a returnValue assignment for the browser to show its confirm.
  useEffect(() => {
    if (sheet.draftCount === 0) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
      return "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [sheet.draftCount]);
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

  // addNode funnels through dispatch like every mutation. The executor decides
  // direct-vs-CR by ACL — we DON'T gate the button on ownership (a non-owner's
  // click just files a CR, same as Suggest column). On an executed add, refetch
  // so the new (label-less) node appears for inline naming; on a suggested add a
  // CR is filed. Either way refresh activity + the CR inbox so the result shows.
  const addNode = (parent: string | null) => {
    void sheet.dispatch("addNode", { sheet: sheetName, parent }).then((o) => {
      if (o.kind === "executed") void sheet.refetch();
      setActivityRefreshKey((k) => k + 1);
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

  // Global Roles admin (IA fix). Role data is SITE-WIDE — listRoles /
  // listRoleApplications / listRoleGrants take no sheet — so role administration is
  // NOT a per-sheet Governance tab anymore: an admin-only header button opens the
  // RolesModal (assign/revoke + applications inbox). The badge counts pending
  // applications so the admin sees actionable role work at a glance.
  const pendingRoleApplications = roleApplications.filter((a) => a.status === "proposed").length;

  // Activity tab (change history). Always provided when the sheet has loaded — an
  // always-on slot keeps the governance panel reachable (the ActivityPanel renders
  // its own empty state). The panel self-fetches/pages/filters; refreshKey re-runs
  // page 1 after a mutation, and onCount feeds the tab badge (count + hasMore).
  const activitySlot = snap ? (
    <ActivityPanel
      client={client}
      sheet={sheetName}
      refreshKey={activityRefreshKey}
      onCount={(n, more) => {
        setActivityCount(n);
        setActivityHasMore(more);
      }}
    />
  ) : null;

  return (
    <main className="arbor-app">
      <header className="arbor-header">
        <div className="arbor-header-titles">
          {/* Back to the sheet-list home. Navigation is URL-driven: the home is the
              same path with no ?sheet= param (index.tsx renders <SheetList/> then). */}
          <a
            className="arbor-back-link"
            data-testid="back-to-sheets"
            href={typeof window !== "undefined" ? window.location.pathname : "/"}
          >
            ‹ All sheets
          </a>
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
            {/* "Request a role" — the user self-application control (Feature:
                roles), available to every user. Renders nothing when there is
                nothing to request and no roles held. */}
            <RequestRoleControl roles={roles} onApply={(p) => roleOp("applyForRole", p)} />
            {/* Global Roles admin (admin-only). Role data is site-wide, so this
                opens a MODAL rather than living in the per-sheet Governance panel.
                Badge = pending applications so the admin sees actionable work. */}
            {isAdmin && (
              <button
                type="button"
                className="arbor-roles-admin-btn"
                data-testid="roles-admin-button"
                aria-haspopup="dialog"
                aria-expanded={rolesOpen}
                onClick={() => setRolesOpen(true)}
              >
                Roles
                {pendingRoleApplications > 0 && (
                  <span className="arbor-count">{pendingRoleApplications}</span>
                )}
              </button>
            )}
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
                onSubmit={(params) =>
                  // Mirror columnOp: a suggested add-column files a CR (refresh
                  // the inbox so it shows immediately), a direct add changes the
                  // schema (refetch so the new column re-renders). Either way an
                  // executed op + a filed CR both surface a row in Activity.
                  void sheet.dispatch("addColumn", params).then((o) => {
                    if (o.kind === "executed") void sheet.refetch();
                    refreshCRs();
                    setActivityRefreshKey((k) => k + 1);
                  })
                }
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
              {/* Row-density control: clamp long-text cells to 2/3 lines or
                  expand them — keeps the dense matrix scannable (UX review D2). */}
              <div className="arbor-density" role="group" aria-label="Row density" data-testid="density-toggle">
                {(["compact", "comfortable", "expand"] as const).map((d) => (
                  <button
                    key={d}
                    type="button"
                    aria-pressed={density === d}
                    data-testid={`density-${d}`}
                    onClick={() => setDensity(d)}
                  >
                    {d === "compact" ? "Compact" : d === "comfortable" ? "Cozy" : "Expand"}
                  </button>
                ))}
              </div>
            </div>
            <div className="arbor-tree-card" data-density={density}>
              <TreeTable
                columns={sheet.columns}
                nodes={sheet.nodes}
                labelColumn={snap.label_column}
                collapsed={sheet.collapsed}
                onToggle={sheet.toggle}
                pendingCell={pendingCell}
                pendingTitle={pendingTitle}
                pendingCount={pendingCount}
                draftCell={draftCell}
                isPendingMove={isPendingMove}
                onCommitCell={commitCell}
                onMove={move}
                onColumnSettings={setEditingColumn}
                onDeleteNode={del}
                onAddChild={(n) => addNode(n.name)}
                onAddSibling={(n) => addNode(n.parent ?? null)}
                onEdit={startEditNode}
                editingNode={editingNode}
                editSignal={editSignal}
                onAddNode={() => addNode(null)}
              />
            </div>
            {/* Draft flow — the "Review N change(s)" bar. Mounted ONLY while the
                viewer has >=1 unsubmitted draft (owners commit directly and never
                see it). Clicking it opens the Draft Review modal. */}
            {sheet.draftCount > 0 && (
              <DraftReviewBar count={sheet.draftCount} onReview={() => setDraftReviewOpen(true)} />
            )}
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
              activityCount={activityCount}
              activityHasMore={activityHasMore}
              changeRequests={crSlot}
              notifications={notificationSlot}
              delegations={delegationSlot}
              activity={activitySlot}
            />
          </section>
          {/* Floating agent widget: a bubble pinned bottom-right that opens a popup
              panel (same on desktop + mobile). The table always keeps full width —
              no docked column. The sidebar stays mounted (the popup is CSS-hidden
              when closed) so the transcript survives close/reopen. */}
          <div
            className={`arbor-agent-dock${agentOpen ? " is-open" : ""}`}
            data-testid="agent-dock"
          >
            <div className="arbor-agent-popup" role="dialog" aria-label="Agent panel">
              <AgentSidebar
                client={client}
                sheet={sheetName}
                onActionObserved={() => void sheet.refetch()}
              />
            </div>
            <button
              type="button"
              className="arbor-agent-fab"
              data-testid="agent-fab"
              aria-expanded={agentOpen}
              aria-label={agentOpen ? "Close agent" : "Ask the agent"}
              title={agentOpen ? "Close agent" : "Ask the agent"}
              onClick={() => setAgentOpen((o) => !o)}
            >
              {agentOpen ? (
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
          {/* Global Roles admin modal — admin-only, header-launched. Mounted only
              when open; reuses the .arbor-modal shell (like ColumnSettings). Every
              write funnels through roleOp (refresh roles + snapshot). */}
          {isAdmin && rolesOpen && (
            <RolesModal
              isAdmin={isAdmin}
              roles={roles}
              grants={roleGrants}
              applications={roleApplications}
              onClose={() => setRolesOpen(false)}
              onAssign={(p) => roleOp("assignRole", p)}
              onRevoke={(p) => roleOp("revokeRole", p)}
              onApprove={(p) => roleOp("approveRoleApplication", p)}
              onReject={(p) => roleOp("rejectRoleApplication", p)}
              onWithdraw={(p) => roleOp("withdrawRoleApplication", p)}
            />
          )}
          {/* Draft flow — the Draft Review modal. Mounted only when open; reuses
              the shared .arbor-modal shell. Groups the staged drafts by resolved
              approver, shows each old → new diff, and routes discard/submit
              through useSheet. */}
          {draftReviewOpen && (
            <DraftReviewModal
              drafts={draftRows}
              onClose={() => setDraftReviewOpen(false)}
              onSubmit={submitDrafts}
              onDiscardOne={(node, column) => void sheet.discardDraft(node, column)}
              onDiscardAll={() => {
                void sheet.discardAllDrafts();
                setDraftReviewOpen(false);
              }}
            />
          )}
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
