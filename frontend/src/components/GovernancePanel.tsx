// Consolidated Governance inbox (SPEC P1). One panel, three tabs — Change
// Requests / Notifications / Delegations — replacing the three stacked sections
// that used to fight for the prime post-table slot. The panel owns ONLY tab
// selection; each tab's body is supplied by the host as a ReactNode slot, so the
// existing ChangeRequestPanel list, NotificationItem list, and DelegationControl
// render UNCHANGED inside (this panel re-derives no governance state).
//
// Default active tab = the first tab whose count > 0, preferring Change
// Requests; if every count is zero the body collapses to one quiet line while
// the tab strip still renders. Only the active tab's slot is mounted (inactive
// slots are not rendered at all — so e2e must click the tab first; handled in
// the e2e-update step).

import { useMemo, useState, type ReactNode } from "react";

export type GovernanceTabKey = "changeRequests" | "notifications" | "delegations";

export function GovernancePanel({
  changeRequestCount,
  notificationCount,
  delegationCount,
  changeRequests,
  notifications,
  delegations,
}: {
  changeRequestCount: number;
  notificationCount: number;
  delegationCount: number;
  // already-built content nodes; only the active one is mounted
  changeRequests: ReactNode;
  notifications: ReactNode;
  delegations: ReactNode;
}): JSX.Element {
  // Fixed order; CR first so it wins ties on default selection.
  const tabs = useMemo(
    () =>
      [
        { key: "changeRequests" as const, label: "Change Requests", count: changeRequestCount, slot: changeRequests },
        { key: "notifications" as const, label: "Notifications", count: notificationCount, slot: notifications },
        { key: "delegations" as const, label: "Delegations", count: delegationCount, slot: delegations },
      ],
    [changeRequestCount, notificationCount, delegationCount, changeRequests, notifications, delegations],
  );

  // Default = first tab with count>0 (CR preferred by order); fall back to CR.
  const defaultKey = useMemo<GovernanceTabKey>(
    () => tabs.find((t) => t.count > 0)?.key ?? "changeRequests",
    [tabs],
  );
  const [active, setActive] = useState<GovernanceTabKey>(defaultKey);

  const allZero = changeRequestCount === 0 && notificationCount === 0 && delegationCount === 0;
  const activeTab = tabs.find((t) => t.key === active) ?? tabs[0];

  return (
    <section
      className={`arbor-governance${allZero ? " is-collapsed" : ""}`}
      data-testid="governance-panel"
      data-active-tab={active}
      data-collapsed={allZero}
    >
      <div className="arbor-governance-tabs" role="tablist" data-testid="governance-tabs">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={active === t.key}
            className={`arbor-governance-tab${active === t.key ? " is-active" : ""}${
              t.count === 0 ? " is-zero" : ""
            }`}
            data-testid={`governance-tab-${t.key}`}
            data-count={t.count}
            onClick={() => setActive(t.key)}
          >
            {t.label}{" "}
            {/* Zero-count badges de-emphasize (muted) so attention routes to tabs
                with work; non-zero badges stay at the standard weight. */}
            <span className={`arbor-count${t.count === 0 ? " is-zero" : ""}`}>{t.count}</span>
          </button>
        ))}
      </div>

      {allZero ? (
        // Entire inbox collapses to a slim header bar + one quiet line.
        <p className="arbor-governance-empty" data-testid="governance-empty">
          No pending governance
        </p>
      ) : (
        // Always mount the active tab's slot (even at count 0) — each slot renders
        // its OWN empty state (e.g. "No open change requests"), and the Change
        // Requests slot also hosts the bulk-action bar whose post-run summary must
        // survive the queue draining to zero. The panel owns tab selection only,
        // never a tab's empty rendering.
        <div
          className="arbor-governance-body"
          role="tabpanel"
          data-testid={`governance-body-${activeTab.key}`}
        >
          {activeTab.slot}
        </div>
      )}
    </section>
  );
}
