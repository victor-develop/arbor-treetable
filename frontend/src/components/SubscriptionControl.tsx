// Subscription + notification controls — thin shell over executeAction
// (ARCHITECTURE §4.1(a)). Subscribe/unsubscribe form the symmetric pair the
// §5.1 surface-parity gap calls for: `subscribe` is exercised on every surface,
// `unsubscribe` was missing from the Web UI (TEST-PLAN §5). Both dispatch a
// registry capability id — the component re-derives no ACL and performs no raw
// write. The Acknowledge affordance (WEB_UI-090) appears only for
// requires_ack notifications and dispatches `acknowledge`.

import { useState } from "react";

// Single source of truth in api.ts; re-export so existing imports keep working.
import type { NotificationView } from "../api";
export type { NotificationView };

// The subscribe/unsubscribe toggle. `subscribed` is supplied by the snapshot
// (the server is the source of truth); the control merely dispatches the
// matching capability and lets the host refetch.
export function SubscriptionControl({
  sheet,
  subscribed,
  subscriptionName,
  onSubscribe,
  onUnsubscribe,
}: {
  sheet: string;
  subscribed: boolean;
  // present when subscribed — the Subscription doc id to unsubscribe
  subscriptionName?: string;
  onSubscribe: (params: Record<string, unknown>) => void;
  onUnsubscribe: (params: Record<string, unknown>) => void;
}): JSX.Element {
  return (
    <div className="arbor-subscription" data-testid="subscription-control" data-subscribed={subscribed}>
      {subscribed ? (
        <button
          type="button"
          data-testid="unsubscribe-btn"
          onClick={() => onUnsubscribe({ sheet, subscription: subscriptionName })}
        >
          Unsubscribe
        </button>
      ) : (
        <button
          type="button"
          data-testid="subscribe-btn"
          onClick={() => onSubscribe({ sheet })}
        >
          Subscribe
        </button>
      )}
    </div>
  );
}

// In-app notification item. Acknowledge shows only for requires_ack items
// (persona G, WEB_UI-090); non-ack notifications render no such button.
export function NotificationItem({
  notification,
  onAcknowledge,
}: {
  notification: NotificationView;
  onAcknowledge: (params: Record<string, unknown>) => void;
}): JSX.Element {
  const [acked, setAcked] = useState(notification.acked);
  return (
    <li
      className="arbor-notification"
      data-testid={`notification-${notification.name}`}
      data-event-type={notification.event_type}
      data-acked={acked}
    >
      <span className="arbor-notification-msg">{notification.message}</span>
      {notification.requires_ack &&
        (acked ? (
          <span data-testid="ack-state">Acknowledged</span>
        ) : (
          <button
            type="button"
            data-testid="ack-btn"
            onClick={() => {
              onAcknowledge({ notification: notification.name });
              setAcked(true);
            }}
          >
            Acknowledge
          </button>
        ))}
    </li>
  );
}
