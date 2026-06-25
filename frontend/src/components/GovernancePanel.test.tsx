// Bench-free unit spec for the consolidated Governance panel (P1).
//
// GovernancePanel is the single panel below the tree table that tabs between the
// three existing governance surfaces. It does NOT re-implement them: each tab
// renders the EXISTING content (the ChangeRequestPanel list = data-testid
// "cr-inbox", the NotificationItem list = "notification-inbox", and
// DelegationControl = "delegation-control"). The panel owns: the three tab
// buttons + count badges, the default-active-tab rule, the all-zero collapse,
// and switching which inbox is mounted on click.
//
// This mirrors how App will wire it: App computes the three counts and hands the
// panel the three already-built content nodes as named slots; only the active
// slot is mounted. The component does not exist yet — this file is RED until it
// does. We do NOT implement it here.

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { GovernancePanel } from "./GovernancePanel";

// Stand-ins for the real content each tab renders. We assert on these testids
// to prove the panel mounts the correct slot — not on the real components, so
// this stays bench-free and decoupled from CR/notification/delegation internals.
const changeRequestsSlot = <div data-testid="cr-inbox">CR list</div>;
const notificationsSlot = <div data-testid="notification-inbox">Notifications list</div>;
const delegationsSlot = <div data-testid="delegation-control">Delegation control</div>;

const rolesSlot = <div data-testid="roles-panel">Roles panel</div>;

const activitySlot = <div data-testid="activity-panel">Activity timeline</div>;

// Render helper mirroring App's call shape: counts + content slots. The Roles tab
// is opt-in (roles slot null by default) so the original three-tab specs hold.
// The Activity tab is likewise opt-in (activity slot null by default).
function renderPanel(counts: {
  cr: number;
  notifications: number;
  delegations: number;
  roles?: number;
  rolesSlot?: React.ReactNode;
  activity?: number;
  activitySlot?: React.ReactNode;
}) {
  return render(
    <GovernancePanel
      changeRequestCount={counts.cr}
      notificationCount={counts.notifications}
      delegationCount={counts.delegations}
      roleCount={counts.roles ?? 0}
      activityCount={counts.activity ?? 0}
      changeRequests={changeRequestsSlot}
      notifications={notificationsSlot}
      delegations={delegationsSlot}
      roles={counts.rolesSlot ?? null}
      activity={counts.activitySlot ?? null}
    />,
  );
}

describe("GovernancePanel — tabs + count badges", () => {
  it("renders all three tabs, each labelled with its count badge", () => {
    renderPanel({ cr: 3, notifications: 2, delegations: 1 });

    // All three tab controls exist regardless of counts.
    const crTab = screen.getByRole("tab", { name: /change requests/i });
    const notifTab = screen.getByRole("tab", { name: /notifications/i });
    const delegTab = screen.getByRole("tab", { name: /delegations/i });
    expect(crTab).toBeInTheDocument();
    expect(notifTab).toBeInTheDocument();
    expect(delegTab).toBeInTheDocument();

    // Each tab surfaces its count via the muted .arbor-count badge.
    const badges = document.querySelectorAll(".arbor-count");
    const badgeText = Array.from(badges).map((b) => b.textContent?.trim());
    expect(badgeText).toEqual(expect.arrayContaining(["3", "2", "1"]));

    // The badges live inside their respective tabs.
    expect(crTab.querySelector(".arbor-count")?.textContent?.trim()).toBe("3");
    expect(notifTab.querySelector(".arbor-count")?.textContent?.trim()).toBe("2");
    expect(delegTab.querySelector(".arbor-count")?.textContent?.trim()).toBe("1");
  });
});

describe("GovernancePanel — default active tab (first non-zero, preferring Change Requests)", () => {
  it("defaults to Change Requests when its count > 0", () => {
    renderPanel({ cr: 2, notifications: 5, delegations: 5 });

    // Only the active tab's content mounts.
    expect(screen.getByTestId("cr-inbox")).toBeInTheDocument();
    expect(screen.queryByTestId("notification-inbox")).toBeNull();
    expect(screen.queryByTestId("delegation-control")).toBeNull();
  });

  it("falls through to Notifications when CR count is 0 but notifications > 0", () => {
    renderPanel({ cr: 0, notifications: 4, delegations: 2 });

    expect(screen.getByTestId("notification-inbox")).toBeInTheDocument();
    expect(screen.queryByTestId("cr-inbox")).toBeNull();
    expect(screen.queryByTestId("delegation-control")).toBeNull();
  });

  it("falls through to Delegations when only delegations > 0", () => {
    renderPanel({ cr: 0, notifications: 0, delegations: 3 });

    expect(screen.getByTestId("delegation-control")).toBeInTheDocument();
    expect(screen.queryByTestId("cr-inbox")).toBeNull();
    expect(screen.queryByTestId("notification-inbox")).toBeNull();
  });
});

