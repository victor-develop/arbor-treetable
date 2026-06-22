"""Notification dispatcher (ARCHITECTURE §6, DATA-MODEL §7-§9).

Reacts to NEW Tree Event rows (the SAME stream that feeds the webhook
dispatcher — DRY). For each matching Subscription it creates ONE Notification per
``(tree_event, recipient, channel)`` with ``channel`` + ``delivered_at``, copies
``requires_ack`` from the subscription, and exposes the "N notified / M acked"
accountability aggregate.

Pure logic over the :class:`~arbor.dispatch.ports.NotificationStore` and
:class:`~arbor.dispatch.ports.Clock` seams — no frappe import, so it runs
deterministically in tests with the in-memory store + frozen clock.

The shared scope matcher (:mod:`arbor.dispatch.matcher`) is the SAME one the
webhook dispatcher uses (WEBHOOKS-037).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .matcher import selector_matches
from .ports import Clock, EventView, NotificationStore


@dataclass(frozen=True)
class Accountability:
    """"N notified / M acked" for an event or a Change Request (ARCHITECTURE §6,
    PERMISSIONS §3-G). Only ``requires_ack`` notifications count toward the
    denominator (NOTIFICATIONS_AND_ACK-030)."""

    notified: int
    acked: int

    def as_dict(self) -> dict[str, int]:
        return {"notified": self.notified, "acked": self.acked}


class NotificationDispatcher:
    """Consumes one Tree Event and fans out Notifications. Stateless except for
    its injected store + clock."""

    def __init__(self, store: NotificationStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    # -- fan-out ------------------------------------------------------------
    def on_tree_event(self, event: EventView) -> list[str]:
        """Create Notifications for every LIVE subscription matching ``event``.

        Returns the ids of created Notification rows. Idempotent per
        ``(tree_event, recipient, channel)`` (NOTIFICATIONS_AND_ACK-014); distinct
        channels for the same recipient produce distinct rows
        (NOTIFICATIONS_AND_ACK-014b). Reads LIVE subscriptions only, so removed or
        late subscriptions are never back-filled (NOTIFICATIONS_AND_ACK-037/038).
        """
        created: list[str] = []
        seen: set[tuple[str, str]] = set()  # (recipient, channel) dedup within this event

        event_created = getattr(event, "created_at", None)
        for sub in self._store.live_subscriptions(event.sheet):
            if not selector_matches(sub, event, self._store.get_node_range):
                continue

            # No back-fill: a subscription created AFTER the event never receives it
            # (NOTIFICATIONS_AND_ACK-037). Real-time dispatch gets this for free; the
            # guard makes it hold under batch/replay dispatch too. Skipped when either
            # timestamp is absent (pure in-memory doubles).
            sub_created = getattr(sub, "created_at", None)
            if event_created is not None and sub_created is not None and sub_created > event_created:
                continue

            recipient = sub.subscriber
            channel = sub.delivery
            key = (recipient, channel)
            if key in seen:
                continue
            seen.add(key)

            # Cross-invocation idempotency (e.g. dispatcher re-run / at-least-once
            # worker): one Notification per (tree_event, recipient, channel).
            if self._store.notification_exists(event.name, recipient, channel):
                continue

            notif_id = self._store.create_notification(
                {
                    "tree_event": event.name,
                    "change_request": event.change_request,
                    "recipient": recipient,
                    "channel": channel,
                    "requires_ack": bool(sub.requires_ack),
                    # deliver(): channel + delivered_at set at creation time
                    # (NOTIFICATIONS_AND_ACK-015).
                    "delivered_at": self._clock.now(),
                }
            )
            created.append(notif_id)

        return created

    # -- accountability -----------------------------------------------------
    def accountability(
        self,
        *,
        tree_event: Optional[str] = None,
        change_request: Optional[str] = None,
    ) -> Accountability:
        """"N notified / M acked", scoped to a single Tree Event OR a Change
        Request (ARCHITECTURE §6 "for a given Tree Event or Change Request";
        NOTIFICATIONS_AND_ACK-029..033). Counts only ``requires_ack``
        notifications; clean zero-state when none (no division). """
        if tree_event is None and change_request is None:
            raise ValueError("accountability needs tree_event or change_request")
        notified = self._store.count_ack_required(
            tree_event=tree_event, change_request=change_request
        )
        acked = self._store.count_acknowledged(
            tree_event=tree_event, change_request=change_request
        )
        return Accountability(notified=notified, acked=acked)
