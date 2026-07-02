"""Frappe ADAPTER for the dispatch lane.

This is the ONLY file in the dispatch lane that imports frappe. It wires the pure
:mod:`arbor.dispatch.notify` + :mod:`arbor.dispatch.webhook` dispatchers to the
Frappe DocTypes by implementing the store/clock/transport ports over the ORM,
``requests``, and the wall clock.

Integrator wiring (declared in the manifest, NOT edited here):

* ``doc_events`` — ``Tree Event``'s ``after_insert`` calls
  :func:`on_tree_event_insert`. ONE doc_event feeds BOTH dispatchers (DRY): the
  same new Tree Event row is handed to the notification dispatcher and the
  webhook dispatcher in turn.
* ``scheduler_events`` — a periodic (e.g. ``cron`` / ``all``) call to
  :func:`run_webhook_retries` drives the backoff retry runner.

Everything below degrades to a no-op import guard so the module is importable in
a bench-free unit run (the pure dispatchers + in-memory doubles are what the
lane's tests exercise; the frappe paths are covered at the integration layer on a
bench).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from arbor.core import process as process_machine
from arbor.core.backoff import is_exhausted

from .matcher import selector_matches  # re-exported for integration tests
from .notify import Accountability, NotificationDispatcher
from .ports import TransportTimeout
from .serializer import serialize_event_dict
from .webhook import WebhookDispatcher

try:  # pragma: no cover - exercised only on a bench
    import frappe
    from frappe.utils import now_datetime
except Exception:  # pragma: no cover - bench-free import guard
    frappe = None  # type: ignore

    def now_datetime() -> datetime:  # type: ignore
        return datetime.utcnow()


# ---------------------------------------------------------------------------
# Adapter views over frappe Documents (duck-typed to the *View protocols).
# ---------------------------------------------------------------------------
class _EventDoc:
    """Wrap a ``Tree Event`` Document as an EventView. ``payload`` is JSON in the
    DB; ``name`` is the event id (== X-Arbor-Event-Id)."""

    def __init__(self, doc: Any) -> None:
        self._doc = doc

    @property
    def name(self) -> str:
        return self._doc.name

    @property
    def sheet(self) -> str:
        return self._doc.sheet

    @property
    def type(self) -> str:
        return self._doc.type

    @property
    def payload(self) -> dict[str, Any]:
        raw = self._doc.payload
        if isinstance(raw, str):
            return json.loads(raw or "{}")
        return raw or {}

    @property
    def actor(self) -> Optional[str]:
        return self._doc.actor

    @property
    def actor_type(self) -> str:
        return self._doc.actor_type

    @property
    def change_request(self) -> Optional[str]:
        return self._doc.change_request

    @property
    def timestamp(self) -> Optional[str]:
        # Tree Event is append-only; creation time is the event timestamp.
        return str(self._doc.creation) if getattr(self._doc, "creation", None) else None

    @property
    def created_at(self):
        # Used by the dispatcher's no-back-fill guard to compare against a
        # subscription's creation; normalized to a datetime so the comparison is
        # type-consistent (a fresh doc's creation may be a str, get_all gives one).
        c = getattr(self._doc, "creation", None)
        return frappe.utils.get_datetime(c) if c else None


class _SubscriptionDoc:
    def __init__(self, row: dict[str, Any]) -> None:
        self.name = row["name"]
        self.subscriber = row["subscriber"]
        self.subscriber_kind = row.get("subscriber_kind", "user")
        self.scope = row["scope"]
        self.target = row["target"]
        et = row.get("event_types")
        self.event_types = json.loads(et) if isinstance(et, str) else (et or [])
        self.delivery = row["delivery"]
        self.requires_ack = bool(row.get("requires_ack"))
        _c = row.get("creation")
        self.created_at = frappe.utils.get_datetime(_c) if _c else None


class _EndpointDoc:
    def __init__(self, doc: Any) -> None:
        self.name = doc.name
        self.url = doc.url
        # Password field: fetched decrypted via get_password.
        self.secret = (
            doc.get_password("secret") if hasattr(doc, "get_password") else doc.secret
        )
        et = doc.event_types
        self.event_types = json.loads(et) if isinstance(et, str) else (et or [])
        self.scope = doc.scope
        self.target = doc.target
        self.active = bool(doc.active)


# ---------------------------------------------------------------------------
# Frappe-backed stores
# ---------------------------------------------------------------------------
class FrappeClock:
    def now(self) -> datetime:
        return now_datetime()


class FrappeNotificationStore:
    """``NotificationStore`` over the Subscription / Notification / Acknowledgement
    DocTypes (DATA-MODEL §7-§9)."""

    def live_subscriptions(self, sheet: str):
        # A subscription is "live" if it exists; unsubscribe deletes/deactivates.
        rows = frappe.get_all(
            "Subscription",
            fields=[
                "name",
                "subscriber",
                "subscriber_kind",
                "scope",
                "target",
                "event_types",
                "delivery",
                "requires_ack",
                "creation",
            ],
            # sheet-scope rows carry target=sheet; branch/column rows belong to
            # the sheet via their target node/column. The matcher does the final
            # scope decision; we pre-filter cheaply where possible.
        )
        return [_SubscriptionDoc(r) for r in rows]

    def get_node_range(self, node: str) -> Optional[tuple[int, int]]:
        vals = frappe.db.get_value("Tree Node", node, ["lft", "rgt"])
        if not vals:
            return None
        return (int(vals[0]), int(vals[1]))

    def notification_exists(self, tree_event: str, recipient: str, channel: str) -> bool:
        return bool(
            frappe.db.exists(
                "Notification",
                {"tree_event": tree_event, "recipient": recipient, "channel": channel},
            )
        )

    def create_notification(self, data: dict[str, Any]) -> str:
        doc = frappe.get_doc({"doctype": "Notification", **data})
        doc.insert(ignore_permissions=True)
        return doc.name

    def count_ack_required(self, *, tree_event=None, change_request=None) -> int:
        filters: dict[str, Any] = {"requires_ack": 1}
        if tree_event is not None:
            filters["tree_event"] = tree_event
        else:
            filters["change_request"] = change_request
        return frappe.db.count("Notification", filters)

    def count_acknowledged(self, *, tree_event=None, change_request=None) -> int:
        filters: dict[str, Any] = {"requires_ack": 1}
        if tree_event is not None:
            filters["tree_event"] = tree_event
        else:
            filters["change_request"] = change_request
        notif_names = frappe.get_all("Notification", filters=filters, pluck="name")
        if not notif_names:
            return 0
        return frappe.db.count("Acknowledgement", {"notification": ["in", notif_names]})


class FrappeWebhookStore:
    """``WebhookStore`` over the Webhook Endpoint / Webhook Delivery DocTypes
    (DATA-MODEL §10-§11)."""

    def active_endpoints(self, sheet: str):
        names = frappe.get_all("Webhook Endpoint", filters={"active": 1}, pluck="name")
        return [_EndpointDoc(frappe.get_doc("Webhook Endpoint", n)) for n in names]

    def get_endpoint(self, endpoint: str):
        if not frappe.db.exists("Webhook Endpoint", endpoint):
            return None
        return _EndpointDoc(frappe.get_doc("Webhook Endpoint", endpoint))

    def get_node_range(self, node: str) -> Optional[tuple[int, int]]:
        vals = frappe.db.get_value("Tree Node", node, ["lft", "rgt"])
        if not vals:
            return None
        return (int(vals[0]), int(vals[1]))

    def delivery_exists(self, endpoint: str, tree_event: str) -> bool:
        return bool(
            frappe.db.exists(
                "Webhook Delivery", {"endpoint": endpoint, "tree_event": tree_event}
            )
        )

    def create_delivery(self, data: dict[str, Any]) -> str:
        # ``body`` is a bytes cache for retries; store as a hidden Code/Text col.
        body = data.get("body")
        stored = dict(data)
        if isinstance(body, bytes):
            stored["body"] = body.decode("utf-8")
        doc = frappe.get_doc({"doctype": "Webhook Delivery", **stored})
        doc.insert(ignore_permissions=True)
        return doc.name

    def update_delivery(self, delivery: str, patch: dict[str, Any]) -> None:
        frappe.db.set_value("Webhook Delivery", delivery, patch)

    def get_delivery(self, delivery: str) -> dict[str, Any]:
        d = frappe.get_doc("Webhook Delivery", delivery).as_dict()
        if isinstance(d.get("body"), str):
            d["body"] = d["body"].encode("utf-8")
        return d

    def due_deliveries(self, now) -> list[dict[str, Any]]:
        rows = frappe.get_all(
            "Webhook Delivery",
            filters={"status": "pending", "next_retry_at": ["<=", now]},
            fields=["name"],
        )
        return [{"name": r["name"]} for r in rows]

    def claim_delivery(self, delivery: str) -> bool:
        # Row-level lock: SELECT ... FOR UPDATE pins the row inside the worker's
        # transaction; re-check it is still pending so the first committer wins
        # and concurrent runners don't double-POST (WEBHOOKS-033).
        current = frappe.db.get_value(
            "Webhook Delivery", delivery, "status", for_update=True
        )
        return current == "pending"


class RequestsTransport:
    """``Transport`` over the ``requests`` library. Does NOT follow redirects
    (WEBHOOKS-030); maps timeouts/connection errors to ``TransportTimeout``
    (WEBHOOKS-023)."""

    def post(self, url, body, headers, timeout):
        import requests  # local import; not needed in bench-free unit runs

        try:
            return requests.post(
                url, data=body, headers=headers, timeout=timeout, allow_redirects=False
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise TransportTimeout(str(exc)) from exc


# ---------------------------------------------------------------------------
# Process lane (Area 3) — a THIRD pure consumer of the SAME Tree Event stream.
#
# ``NODE_CREATED`` in a process's scope STARTS a run at stage 0 + notifies the
# stage-0 column owner; a ``NODE_VALUE_UPDATED`` on the CURRENT stage column
# ADVANCES the run + notifies the next owner; the terminal fill completes the run
# (no notify). Advancement emits NO Tree Event, so feeding this off the SAME
# after_insert hook cannot recurse. Idempotency is the pure machine's job
# (current_stage_idx + filled_at + notified_owner guards).
# ---------------------------------------------------------------------------
class FrappeProcessClock:
    """Wall clock for the process lane, as an ISO-8601 string (lexically ordered,
    so the pure ``_past_due`` string comparison + the Datetime column agree)."""

    def now(self) -> str:
        return str(now_datetime())


class FrappeProcessNotifier:
    """Persist ONE ``source in {'process','sla'}`` in-app Notification per
    recipient (reusing the SAME Notification DocType as the tree-event + comment
    inboxes). FYI only: ``requires_ack=0`` so process rows never pollute the
    accountability aggregate. Idempotency is upstream (the pure ``notified_owner``
    guard fires the notify at most once per stage-enter), so no de-dupe here beyond
    the store's own uniqueness."""

    def __call__(self, recipients: list[str], data: dict[str, Any]) -> None:
        source = data.get("source", "process")
        for r in recipients:
            frappe.get_doc(
                {
                    "doctype": "Notification",
                    "source": source,
                    "tree_event": None,
                    "recipient": r,
                    "channel": "in-app",
                    "requires_ack": 0,
                    "delivered_at": now_datetime(),
                }
            ).insert(ignore_permissions=True)


