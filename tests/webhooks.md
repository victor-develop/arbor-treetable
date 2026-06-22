# Arbor — Test-Case Catalog: Webhook Events

> Test-first catalog for the **Webhook Events** surface. Authored against the canonical
> specs ([`ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §7, §11,
> [`DATA-MODEL.md`](../docs/DATA-MODEL.md) §10–§12,
> [`CAPABILITIES.md`](../docs/CAPABILITIES.md), [`PERMISSIONS.md`](../docs/PERMISSIONS.md)).
>
> **Surface scope:** External systems subscribe to Tree Event types via **Webhook
> Endpoint** rows; the **webhook dispatcher** (`arbor.webhooks.dispatcher`) fans out from
> the append-only Tree Event stream into **Webhook Delivery** rows. Covers: delivery
> payload schema, HMAC signature, retry/backoff, delivery log, subscription
> create/update/delete, and the DRY invariant that webhooks ride the *same* Tree Event
> stream as notifications with no divergent logic.
>
> **Out of scope (covered by sibling catalogs):** notification/ack ledger internals (see
> `notifications.md`), ACL resolution mechanics (see `permissions.md`), capability
> handler/mutation logic (see per-capability catalogs). This catalog asserts only that
> webhooks *consume* the events those surfaces emit.

---

## Shared fixtures (canonical — do NOT redefine per test)

All cases below reuse the canonical world from `PERMISSIONS.md` §2. Assume a shared fixture
factory provides:

- **Personas:** users **A** (root `structural_owner`), **B** / **C** (column owners),
  **D** (delegated owner of sub-branch P2 via active Branch Grant), **E** / **F**
  (suggest-only), **G** (sensitive subscriber, `requires_ack`), **EXT** (external system:
  API consumer + webhook endpoint owner).
- **Sample sheet `S`** with tree:
  ```
  R
  ├── P1
  │   └── X
  └── P2   (Branch Grant: grantee = D, active)
      ├── Y
      └── Z
  ```
  Columns: `col:name` (is_label, owner B), `col:status` (owner C, editors [B]),
  `col:budget` (owner C), `col:notes` (owner B).
- **`EXT_ENDPOINT`** — a canonical `Webhook Endpoint` row: `url = https://ext.example/hook`,
  `secret = <known test secret>`, `event_types = ["NODE_VALUE_UPDATED","CHANGE_APPROVED"]`,
  `scope = sheet`, `target = S`, `active = 1`.
- **Receiver harness** — a controllable HTTP test double the dispatcher POSTs to:
  programmable to return 2xx / 4xx / 5xx / timeout / malformed, and capturing raw body +
  all headers per request for assertion. (Receiver, NOT the dispatcher under test, verifies
  HMAC.)
- **Clock control** — a freezable/advanceable clock so `next_retry_at` and the backoff
  schedule are deterministic. The retry runner is invokable on demand (no real sleeps).
- **Event factory** — helper to drive `execute_action` (or `approveChange`) so real Tree
  Events land on the stream, exercising the dispatcher end-to-end rather than fabricating
  Tree Event rows by hand (which would bypass the append-only emitter).

Event types referenced are the closed set in `DATA-MODEL.md` §12. The HMAC scheme is
`X-Arbor-Signature: sha256=HMAC-SHA256(endpoint.secret, raw_body)` with
`X-Arbor-Event-Id = tree_event` (`ARCHITECTURE.md` §7).

---

## A. Subscription lifecycle (create / update / delete)

### WEBHOOKS-001
- **Title:** Create webhook endpoint subscribed to selected event types (happy path)
- **Level:** integration
- **Preconditions / fixtures:** Sheet `S`; persona EXT; no endpoint yet.
- **Given** EXT registers a Webhook Endpoint with
  `url=https://ext.example/hook`, `event_types=["NODE_VALUE_UPDATED","CHANGE_APPROVED"]`,
  `scope=sheet`, `target=S`, a `secret`, `active=1`.
- **When** the endpoint is persisted.
- **Then** a `Webhook Endpoint` row exists with exactly those fields; `secret` is stored as
  a Password field (not returned in plaintext on read-back); and the endpoint participates
  in dispatcher fan-out for subsequent matching events. No `Webhook Delivery` rows exist yet
  (no event has fired).
- **Covers:** Webhook Endpoint DocType (DATA-MODEL §10); dispatcher subscription wiring
  (ARCHITECTURE §7).

### WEBHOOKS-002
- **Title:** Update endpoint event_types narrows the subscription
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT` (subscribed to NODE_VALUE_UPDATED +
  CHANGE_APPROVED).
- **Given** EXT updates the endpoint's `event_types` to `["CHANGE_APPROVED"]` only.
- **When** C executes `updateCell(X, col:budget)` (emits `NODE_VALUE_UPDATED`).
- **Then** NO `Webhook Delivery` is created for `EXT_ENDPOINT` (event type no longer
  subscribed). A later `CHANGE_APPROVED` event DOES produce a delivery.
- **Covers:** Webhook Endpoint.event_types filtering; Tree Events `NODE_VALUE_UPDATED`,
  `CHANGE_APPROVED`.

### WEBHOOKS-003
- **Title:** Deactivating an endpoint stops delivery without deleting the row/log
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT`, with at least one prior delivered
  `Webhook Delivery` row in the log.
- **Given** EXT sets `active=0` on the endpoint.
- **When** C executes `updateCell(X, col:budget)` (matching `NODE_VALUE_UPDATED`).
- **Then** no new `Webhook Delivery` is created for that endpoint; the endpoint row and its
  historical delivery log rows remain intact and queryable.
- **Covers:** Webhook Endpoint.active gating; delivery-log retention.

### WEBHOOKS-004
- **Title:** Re-activating an endpoint resumes delivery (no backfill of missed events)
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT` currently `active=0`; one matching event was
  emitted while inactive.
- **Given** EXT sets `active=1` again.
- **When** a new matching `NODE_VALUE_UPDATED` event is emitted after re-activation.
- **Then** a delivery is created ONLY for the post-reactivation event; the event emitted
  while inactive is NOT retroactively delivered (dispatch decision is made at emit time).
- **Covers:** Webhook Endpoint.active; no-backfill semantics (ARCHITECTURE §7).

### WEBHOOKS-005
- **Title:** Delete endpoint removes future delivery; in-flight retries cease
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT` with one `Webhook Delivery` in `pending`
  state (awaiting retry, `next_retry_at` in future).
- **Given** the endpoint is deleted.
- **When** the retry runner next executes, and a new matching event is later emitted.
- **Then** the pending delivery is not retried against the deleted endpoint (it is
  cancelled/orphaned per spec, not POSTed); no new deliveries are created for future events.
- **Covers:** Webhook Endpoint deletion; Webhook Delivery retry runner guard.

### WEBHOOKS-006
- **Title:** Branch-scoped endpoint only receives events inside its subtree
- **Level:** integration
- **Preconditions / fixtures:** A second endpoint `EXT_BRANCH` with `scope=branch`,
  `target=P2`, `event_types=["NODE_DELETED"]`, active.
- **Given** the branch-scoped endpoint targets P2.
- **When** (a) D executes `deleteNode(Z)` (Z is a descendant of P2), then
  (b) A executes `deleteNode(X)` (X is under P1, outside P2).
- **Then** a `Webhook Delivery` is created for `EXT_BRANCH` for event (a) only; event (b)
  does NOT match (NestedSet descendant range excludes X). Branch matching uses the same
  `lft/rgt` descendant range as notification dispatch.
- **Covers:** Webhook Endpoint.scope=branch matching (ARCHITECTURE §6/§7 shared matcher);
  Tree Event `NODE_DELETED`.

### WEBHOOKS-007
- **Title:** Column-scoped endpoint matches only its column's value events
- **Level:** integration
- **Preconditions / fixtures:** Endpoint `EXT_COL` with `scope=column`,
  `target=col:budget`, `event_types=["NODE_VALUE_UPDATED"]`, active.
- **Given** the column-scoped endpoint targets `col:budget`.
- **When** C updates `col:budget` on Y (match) and B updates `col:name` on Z (non-match).
- **Then** exactly one delivery is created — for the `col:budget` update; the `col:name`
  update does not match (direct column equality, per ARCHITECTURE §6 matcher reuse).
- **Covers:** Webhook Endpoint.scope=column matching; Tree Event `NODE_VALUE_UPDATED`.

### WEBHOOKS-008
- **Title:** Multiple endpoints fan out independently from one event
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT` (sheet scope) and `EXT_COL` (column scope on
  col:budget), both subscribed to `NODE_VALUE_UPDATED`, both active.
- **Given** two endpoints whose scopes both match a `col:budget` update on Y.
- **When** C executes `updateCell(Y, col:budget)`.
- **Then** TWO distinct `Webhook Delivery` rows are created — one per endpoint — each
  referencing the same `tree_event`, each independently signed with its own endpoint
  `secret`, with independent `status`/`attempts`/`next_retry_at`.
- **Covers:** dispatcher fan-out one-event→many-deliveries; per-endpoint HMAC isolation.

### WEBHOOKS-009
- **Title:** Subscription create/update/delete emit no spurious webhook to self
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT` subscribed (does not include
  `SUBSCRIPTION_CHANGED`).
- **Given** the endpoint's event_types exclude `SUBSCRIPTION_CHANGED`.
- **When** EXT (or A) subscribes/unsubscribes another watcher (emitting
  `SUBSCRIPTION_CHANGED`).
- **Then** no `Webhook Delivery` for `EXT_ENDPOINT` results from the `SUBSCRIPTION_CHANGED`
  event (type not subscribed). Confirms `SUBSCRIPTION_CHANGED` is an ordinary stream event
  governed by the same event_types filter.
- **Covers:** Tree Event `SUBSCRIPTION_CHANGED`; event_types filter; `subscribe`/
  `unsubscribe` capabilities (emit `SUBSCRIPTION_CHANGED`).

---

## B. Delivery payload schema

### WEBHOOKS-010
- **Title:** Delivered payload is the serialized Tree Event with the full field set
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT`; receiver harness capturing body.
- **Given** an active sheet-scoped endpoint subscribed to `NODE_VALUE_UPDATED`.
- **When** C executes `updateCell(X, col:budget, value=42)` → emits `NODE_VALUE_UPDATED`.
- **Then** the POST body is JSON containing exactly: `type` (`"NODE_VALUE_UPDATED"`),
  `sheet` (`S`), `payload`, `actor`, `actor_type`, `change_request` (null here),
  `timestamp`, and `event_id` (ARCHITECTURE §7). The body matches the canonical serialized
  Tree Event shape (same serializer used for stream/audit).
- **Covers:** delivery payload schema; Tree Event `NODE_VALUE_UPDATED`.

### WEBHOOKS-011
- **Title:** NODE_VALUE_UPDATED payload carries {node, column, old, new, version}
- **Level:** unit
- **Preconditions / fixtures:** event serializer; a `NODE_VALUE_UPDATED` event for
  `(X, col:budget)` going from old=10 → new=42, version incremented.
- **Given** the emitted event payload per DATA-MODEL §4.
- **When** the webhook dispatcher serializes the body.
- **Then** `payload` contains `node=X`, `column=col:budget`, `old_value=10`,
  `new_value=42`, and `version` (the incremented counter). No other surface re-derives this
  payload — it is the event's own payload verbatim.
- **Covers:** Tree Event `NODE_VALUE_UPDATED` payload contract.

### WEBHOOKS-012
- **Title:** CHANGE_PROPOSED payload references the Change Request
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT` updated to subscribe to `CHANGE_PROPOSED`.
- **Given** E executes `updateCell(X, col:budget)` while unauthorized → a Change Request is
  created and `CHANGE_PROPOSED` emitted (ARCHITECTURE §4.2 branch 4b).
- **When** the dispatcher delivers.
- **Then** the body `type="CHANGE_PROPOSED"`, top-level `change_request` is set to the CR
  name, and `payload` includes `{change_request, action}`. `actor=E`, `actor_type="human"`.
- **Covers:** Tree Event `CHANGE_PROPOSED`; `updateCell`→CR path; capability `suggestChange`
  equivalence.

### WEBHOOKS-013
- **Title:** CHANGE_APPROVED delivery follows the real mutation event, both with CR link
- **Level:** e2e
- **Preconditions / fixtures:** `EXT_ENDPOINT` subscribed to
  `["NODE_VALUE_UPDATED","CHANGE_APPROVED"]`; an open CR (E's `updateCell` to `col:budget`,
  `resolved_approver=C`).
- **Given** the CR is pending.
- **When** C executes `approveChange(cr)` — which replays the handler as C, emitting the
  real `NODE_VALUE_UPDATED`, then `CHANGE_APPROVED` (ARCHITECTURE §5.1).
- **Then** TWO deliveries are produced for `EXT_ENDPOINT`: one `NODE_VALUE_UPDATED`
  (`actor=C`, `change_request` = the CR), one `CHANGE_APPROVED` (`change_request` = the CR).
  Ordering reflects emission order. Both carry the CR link, demonstrating webhooks observe
  the same event sequence the approval lifecycle produces.
- **Covers:** Tree Events `NODE_VALUE_UPDATED` + `CHANGE_APPROVED`; `approveChange`.

### WEBHOOKS-014
- **Title:** actor_type=agent surfaces correctly in webhook payload
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT`; the server-side agent acting as its own User
  with column authority on `col:status` (e.g. agent user added as editor) so its
  `updateCell` executes directly.
- **Given** the agent (its own User) executes `updateCell(X, col:status)` via `agent.chat`.
- **When** `NODE_VALUE_UPDATED` is emitted and delivered.
- **Then** the payload `actor_type="agent"` and `actor` = the agent's User. The webhook
  consumer can distinguish agent-originated mutations purely from the payload, with no
  separate code path (ARCHITECTURE §8, §11 surface parity).
- **Covers:** Tree Event `NODE_VALUE_UPDATED` with `actor_type=agent`; surface parity.

### WEBHOOKS-015
- **Title:** Payload is canonical/byte-stable so HMAC over raw_body verifies
- **Level:** unit
- **Preconditions / fixtures:** event serializer + signer.
- **Given** a fixed Tree Event.
- **When** the dispatcher serializes the body and computes the signature over the exact
  bytes it will transmit.
- **Then** the stored `Webhook Delivery.signature` equals
  `sha256=HMAC-SHA256(secret, raw_body)` for the identical byte sequence sent on the wire
  (no re-serialization between signing and sending; stable key ordering / no trailing
  whitespace drift). Receiver recomputing over the received bytes matches.
- **Covers:** signing/serialization coupling (ARCHITECTURE §7).

---

## C. HMAC signature

### WEBHOOKS-016
- **Title:** Delivery includes valid X-Arbor-Signature header
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT` with known `secret`; receiver harness.
- **Given** an active endpoint.
- **When** a matching event is delivered.
- **Then** the request includes header `X-Arbor-Signature: sha256=<hex>` where
  `<hex> = HMAC-SHA256(secret, raw_body)`; the receiver independently recomputes and the
  values match. The same value is persisted in `Webhook Delivery.signature`.
- **Covers:** HMAC signature header + Webhook Delivery.signature (ARCHITECTURE §7,
  DATA-MODEL §11).

### WEBHOOKS-017
- **Title:** Tampered body fails receiver-side signature verification
- **Level:** unit
- **Preconditions / fixtures:** captured raw body + signature from a real delivery.
- **Given** a delivered payload and its `X-Arbor-Signature`.
- **When** a single byte of the body is altered and the receiver recomputes HMAC over the
  altered body.
- **Then** the recomputed signature does NOT match the header — demonstrating the signature
  binds integrity of the exact bytes. (Asserts the contract a consumer relies on.)
- **Covers:** HMAC signature integrity property.

### WEBHOOKS-018
- **Title:** Per-endpoint secret isolation — wrong secret never verifies
- **Level:** unit
- **Preconditions / fixtures:** two endpoints with different secrets, each delivered the
  same event (see WEBHOOKS-008).
- **Given** delivery D1 (endpoint 1 secret) and D2 (endpoint 2 secret) of the same event.
- **When** D1's signature is verified using endpoint 2's secret.
- **Then** verification fails; each delivery verifies only under its own endpoint's secret.
  Confirms signatures are computed per endpoint, not globally.
- **Covers:** per-endpoint HMAC keying.

### WEBHOOKS-019
- **Title:** Rotating an endpoint secret signs subsequent deliveries with the new key
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT`; one delivery already made with old secret.
- **Given** EXT updates `secret` to a new value.
- **When** a new matching event is delivered.
- **Then** the new delivery's `X-Arbor-Signature` verifies under the NEW secret and not the
  old; the prior delivery's stored signature is unchanged (historical record preserved).
- **Covers:** secret rotation; Webhook Delivery.signature immutability of past rows.

### WEBHOOKS-020
- **Title:** X-Arbor-Event-Id header equals the Tree Event id for idempotent consumption
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT`; receiver harness.
- **Given** an active endpoint.
- **When** a matching event is delivered.
- **Then** the request carries `X-Arbor-Event-Id: <tree_event id>`, equal to the delivery's
  `tree_event` link and the payload `event_id`. A consumer can dedupe on this header.
- **Covers:** idempotency header (ARCHITECTURE §7, DATA-MODEL §11).

---

## D. Retry / backoff

### WEBHOOKS-021
- **Title:** 2xx response marks delivery delivered on first attempt
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT`; receiver returns `200`.
- **Given** a matching event.
- **When** the dispatcher posts and gets `200`.
- **Then** `Webhook Delivery.status="delivered"`, `attempts=1`, `last_response` records the
  2xx, `next_retry_at` is null/cleared; no further attempts are scheduled.
- **Covers:** success path (ARCHITECTURE §7, DATA-MODEL §11).

### WEBHOOKS-022
- **Title:** Non-2xx schedules a retry with backoff
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT`; receiver returns `500`; frozen clock.
- **Given** a matching event delivered at T0.
- **When** the receiver returns `500`.
- **Then** `status="pending"` (retryable), `attempts=1`, `last_response` captures the 500,
  and `next_retry_at ≈ T0 + 30s` (second slot of the default schedule
  `0s,30s,5m,30m,2h,12h`).
- **Covers:** retry scheduling on failure.

### WEBHOOKS-023
- **Title:** Timeout is treated as a retryable failure
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT`; receiver configured to time out.
- **Given** a matching event.
- **When** the POST exceeds the request timeout.
- **Then** the attempt is recorded as failed/retryable exactly like a non-2xx: `attempts`
  incremented, `next_retry_at` set, `status="pending"`; `last_response` notes the timeout.
- **Covers:** timeout = retryable (ARCHITECTURE §7).

### WEBHOOKS-024
- **Title:** Backoff schedule follows 0s, 30s, 5m, 30m, 2h, 12h across attempts
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT`; receiver always `503`; controllable clock
  + on-demand retry runner.
- **Given** an event whose delivery keeps failing.
- **When** the retry runner is advanced through each scheduled slot.
- **Then** successive `next_retry_at` deltas match the default schedule sequence (allowing
  for documented jitter), `attempts` increments 1→6, and `status` stays `pending` until the
  6th attempt resolves.
- **Covers:** default backoff schedule (DATA-MODEL §11).

### WEBHOOKS-025
- **Title:** Jitter keeps retries within bounded window of nominal delay
- **Level:** unit
- **Preconditions / fixtures:** backoff calculator function.
- **Given** attempt N with nominal delay d(N) from the schedule.
- **When** `next_retry_at` is computed repeatedly.
- **Then** each computed delay falls within the documented jitter band around d(N) (e.g.
  d ± jitter), is non-negative, and is monotonic per the schedule across N. Exponential
  backoff "with jitter" (ARCHITECTURE §7) never produces a delay shorter than the base of
  the slot below zero.
- **Covers:** backoff+jitter computation.

### WEBHOOKS-026
- **Title:** Sixth failed attempt marks delivery exhausted; no further retries
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT`; receiver always fails; clock advanced
  through all 6 slots.
- **Given** delivery has failed 5 times.
- **When** the 6th attempt also fails.
- **Then** `status="exhausted"`, `attempts=6`, `next_retry_at` cleared/null; the retry
  runner does not pick it up again. The exhausted row remains in the log.
- **Covers:** exhaustion terminal state (DATA-MODEL §11).

### WEBHOOKS-027
- **Title:** Recovery before exhaustion — a later attempt succeeds and stops retries
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT`; receiver fails twice then returns `200`;
  clock control.
- **Given** attempts 1–2 returned `500`.
- **When** the clock advances to the 3rd slot and the receiver now returns `200`.
- **Then** `status="delivered"`, `attempts=3`, `next_retry_at` cleared; no 4th attempt is
  scheduled. Transition `pending → delivered` is honored.
- **Covers:** mid-schedule recovery.

### WEBHOOKS-028
- **Title:** Retry resends the identical body, signature, and Event-Id (no re-sign drift)
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT`; receiver fails attempt 1, captures bodies.
- **Given** attempt 1 was delivered and recorded.
- **When** the retry attempt fires.
- **Then** the retried request carries the byte-identical body, the same
  `X-Arbor-Signature`, and the same `X-Arbor-Event-Id` as attempt 1 (the signature is over
  the original event body and is not recomputed per attempt). Consumer idempotency via
  Event-Id therefore holds across retries.
- **Covers:** retry payload/signature stability; idempotency interplay.

### WEBHOOKS-029
- **Title:** 2xx other than 200 (e.g. 202/204) counts as delivered
- **Level:** unit
- **Preconditions / fixtures:** delivery outcome classifier.
- **Given** receiver returns `202` (or `204`).
- **When** the response is classified.
- **Then** it is treated as success → `status="delivered"`; only non-2xx/timeout reschedule.
  Boundary at the 2xx range.
- **Covers:** success classification boundary.

### WEBHOOKS-030
- **Title:** 3xx redirect is not auto-followed and is treated as failure
- **Level:** unit
- **Preconditions / fixtures:** delivery outcome classifier.
- **Given** receiver returns `301`/`302`.
- **When** classified.
- **Then** per the 2xx-only success rule, a 3xx is non-2xx → retryable failure (the
  dispatcher does not silently follow redirects to an unverified URL). Asserts the documented
  "delivered on 2xx" boundary, not a redirect-following behavior.
- **Covers:** non-2xx classification boundary (ARCHITECTURE §7).

---

## E. Delivery log & idempotency

### WEBHOOKS-031
- **Title:** Every attempt is appended to the queryable per-endpoint delivery log
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT`; receiver fails twice then succeeds.
- **Given** a delivery that takes 3 attempts.
- **When** the attempts complete.
- **Then** the `Webhook Delivery` log for the endpoint reflects the full attempt history
  (`attempts=3`, `last_response` updated each attempt, final `status="delivered"`), and is
  filterable by `endpoint` for audit. (Per spec the row tracks `attempts`/`last_response`;
  assert the count and last-response progression are observable.)
- **Covers:** delivery-log auditability (ARCHITECTURE §7, DATA-MODEL §11).

### WEBHOOKS-032
- **Title:** One Webhook Delivery row per (endpoint, tree_event); duplicate dispatch is idempotent
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT`; dispatcher invoked twice for the same Tree
  Event (e.g. dispatcher re-run / at-least-once worker redelivery).
- **Given** a single Tree Event already produced a `pending` delivery for the endpoint.
- **When** the dispatcher processes the same Tree Event again.
- **Then** no duplicate `Webhook Delivery` row is created for that `(endpoint, tree_event)`
  pair; the existing row's retry state is reused. The append-only Tree Event log is never
  double-emitted, and dispatch is idempotent per (endpoint, event).
- **Covers:** dispatch idempotency; one-event→one-delivery-per-endpoint invariant.

### WEBHOOKS-033
- **Title:** Concurrent retry runners do not double-POST the same pending delivery
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT`; one `pending` delivery whose
  `next_retry_at` is due; two retry workers triggered concurrently.
- **Given** the delivery is eligible for retry.
- **When** two workers race to send it.
- **Then** the delivery is claimed/locked once; the receiver records exactly one POST for
  that attempt; `attempts` increments by exactly one. No double delivery from concurrency.
- **Covers:** retry-runner concurrency safety / attempt-claim.

### WEBHOOKS-034
- **Title:** Append-only stream guarantees stable event_id across redeliveries
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT`; one event, multiple delivery attempts.
- **Given** the Tree Event is append-only (no update/delete capability, DATA-MODEL §12).
- **When** the same event is (re)delivered across retries.
- **Then** `X-Arbor-Event-Id` / payload `event_id` is identical every time; the underlying
  Tree Event row is never mutated. Consumer dedup keyed on event_id is therefore sound.
- **Covers:** append-only Tree Event ↔ idempotent delivery.

---

## F. DRY: same stream as notifications, no divergent logic

### WEBHOOKS-035
- **Title:** Webhook and notification dispatch consume the identical Tree Event
- **Level:** e2e
- **Preconditions / fixtures:** `EXT_ENDPOINT` (sheet scope) AND persona G's in-app
  subscription on the same event type/scope.
- **Given** both an in-app subscriber (G) and a webhook endpoint (EXT) match the same event
  family.
- **When** a single matching event is emitted (one mutation).
- **Then** both a `Notification` row (for G) and a `Webhook Delivery` row (for EXT) are
  produced from the *same* `tree_event`; both reference that one event id. No second event
  is emitted for the webhook path. Confirms webhooks and notifications are co-derived from
  one stream (ARCHITECTURE §6/§7).
- **Covers:** shared fan-out; Notification + Webhook Delivery off one Tree Event.

### WEBHOOKS-036
- **Title:** Dispatcher emits no Tree Event of its own (consumer, not producer)
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT`; snapshot of Tree Event count before/after.
- **Given** a matching event triggers delivery (including failures/retries).
- **When** the dispatcher runs and retries.
- **Then** the Tree Event log count is unchanged by dispatch/delivery/retry activity — the
  webhook dispatcher contains no mutation logic and never writes to the stream
  (ARCHITECTURE §7). Delivery state lives only in `Webhook Delivery`.
- **Covers:** dispatcher-is-pure-consumer invariant.

### WEBHOOKS-037
- **Title:** Same scope/event matcher governs webhooks and notifications
- **Level:** unit
- **Preconditions / fixtures:** the shared `matching_subscriptions(event)` matcher; a
  webhook endpoint and a notification subscription with identical scope=branch/target=P2/
  event_types.
- **Given** an event on node X (outside P2) and an event on node Z (inside P2).
- **When** the matcher evaluates both the webhook endpoint and the notification
  subscription.
- **Then** both selectors agree: matched for Z, not matched for X — proving a single
  NestedSet-range matcher serves both consumers (no divergent branch logic).
- **Covers:** shared subscription matcher (ARCHITECTURE §6/§7).

### WEBHOOKS-038
- **Title:** A delivery=webhook Subscription routes through the webhook dispatcher
- **Level:** integration
- **Preconditions / fixtures:** A `Subscription` row with `delivery=webhook` referencing an
  endpoint (per DATA-MODEL §7 the `delivery` enum includes `webhook`).
- **Given** a subscription whose channel is `webhook`.
- **When** a matching event is emitted.
- **Then** the resulting `Notification.channel="webhook"` and the actual HTTP delivery is
  performed via the webhook dispatcher path (HMAC + retry), not via in-app/email — i.e. the
  webhook channel is the bridge between the notification ledger and the webhook delivery
  mechanism, sharing the single event stream.
- **Covers:** Subscription.delivery=webhook ↔ Notification.channel=webhook ↔ Webhook
  Delivery linkage.

---

## G. Permission-derived & boundary cases

### WEBHOOKS-039
- **Title:** Webhook subscription does NOT confer mutate authority (EXT still bound by ACL)
- **Level:** integration
- **Preconditions / fixtures:** EXT owns/edits no column; `EXT_ENDPOINT` subscribed.
- **Given** EXT (the external system's User) calls
  `POST /api/method/arbor.update_cell` on `col:budget` (owned by C).
- **When** the write is processed.
- **Then** the write is NOT authorized (EXT ∉ column approvers) → a Change Request to C +
  `CHANGE_PROPOSED` emitted (ARCHITECTURE §11 surface parity, PERMISSIONS §3 EXT). Having a
  webhook endpoint grants zero edit authority — subscription and authority are orthogonal.
  If subscribed to `CHANGE_PROPOSED`, EXT then receives a delivery for the CR it caused.
- **Covers:** capability `updateCell` (Axis 2 deny); Tree Event `CHANGE_PROPOSED`; webhook =
  derived consumer only.

### WEBHOOKS-040
- **Title:** Delegation event (DELEGATION_CHANGED) delivers when subscribed
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT` updated to subscribe to
  `["DELEGATION_CHANGED"]`, sheet scope.
- **Given** A executes `delegateBranch(branch_root=P2, grantee=D)` (or D sub-delegates Z),
  emitting `DELEGATION_CHANGED`.
- **When** the event is dispatched.
- **Then** a delivery is produced with `type="DELEGATION_CHANGED"` and payload identifying
  branch_root/grantee; demonstrates ownership-admin events are first-class stream events
  webhooks can observe.
- **Covers:** capabilities `delegateBranch`/`revokeDelegation`; Tree Event
  `DELEGATION_CHANGED`.

### WEBHOOKS-041
- **Title:** Delegation edge — branch-scoped endpoint follows nearest-grant subtree, not delegate identity
- **Level:** integration
- **Preconditions / fixtures:** `EXT_BRANCH` (scope=branch, target=P2,
  event_types=["NODE_CREATED"]); active grant D on P2.
- **Given** D executes `addNode(parent=Y)` (Y ∈ P2 subtree) → `NODE_CREATED`.
- **When** dispatched.
- **Then** the branch endpoint matches because the new node is within P2's NestedSet range —
  matching is by subtree membership of the affected node, independent of who the structural
  approver/actor (D) is. (A sibling add under P1 would not match.)
- **Covers:** branch-scope matching vs delegation; Tree Event `NODE_CREATED`.

### WEBHOOKS-042
- **Title:** moveNode that crosses into the subscribed branch delivers; node moving out does too, per matcher
- **Level:** integration
- **Preconditions / fixtures:** `EXT_BRANCH` (scope=branch, target=P2,
  event_types=["NODE_MOVED"]); authorized mover (A approves a move of X into P2, or D).
- **Given** a `moveNode` whose destination parent is inside P2 (e.g. X → Y) is approved and
  emits `NODE_MOVED`.
- **When** dispatched against the P2-scoped endpoint.
- **Then** the endpoint receives the `NODE_MOVED` delivery because the event's node now
  resides within P2's range at emit/serialization time; the payload identifies old/new
  parent. (Asserts the matcher's treatment of moved nodes is well-defined against the
  post-move position recorded in the event.)
- **Covers:** Tree Event `NODE_MOVED`; branch-scope matching on moves; `moveNode`.

### WEBHOOKS-043
- **Title:** Unsubscribed event type never produces a delivery (negative boundary)
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT` (event_types =
  NODE_VALUE_UPDATED, CHANGE_APPROVED only).
- **Given** events of every other type fire (NODE_CREATED, NODE_DELETED, NODE_MOVED,
  COLUMN_CONFIG_UPDATED, CHANGE_PROPOSED, CHANGE_REJECTED, SUBSCRIPTION_CHANGED,
  DELEGATION_CHANGED, IMPORT_COMPLETED).
- **When** each is emitted.
- **Then** zero `Webhook Delivery` rows are created for `EXT_ENDPOINT` for any of them; only
  the two subscribed types ever produce deliveries. Exhaustive negative coverage of the
  closed event-type set.
- **Covers:** event_types filter completeness over the closed Tree Event set.

### WEBHOOKS-044
- **Title:** Endpoint subscribed to unknown/invalid event type is rejected at create time
- **Level:** unit
- **Preconditions / fixtures:** endpoint create/validate path.
- **Given** an attempt to set `event_types=["NODE_EXPLODED"]` (not in the closed set).
- **When** the endpoint is validated.
- **Then** creation/update is rejected with a validation error; only members of the closed
  Tree Event type set are accepted. Guards against silent never-matching subscriptions.
- **Covers:** event_types validation against closed set (DATA-MODEL §12).

### WEBHOOKS-045
- **Title:** COLUMN_CONFIG_UPDATED (schema change) delivers to subscribed endpoint
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT` subscribed to `["COLUMN_CONFIG_UPDATED"]`,
  sheet scope.
- **Given** A executes `addColumn(...)` or C executes `deleteColumn(col:budget)` →
  `COLUMN_CONFIG_UPDATED`.
- **When** dispatched.
- **Then** a delivery with `type="COLUMN_CONFIG_UPDATED"` is produced; confirms meta/schema
  events ride the same webhook stream as data events.
- **Covers:** capabilities `addColumn`/`updateColumn`/`deleteColumn`; Tree Event
  `COLUMN_CONFIG_UPDATED`.

### WEBHOOKS-046
- **Title:** IMPORT_COMPLETED bulk event yields a single delivery (not per-row)
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT` subscribed to `["IMPORT_COMPLETED"]`.
- **Given** a bulk import completes on sheet S, emitting one `IMPORT_COMPLETED` event.
- **When** dispatched.
- **Then** exactly one `Webhook Delivery` is produced (one event → one delivery), regardless
  of how many nodes/cells the import touched; the webhook layer never invents per-row events
  beyond what the stream emitted.
- **Covers:** Tree Event `IMPORT_COMPLETED`; one-event-one-delivery under bulk ops.

### WEBHOOKS-047
- **Title:** CHANGE_REJECTED and withdrawal both surface as CHANGE_REJECTED deliveries
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT` subscribed to `["CHANGE_REJECTED"]`; an open
  CR from E.
- **Given** (a) C rejects the CR (`rejectChange` → `CHANGE_REJECTED`); and separately
  (b) the requester withdraws a CR (`withdrawChange` → `CHANGE_REJECTED` with
  status=withdrawn, per CAPABILITIES table).
- **When** each is dispatched.
- **Then** both produce `type="CHANGE_REJECTED"` deliveries; the payload/CR link lets the
  consumer distinguish reject vs withdraw via CR status. Asserts the documented mapping that
  withdraw also emits `CHANGE_REJECTED`.
- **Covers:** capabilities `rejectChange`/`withdrawChange`; Tree Event `CHANGE_REJECTED`.

### WEBHOOKS-048
- **Title:** Owner-self policy CR still produces stream events that webhooks observe
- **Level:** integration
- **Preconditions / fixtures:** sheet S with `settings.owners_must_use_change_requests=true`;
  `EXT_ENDPOINT` subscribed to `["CHANGE_PROPOSED","CHANGE_APPROVED","NODE_VALUE_UPDATED"]`.
- **Given** C (a legitimate column owner) executes `updateCell(X, col:budget)`; under the
  policy this yields a CR with C as self-approver + `CHANGE_PROPOSED` (PERMISSIONS §1.2).
- **When** C then approves their own CR.
- **Then** webhooks receive `CHANGE_PROPOSED`, then on approval `NODE_VALUE_UPDATED` +
  `CHANGE_APPROVED` — identical delivery behavior to a non-owner-originated CR. The webhook
  layer has no special-case for the self-approval policy.
- **Covers:** owner-self policy; Tree Events `CHANGE_PROPOSED`/`CHANGE_APPROVED`/
  `NODE_VALUE_UPDATED`.

### WEBHOOKS-049
- **Title:** Delivery to an event on a different sheet never reaches a sheet-scoped endpoint
- **Level:** integration
- **Preconditions / fixtures:** `EXT_ENDPOINT` (scope=sheet, target=S); a second sheet S2
  also active.
- **Given** an event is emitted on S2.
- **When** dispatched.
- **Then** no delivery is created for `EXT_ENDPOINT` (sheet equality fails). Confirms sheet
  scoping isolates tenants/sheets in fan-out.
- **Covers:** scope=sheet isolation boundary.

### WEBHOOKS-050
- **Title:** Surface parity — identical Tree Event regardless of originating surface, identical webhook payload
- **Level:** e2e
- **Preconditions / fixtures:** `EXT_ENDPOINT` subscribed to `NODE_VALUE_UPDATED`; the same
  authorized `updateCell(X, col:status, value=v)` performed three ways: web `executeAction`,
  REST `POST /api/method/arbor.update_cell`, and the agent tool — each by an actor with
  authority (B as editor on col:status; agent user also granted editor for its run).
- **Given** the three equivalent invocations (ARCHITECTURE §11 invariant).
- **When** each emits its `NODE_VALUE_UPDATED` and is delivered.
- **Then** the three webhook payloads are structurally identical except for actor/actor_type
  and event_id/timestamp — same `type`, `sheet`, and `payload.{node,column,old,new,version}`
  shape. The webhook surface cannot tell which surface originated the mutation beyond
  actor_type. Establishes the primary parity invariant for this surface.
- **Covers:** surface parity (ARCHITECTURE §11); Tree Event `NODE_VALUE_UPDATED`;
  capability `updateCell`.
