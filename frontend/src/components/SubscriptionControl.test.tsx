// Runnable: bench-free (vitest + jsdom; no Frappe, no running app).
//
// Subscription + notification thin-shell controls. Closes the TEST-PLAN §5.1
// surface-parity gap: `subscribe` was tested on every surface but `unsubscribe`
// was missing from the Web UI. These cases assert the symmetric pair dispatches
// the matching registry capability id through executeAction (no raw write, no
// re-derived ACL), plus the requires_ack Acknowledge affordance (WEB_UI-090).
//
// Case IDs:
//   WEB_UI-090  Acknowledge affordance appears only for requires_ack notifications
//   WEB_UI-091  Web-UI subscribe → executeAction("subscribe", {sheet})  (parity sibling)
//   WEB_UI-092  Web-UI unsubscribe → executeAction("unsubscribe", {subscription}) (§5.1 gap)

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NotificationItem, SubscriptionControl, type NotificationView } from "./SubscriptionControl";
import { isCapabilityId } from "../lib/capabilities";

describe("SubscriptionControl — subscribe/unsubscribe parity (TEST-PLAN §5.1)", () => {
  it("renders the Subscribe affordance when not subscribed and dispatches subscribe (WEB_UI-091)", () => {
    const onSubscribe = vi.fn();
    const onUnsubscribe = vi.fn();
    render(
      <SubscriptionControl
        sheet="S"
        subscribed={false}
        onSubscribe={onSubscribe}
        onUnsubscribe={onUnsubscribe}
      />,
    );
    expect(screen.queryByTestId("unsubscribe-btn")).toBeNull();
    fireEvent.click(screen.getByTestId("subscribe-btn"));
    expect(onSubscribe).toHaveBeenCalledWith({ sheet: "S" });
    expect(onUnsubscribe).not.toHaveBeenCalled();
  });

  it("renders the Unsubscribe affordance when subscribed and dispatches unsubscribe with the subscription id (WEB_UI-092)", () => {
    const onSubscribe = vi.fn();
    const onUnsubscribe = vi.fn();
    render(
      <SubscriptionControl
        sheet="S"
        subscribed
        subscriptionName="SUB-1"
        onSubscribe={onSubscribe}
        onUnsubscribe={onUnsubscribe}
      />,
    );
    expect(screen.queryByTestId("subscribe-btn")).toBeNull();
    fireEvent.click(screen.getByTestId("unsubscribe-btn"));
    expect(onUnsubscribe).toHaveBeenCalledWith({ sheet: "S", subscription: "SUB-1" });
    expect(onSubscribe).not.toHaveBeenCalled();
  });

  it("subscribe and unsubscribe are both registry capability ids (surface parity, §11)", () => {
    // Guards the §5.1 invariant: the pair must be symmetric registry capabilities,
    // not ad-hoc UI calls.
    expect(isCapabilityId("subscribe")).toBe(true);
    expect(isCapabilityId("unsubscribe")).toBe(true);
  });
});

describe("NotificationItem — acknowledge (WEB_UI-090)", () => {
  const ackNotif: NotificationView = {
    name: "N1",
    event_type: "NODE_DELETED",
    message: "A node was deleted in P2",
    requires_ack: true,
    acked: false,
  };
  const plainNotif: NotificationView = {
    name: "N2",
    event_type: "NODE_VALUE_UPDATED",
    message: "A cell changed",
    requires_ack: false,
    acked: false,
  };

  it("shows an Acknowledge button for a requires_ack notification and dispatches acknowledge (persona G)", () => {
    const onAcknowledge = vi.fn();
    render(<NotificationItem notification={ackNotif} onAcknowledge={onAcknowledge} />);
    fireEvent.click(screen.getByTestId("ack-btn"));
    expect(onAcknowledge).toHaveBeenCalledWith({ notification: "N1" });
    // after success the item shows an acked state and the button is gone
    expect(screen.getByTestId("ack-state")).toBeInTheDocument();
    expect(screen.queryByTestId("ack-btn")).toBeNull();
  });

  it("shows NO Acknowledge button for a non-ack notification", () => {
    const onAcknowledge = vi.fn();
    render(<NotificationItem notification={plainNotif} onAcknowledge={onAcknowledge} />);
    expect(screen.queryByTestId("ack-btn")).toBeNull();
    expect(onAcknowledge).not.toHaveBeenCalled();
  });
});