def _process_repo():
    """A Frappe ``Repository`` for the process lane (the pure machine operates over
    the SAME data seam the executor/handlers use)."""
    try:  # ``arbor.adapter`` on a bench; ``arbor.arbor.adapter`` in the dev repo.
        from arbor.adapter.repository import FrappeRepository
    except ModuleNotFoundError:  # pragma: no cover - dev-layout fallback
        from arbor.arbor.adapter.repository import FrappeRepository  # type: ignore
    return FrappeRepository()


class ProcessDispatcher:
    """Thin Frappe binding around the pure ``arbor.core.process`` machine.

    Injectable (repo/notify/clock) so tests drive it against the in-memory doubles
    with a freezable clock; the default wires the Frappe repo + Notification
    persister + wall clock. Holds NO logic of its own — start/advance/complete +
    SLA breach all live in the pure module."""

    def __init__(self, repo=None, notify=None, clock=None) -> None:
        self.repo = repo or _process_repo()
        self.notify = notify or FrappeProcessNotifier()
        self.clock = clock or FrappeProcessClock()

    def on_tree_event(self, event: Any) -> list[dict[str, Any]]:
        """Drive the process consumer for ONE Tree Event. Resolves the sheet's
        process (inert if none / disabled) and reacts to NODE_CREATED /
        NODE_VALUE_UPDATED only; all other types are no-ops."""
        etype = event.type
        if etype not in ("NODE_CREATED", "NODE_VALUE_UPDATED"):
            return []
        process = self.repo.get_process(event.sheet)
        if process is None or not process.enabled:
            return []
        payload = event.payload or {}
        ev = {
            "type": etype,
            "node": payload.get("node"),
            "column": payload.get("column"),
            "tree_event": event.name,
        }
        return process_machine.on_event(
            self.repo, process, ev, now=self.clock.now(), notify=self.notify
        )

    def sla_sweep(self) -> list[dict[str, Any]]:
        """Mark the current stage of every over-due active run breached + notify
        the stage owner once (when the owning process has ``sla_breach_notify``)."""
        return process_machine.sla_sweep(
            self.repo,
            self.clock.now(),
            process_of=self._process_of,
            notify=self.notify,
        )

    def _process_of(self, process_name: str):
        """Resolve a run's ``process`` link back to its ``ProcessView`` definition
        (for the sweep's ``sla_breach_notify`` gate). Uses the adapter's
        ``get_process_by_name`` when present, else falls back to scanning the
        repo's process store (the in-memory double keyed by name)."""
        by_name = getattr(self.repo, "get_process_by_name", None)
        if by_name is not None:
            return by_name(process_name)
        store = getattr(self.repo, "processes", None)  # in-memory double
        if store is not None:
            return store.get(process_name)
        return None


