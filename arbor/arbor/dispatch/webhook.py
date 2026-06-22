"""Webhook dispatcher (ARCHITECTURE §7, DATA-MODEL §10-§11).

Rides the SAME Tree Event stream as the notification dispatcher (DRY): for each
matching Webhook Endpoint it builds ``payload = serialized Tree Event``, signs it
with the core HMAC (``arbor.core.security``), POSTs it with the
``X-Arbor-Signature`` / ``X-Arbor-Event-Id`` headers, and records a Webhook
Delivery row with ``status`` / ``attempts`` / ``last_response`` / ``next_retry_at``
/ ``signature``. A retry runner re-drives pending deliveries on the
``arbor.core.backoff`` schedule (0s, 30s, 5m, 30m, 2h, 12h).

The dispatcher is a PURE CONSUMER — it emits no Tree Event of its own
(WEBHOOKS-036). All side effects go through the injected Transport / WebhookStore
/ Clock seams, so delivery + retry are deterministic in tests (TEST-PLAN §1.2).

The signature and body are computed ONCE at first dispatch and stored on the
delivery; retries resend the byte-identical body + signature + Event-Id (no
re-sign drift; WEBHOOKS-028/034).
"""

from __future__ import annotations

import random
from datetime import timedelta
from typing import Any, Optional

from arbor.core.backoff import MAX_ATTEMPTS, delay_for_attempt, is_exhausted
from arbor.core.security import compute_signature

from .matcher import selector_matches
from .ports import (
    Clock,
    EventView,
    Transport,
    TransportTimeout,
    WebhookStore,
)
from .serializer import serialize_event_bytes

SIGNATURE_HEADER = "X-Arbor-Signature"
EVENT_ID_HEADER = "X-Arbor-Event-Id"

#: Default per-request timeout (seconds) for an outbound delivery POST.
DEFAULT_TIMEOUT = 10.0

#: Jitter band as a fraction of the nominal slot delay (WEBHOOKS-025). The
#: computed delay stays within ``[d, d + JITTER_FRACTION*d]`` and is never
#: negative or shorter than the slot base.
JITTER_FRACTION = 0.1

# Delivery status constants (DATA-MODEL §11).
PENDING = "pending"
DELIVERED = "delivered"
FAILED = "failed"
EXHAUSTED = "exhausted"


def is_success(status_code: int) -> bool:
    """2xx is delivered; everything else (incl. 3xx, 4xx, 5xx) reschedules
    (WEBHOOKS-029/030). Redirects are NOT auto-followed."""
    return 200 <= status_code < 300


def compute_next_retry_offset(current_attempt: int, jitter: bool = True) -> Optional[int]:
    """Seconds until the next retry after ``current_attempt`` completes, with
    bounded jitter (WEBHOOKS-025), or ``None`` if exhausted (WEBHOOKS-026).

    The base is the next slot's nominal delay from the core schedule; jitter only
    ever ADDS within ``JITTER_FRACTION`` so the result is monotic per schedule and
    never shorter than the slot base."""
    next_attempt = current_attempt + 1
    if next_attempt > MAX_ATTEMPTS:
        return None
    base = delay_for_attempt(next_attempt)
    if not jitter or base == 0:
        return base
    return base + int(random.random() * JITTER_FRACTION * base)


