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

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

export type GovernanceTabKey =
  | "changeRequests"
  | "notifications"
  | "delegations"
  | "roles"
  | "activity";

export function GovernancePanel({
  changeRequestCount,
  notificationCount,
  delegationCount,
  roleCount,
  activityCount,
  changeRequests,
  notifications,
  delegations,
  roles,
  activity,
}: {
  changeRequestCount: number;
  notificationCount: number;
  delegationCount: number;
  // Count badge for the Roles tab: pending applications the viewer can act on
  // (admins) + the viewer's own open applications. 0 when there is no role work.
  roleCount: number;
  // Count badge for the Activity tab: the number of loaded history events. Subtle
  // metadata only — Activity is history, not a queue, so it never drives the
  // default-tab rule nor keeps a "pending" state alive (see allZero below).
  activityCount: number;
  // already-built content nodes; only the active one is mounted
  changeRequests: ReactNode;
  notifications: ReactNode;
  delegations: ReactNode;
  // Roles tab body: admin assign/revoke + applications inbox (Feature: roles).
  // null when there is nothing to show (no admin panel and no applications).
  roles: ReactNode;
  // Activity tab body: the change-history timeline. Always provided by the host
  // (an always-mounted slot keeps the panel reachable), or null to hide the tab.
  activity: ReactNode;
}): JSX.Element {
  // Fixed order; CR first so it wins ties on default selection. Activity is LAST —
  // history sits after every actionable queue and never wins default focus.
  const tabs = useMemo(
    () =>
      [
        { key: "changeRequests" as const, label: "Change Requests", count: changeRequestCount, slot: changeRequests },
        { key: "notifications" as const, label: "Notifications", count: notificationCount, slot: notifications },
        { key: "delegations" as const, label: "Delegations", count: delegationCount, slot: delegations },
        { key: "roles" as const, label: "Roles", count: roleCount, slot: roles },
        { key: "activity" as const, label: "Activity", count: activityCount, slot: activity },
      ].filter((t) => t.slot != null),
    [
      changeRequestCount,
      notificationCount,
      delegationCount,
      roleCount,
      activityCount,
      changeRequests,
      notifications,
      delegations,
      roles,
      activity,
    ],
  );

  // Default = first ACTIONABLE tab with count>0 (CR preferred by order); fall back
  // to CR. Activity is history, not a queue, so it never wins default focus —
  // even when it is the only non-zero count, the panel stays on its CR fallback.
  const defaultKey = useMemo<GovernanceTabKey>(
    () => tabs.find((t) => t.key !== "activity" && t.count > 0)?.key ?? "changeRequests",
    [tabs],
  );
  const [active, setActive] = useState<GovernanceTabKey>(defaultKey);

  // The active tab FOLLOWS defaultKey as counts stream in (e.g. the CR list
  // loads after the snapshot already supplied a delegation), so the highest
  // priority queue with work always wins — UNTIL the user manually picks a tab,
  // after which their choice is permanent and auto-sync stops for good.
  const userPicked = useRef(false);
  useEffect(() => {
    if (!userPicked.current) {
      setActive(defaultKey);
    }
  }, [defaultKey]);

  // Collapse to the quiet line only when there is genuinely nothing to act on AND
  // no always-on slot to reach. A provided roles slot (admin panel, or a user with
  // applications) keeps the panel open even at count 0; likewise a provided
  // activity slot keeps the panel reachable so the change history is always one
  // click away. Activity does NOT add to any count — it is history, not a queue —
  // so it never forces a phantom "pending" state, it only blocks the collapse.
  const allZero =
    changeRequestCount === 0 &&
    notificationCount === 0 &&
    delegationCount === 0 &&
    roles == null &&
    activity == null;
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
            onClick={() => {
              userPicked.current = true;
              setActive(t.key);
            }}
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