# ---------------------------------------------------------------------------
# Factories + doc_event / scheduler entrypoints (the integrator wires these).
# ---------------------------------------------------------------------------
def _notification_dispatcher() -> NotificationDispatcher:
    return NotificationDispatcher(FrappeNotificationStore(), FrappeClock())


def _webhook_dispatcher() -> WebhookDispatcher:
    return WebhookDispatcher(
        FrappeWebhookStore(), RequestsTransport(), FrappeClock()
    )


def _process_dispatcher() -> ProcessDispatcher:
    return ProcessDispatcher()


def on_tree_event_insert(doc: Any, method: Optional[str] = None) -> None:
    """``doc_events["Tree Event"]["after_insert"]`` entrypoint.

    ONE hook feeds THREE pure consumers off the SAME new Tree Event row (DRY,
    ARCHITECTURE §6/§7): notifications, webhooks, then the process consumer. None
    emit a Tree Event, so there is no recursion."""
    event = _EventDoc(doc)
    _notification_dispatcher().on_tree_event(event)
    _webhook_dispatcher().on_tree_event(event)
    _process_dispatcher().on_tree_event(event)


def run_webhook_retries() -> None:
    """``scheduler_events`` entrypoint — drive the backoff retry runner."""
    _webhook_dispatcher().run_retries()


def run_process_sla_sweep() -> None:
    """``scheduler_events`` entrypoint — mark SLA breaches on over-due active runs
    (+ notify the stage owner once when the process opts in)."""
    _process_dispatcher().sla_sweep()


def accountability(
    *, tree_event: Optional[str] = None, change_request: Optional[str] = None
) -> dict[str, int]:
    """Public accountability aggregate helper ("N notified / M acked") for the UI
    / API to call against a live site."""
    return _notification_dispatcher().accountability(
        tree_event=tree_event, change_request=change_request
    ).as_dict()


__all__ = [
    "on_tree_event_insert",
    "run_webhook_retries",
    "run_process_sla_sweep",
    "accountability",
    "FrappeNotificationStore",
    "FrappeWebhookStore",
    "FrappeClock",
    "RequestsTransport",
    "ProcessDispatcher",
    "FrappeProcessClock",
    "FrappeProcessNotifier",
    "Accountability",
    "is_exhausted",
    "serialize_event_dict",
    "selector_matches",
]