describe("GovernancePanel — all-zero collapse", () => {
  it("shows the quiet 'No pending governance' line and mounts no inbox content", () => {
    renderPanel({ cr: 0, notifications: 0, delegations: 0 });

    expect(screen.getByText(/no pending governance/i)).toBeInTheDocument();

    // None of the tab content slots mount when everything is empty.
    expect(screen.queryByTestId("cr-inbox")).toBeNull();
    expect(screen.queryByTestId("notification-inbox")).toBeNull();
    expect(screen.queryByTestId("delegation-control")).toBeNull();
  });

  it("still renders the three tab headers even when collapsed", () => {
    renderPanel({ cr: 0, notifications: 0, delegations: 0 });

    expect(screen.getByRole("tab", { name: /change requests/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /notifications/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /delegations/i })).toBeInTheDocument();
  });
});

describe("GovernancePanel — Roles tab (Feature: roles)", () => {
  it("shows a Roles tab only when a roles slot is provided", () => {
    renderPanel({ cr: 1, notifications: 0, delegations: 0 });
    expect(screen.queryByRole("tab", { name: /roles/i })).toBeNull();

    renderPanel({ cr: 1, notifications: 0, delegations: 0, roles: 2, rolesSlot });
    expect(screen.getByRole("tab", { name: /roles/i })).toBeInTheDocument();
  });

  it("keeps the panel open (no collapse) for an admin with a roles slot but zero counts", () => {
    renderPanel({ cr: 0, notifications: 0, delegations: 0, roles: 0, rolesSlot });
    // Not collapsed: the quiet line is absent and the Roles tab is reachable
    // (clicking it mounts the roles panel even at count 0).
    expect(screen.queryByText(/no pending governance/i)).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: /roles/i }));
    expect(screen.getByTestId("roles-panel")).toBeInTheDocument();
  });

  it("clicking Roles mounts the roles panel only", () => {
    renderPanel({ cr: 2, notifications: 1, delegations: 1, roles: 1, rolesSlot });
    fireEvent.click(screen.getByRole("tab", { name: /roles/i }));
    expect(screen.getByTestId("roles-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("cr-inbox")).toBeNull();
  });
});

describe("GovernancePanel — Activity tab (change history)", () => {
  it("shows an Activity tab only when an activity slot is provided", () => {
    renderPanel({ cr: 1, notifications: 0, delegations: 0 });
    expect(screen.queryByRole("tab", { name: /activity/i })).toBeNull();

    renderPanel({ cr: 1, notifications: 0, delegations: 0, activity: 4, activitySlot });
    expect(screen.getByRole("tab", { name: /activity/i })).toBeInTheDocument();
  });

  it("renders Activity as the LAST tab", () => {
    renderPanel({
      cr: 1,
      notifications: 0,
      delegations: 0,
      roles: 0,
      rolesSlot,
      activity: 3,
      activitySlot,
    });
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent ?? "");
    expect(tabs[tabs.length - 1]).toMatch(/activity/i);
  });

  it("surfaces activityCount as a subtle badge on the tab", () => {
    renderPanel({ cr: 1, notifications: 0, delegations: 0, activity: 7, activitySlot });
    const tab = screen.getByRole("tab", { name: /activity/i });
    expect(tab.querySelector(".arbor-count")?.textContent?.trim()).toBe("7");
  });

  it("keeps the panel open (no collapse) for a provided Activity slot at zero counts", () => {
    // Activity is history, not a queue — a provided slot keeps the panel reachable
    // but must NOT manufacture a phantom 'pending' state. The quiet collapse line
    // is absent and the Activity tab can be clicked to mount the timeline.
    renderPanel({ cr: 0, notifications: 0, delegations: 0, activity: 0, activitySlot });
    expect(screen.queryByText(/no pending governance/i)).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: /activity/i }));
    expect(screen.getByTestId("activity-panel")).toBeInTheDocument();
  });

  it("clicking Activity mounts the timeline only", () => {
    renderPanel({ cr: 2, notifications: 1, delegations: 1, activity: 2, activitySlot });
    fireEvent.click(screen.getByRole("tab", { name: /activity/i }));
    expect(screen.getByTestId("activity-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("cr-inbox")).toBeNull();
    expect(screen.queryByTestId("notification-inbox")).toBeNull();
  });

  it("does not auto-select Activity even when it is the only non-zero count", () => {
    // History should never steal default focus from the triage queues; with all
    // queues empty the panel stays on its CR fallback, not Activity.
    renderPanel({ cr: 0, notifications: 0, delegations: 0, activity: 9, activitySlot });
    expect(screen.queryByTestId("activity-panel")).toBeNull();
    expect(screen.getByTestId("governance-panel").getAttribute("data-active-tab")).toBe(
      "changeRequests",
    );
  });
});

