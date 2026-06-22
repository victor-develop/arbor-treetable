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

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { GovernancePanel } from "./GovernancePanel";

// Stand-ins for the real content each tab renders. We assert on these testids
// to prove the panel mounts the correct slot — not on the real components, so
// this stays bench-free and decoupled from CR/notification/delegation internals.
const changeRequestsSlot = <div data-testid="cr-inbox">CR list</div>;
const notificationsSlot = <div data-testid="notification-inbox">Notifications list</div>;
const delegationsSlot = <div data-testid="delegation-control">Delegation control</div>;

// Render helper mirroring App's call shape: three counts + three content slots.
function renderPanel(counts: { cr: number; notifications: number; delegations: number }) {
  return render(
    <GovernancePanel
      changeRequestCount={counts.cr}
      notificationCount={counts.notifications}
      delegationCount={counts.delegations}
      changeRequests={changeRequestsSlot}
      notifications={notificationsSlot}
      delegations={delegationsSlot}
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