class WebhookDispatcher:
    """Consumes Tree Events into Webhook Delivery rows and drives retries."""

    def __init__(
        self,
        store: WebhookStore,
        transport: Transport,
        clock: Clock,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        jitter: bool = True,
    ) -> None:
        self._store = store
        self._transport = transport
        self._clock = clock
        self._timeout = timeout
        self._jitter = jitter

    # -- fan-out + first attempt -------------------------------------------
    def on_tree_event(self, event: EventView) -> list[str]:
        """Create + attempt one delivery per matching ACTIVE endpoint
        (WEBHOOKS-001/008). Idempotent per ``(endpoint, tree_event)``
        (WEBHOOKS-032). Returns created delivery ids."""
        created: list[str] = []
        body = serialize_event_bytes(event)

        for endpoint in self._store.active_endpoints(event.sheet):
            if not selector_matches(endpoint, event, self._store.get_node_range):
                continue
            if self._store.delivery_exists(endpoint.name, event.name):
                continue  # one delivery per (endpoint, tree_event)

            signature = compute_signature(endpoint.secret, body)
            delivery_id = self._store.create_delivery(
                {
                    "endpoint": endpoint.name,
                    "tree_event": event.name,
                    "status": PENDING,
                    "attempts": 0,
                    "last_response": None,
                    "next_retry_at": None,
                    "signature": signature,
                    # body cached so retries resend byte-identical content
                    # without re-serializing/re-signing (WEBHOOKS-028).
                    "body": body,
                    "url": endpoint.url,
                }
            )
            created.append(delivery_id)
            self._attempt(delivery_id)

        return created

    # -- retry runner -------------------------------------------------------
    def run_retries(self) -> list[str]:
        """Re-drive every pending delivery whose ``next_retry_at <= now``
        (WEBHOOKS-024). Each is claimed once so concurrent runners don't
        double-POST (WEBHOOKS-033). Returns the ids attempted this pass."""
        attempted: list[str] = []
        for d in self._store.due_deliveries(self._clock.now()):
            delivery_id = d["name"]
            if not self._store.claim_delivery(delivery_id):
                continue  # another worker claimed it
            self._attempt(delivery_id)
            attempted.append(delivery_id)
        return attempted

    # -- one attempt --------------------------------------------------------
    def _attempt(self, delivery_id: str) -> None:
        """Perform a single delivery attempt and record the outcome.

        Guards: a deleted endpoint cancels in-flight retries (WEBHOOKS-005)."""
        delivery = self._store.get_delivery(delivery_id)
        endpoint = self._store.get_endpoint(delivery["endpoint"])
        if endpoint is None:
            # Endpoint deleted: do not POST; mark the orphan failed so the runner
            # never picks it up again (WEBHOOKS-005).
            self._store.update_delivery(
                delivery_id,
                {
                    "status": FAILED,
                    "next_retry_at": None,
                    "last_response": "endpoint deleted; delivery cancelled",
                },
            )
            return

        attempt_no = int(delivery.get("attempts", 0)) + 1
        body: bytes = delivery["body"]
        headers = {
            "Content-Type": "application/json",
            SIGNATURE_HEADER: delivery["signature"],
            EVENT_ID_HEADER: delivery["tree_event"],
        }

        try:
            resp = self._transport.post(
                delivery.get("url") or endpoint.url, body, headers, self._timeout
            )
            ok = is_success(resp.status_code)
            last_response = f"{resp.status_code} {(resp.text or '')[:512]}"
        except TransportTimeout as exc:  # timeout/connection = retryable failure
            ok = False
            last_response = f"timeout: {exc}"

        if ok:
            self._store.update_delivery(
                delivery_id,
                {
                    "status": DELIVERED,
                    "attempts": attempt_no,
                    "last_response": last_response,
                    "next_retry_at": None,
                },
            )
            return

        # Failure: reschedule or exhaust.
        if is_exhausted(attempt_no):
            self._store.update_delivery(
                delivery_id,
                {
                    "status": EXHAUSTED,
                    "attempts": attempt_no,
                    "last_response": last_response,
                    "next_retry_at": None,
                },
            )
            return

        offset = compute_next_retry_offset(attempt_no, jitter=self._jitter)
        next_retry_at = self._clock.now() + timedelta(seconds=offset)
        self._store.update_delivery(
            delivery_id,
            {
                "status": PENDING,
                "attempts": attempt_no,
                "last_response": last_response,
                "next_retry_at": next_retry_at,
            },
        )