describe("GovernancePanel — active tab follows defaultKey until the user picks", () => {
  // Reproduces the live default-tab bug: at mount the snapshot already has a
  // delegation (delegations:1) while the Change-Request list is still loading
  // (cr:0), so defaultKey resolves to "delegations". Once the 6 CRs arrive the
  // active tab must re-sync to Change Requests — the admin's main triage queue —
  // because the user has not manually picked a tab yet.
  it("late-loading Change Requests win: re-render moves the active tab to CR", () => {
    const { rerender } = render(
      <GovernancePanel
        changeRequestCount={0}
        notificationCount={0}
        delegationCount={1}
        roleCount={0}
        changeRequests={changeRequestsSlot}
        notifications={notificationsSlot}
        delegations={delegationsSlot}
        roles={null}
        activity={null}
        activityCount={0}
      />,
    );

    // At mount, only the delegation control is mounted (defaultKey = delegations).
    expect(screen.getByTestId("delegation-control")).toBeInTheDocument();
    expect(screen.queryByTestId("cr-inbox")).toBeNull();

    // The 6 CRs finish loading. defaultKey now resolves to Change Requests.
    rerender(
      <GovernancePanel
        changeRequestCount={6}
        notificationCount={0}
        delegationCount={1}
        roleCount={0}
        changeRequests={changeRequestsSlot}
        notifications={notificationsSlot}
        delegations={delegationsSlot}
        roles={null}
        activity={null}
        activityCount={0}
      />,
    );

    // Auto-sync moves the active/mounted tab to Change Requests.
    expect(screen.getByTestId("cr-inbox")).toBeInTheDocument();
    expect(screen.queryByTestId("delegation-control")).toBeNull();
    expect(screen.getByTestId("governance-panel").getAttribute("data-active-tab")).toBe(
      "changeRequests",
    );
  });

  it("a manual pick sticks: re-rendering with new counts does not move away from it", () => {
    const { rerender } = render(
      <GovernancePanel
        changeRequestCount={0}
        notificationCount={0}
        delegationCount={1}
        roleCount={0}
        changeRequests={changeRequestsSlot}
        notifications={notificationsSlot}
        delegations={delegationsSlot}
        roles={null}
        activity={null}
        activityCount={0}
      />,
    );

    // User deliberately clicks Delegations (even though it is already the default).
    fireEvent.click(screen.getByRole("tab", { name: /delegations/i }));
    expect(screen.getByTestId("delegation-control")).toBeInTheDocument();

    // CRs arrive afterwards — but the user's choice must permanently win.
    rerender(
      <GovernancePanel
        changeRequestCount={6}
        notificationCount={0}
        delegationCount={1}
        roleCount={0}
        changeRequests={changeRequestsSlot}
        notifications={notificationsSlot}
        delegations={delegationsSlot}
        roles={null}
        activity={null}
        activityCount={0}
      />,
    );

    // Still on Delegations; auto-sync stays disabled after a manual pick.
    expect(screen.getByTestId("delegation-control")).toBeInTheDocument();
    expect(screen.queryByTestId("cr-inbox")).toBeNull();
    expect(screen.getByTestId("governance-panel").getAttribute("data-active-tab")).toBe(
      "delegations",
    );
  });
});

describe("GovernancePanel — clicking a tab switches the mounted inbox", () => {
  it("clicking Notifications swaps CR content out for the notification inbox", () => {
    renderPanel({ cr: 2, notifications: 1, delegations: 1 });

    // Starts on Change Requests (default).
    expect(screen.getByTestId("cr-inbox")).toBeInTheDocument();
    expect(screen.queryByTestId("notification-inbox")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: /notifications/i }));

    // Only the notification inbox is now mounted.
    expect(screen.getByTestId("notification-inbox")).toBeInTheDocument();
    expect(screen.queryByTestId("cr-inbox")).toBeNull();
    expect(screen.queryByTestId("delegation-control")).toBeNull();
  });

  it("clicking Delegations mounts the delegation control only", () => {
    renderPanel({ cr: 2, notifications: 1, delegations: 1 });

    fireEvent.click(screen.getByRole("tab", { name: /delegations/i }));

    expect(screen.getByTestId("delegation-control")).toBeInTheDocument();
    expect(screen.queryByTestId("cr-inbox")).toBeNull();
    expect(screen.queryByTestId("notification-inbox")).toBeNull();
  });
});
