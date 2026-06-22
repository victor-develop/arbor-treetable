# Test-Case Catalog — Notifications & Ack Ledger

> **Surface:** `arbor.notify.dispatcher` (Tree Event → Notification / Acknowledgement
> fan-out), the `subscribe` / `unsubscribe` / `acknowledge` capabilities, and the
> "N notified / M acked" accountability report. Test-first; written against
> [`ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §6, [`DATA-MODEL.md`](../docs/DATA-MODEL.md)
> §7–9, [`CAPABILITIES.md`](../docs/CAPABILITIES.md), and [`PERMISSIONS.md`](../docs/PERMISSIONS.md) §2/§3-G.
>
> **Scope boundary.** This catalog covers the notification/ack consumer of the event
> stream. It does NOT re-test webhook delivery internals (HMAC, retries, backoff — that
> is the `webhooks` surface) except where the `delivery=webhook` channel intersects the
> dispatcher's fan-out. It does NOT re-test ACL resolution itself (the `permissions`
> surface) except where an ACL outcome is the *trigger* for a notification.

## Shared canonical fixtures (DO NOT redefine per test)

All cases assume the canonical world from `PERMISSIONS.md` §2. Referenced by name only:

- **Sheet `S`** — `structural_owner = A`, `status = active`,
  `settings.owners_must_use_change_requests = false` (unless a case overrides it).
- **Tree (NestedSet):** `R → {P1 → X, P2 → {Y, Z}}`. **Branch Grant** on `P2`,
  `grantee = D`, `active`, `scope = structure`.
- **Columns:** `col:name` (`is_label`, owner **B**), `col:status` (owner **C**,
  editors `[B]`), `col:budget` (owner **C**), `col:notes` (owner **B**).
- **Personas:** **A** root structural owner; **B**, **C** column owners; **D** delegated
  P2 owner; **E**, **F** suggest-only; **G** sensitive watcher; **EXT** external system
  (API consumer + Webhook Endpoint).
- **G's canonical subscription** (`SUB_G`): `subscribe(scope=branch, target=P2,
  event_types=[CHANGE_PROPOSED, CHANGE_APPROVED, NODE_DELETED], delivery=in-app,
  requires_ack=true)`. Per `PERMISSIONS.md` §3-G, G watches *every approved AND proposed*
  change in P2. Cases that need a narrower G subscription say so explicitly.
- **Event-stream invariant:** every authorized mutation emits exactly one Tree Event;
  every unauthorized mutation emits exactly one `CHANGE_PROPOSED`. The dispatcher runs
  `on_tree_event(event)` once per emitted event (`ARCHITECTURE.md` §6).
- **Idempotency baseline:** `(node, column)` unique on values; `(notification, user)`
  unique on Acknowledgement; one Notification per `(tree_event, recipient)`.

Helpers assumed available: `notifications_for(event)`, `notifications_for_cr(cr)`,
`acks_for(notification)`, `accountability(event_or_cr) -> {notified, acked}`,
`make_subscription(...)`, `emit_and_dispatch(...)` (emits a Tree Event and synchronously
runs the dispatcher), and a fake clock for `delivered_at` / `acked_at` assertions.

---

## A. Subscription lifecycle (`subscribe` / `unsubscribe`)

### NOTIFICATIONS_AND_ACK-001
- **Title:** Self-subscribe at sheet scope creates Subscription and emits SUBSCRIPTION_CHANGED
- **Level:** integration
- **Preconditions / fixtures:** Sheet `S`; persona **E** (no special grants).
- **Given** E has no subscription on `S`.
- **When** E calls `subscribe(scope=sheet, target=S, event_types=[CHANGE_APPROVED], delivery=in-app)`.
- **Then** exactly one Subscription row exists with `subscriber=E`, `subscriber_kind=user`,
  `scope=sheet`, `target=S`, `requires_ack=false` (schema default); and exactly one
  `SUBSCRIPTION_CHANGED` Tree Event is emitted with `actor=E`. No Notification rows are
  created by the subscribe call itself.
- **Covers:** `subscribe`; `SUBSCRIPTION_CHANGED`.

### NOTIFICATIONS_AND_ACK-002
- **Title:** subscribe defaults subscriber to the actor
- **Level:** unit
- **Preconditions / fixtures:** persona **E**.
- **Given** `subscribe` is called by E without an explicit `subscriber` field.
- **When** the capability handler runs.
- **Then** the created Subscription has `subscriber=E` (actor-defaulted per CAPABILITIES
  params schema), `subscriber_kind=user`.
- **Covers:** `subscribe`; `SUBSCRIPTION_CHANGED`.

### NOTIFICATIONS_AND_ACK-003
- **Title:** Admin may subscribe another user; non-admin may not subscribe a third party
- **Level:** integration
- **Preconditions / fixtures:** personas **A** (acts as sheet admin / structural_owner),
  **E**, **F**.
- **Given** the ACL rule for `subscribe` is "self-subscribe, or admin for others"
  (CAPABILITIES registry).
- **When** (a) A calls `subscribe(subscriber=E, scope=sheet, target=S, ...)`; **and**
  (b) F calls `subscribe(subscriber=E, scope=sheet, target=S, ...)`.
- **Then** (a) succeeds and creates a Subscription with `subscriber=E`, emitting
  `SUBSCRIPTION_CHANGED`; (b) is rejected by ACL (F is neither E nor an admin) — no
  Subscription, no event.
- **Covers:** `subscribe`; `SUBSCRIPTION_CHANGED`.

### NOTIFICATIONS_AND_ACK-004
- **Title:** Branch-scope subscription targets a Tree Node (branch root)
- **Level:** unit
- **Preconditions / fixtures:** persona **G**, node `P2`.
- **Given** `SUB_G` (`scope=branch, target=P2`).
- **When** the Subscription is created.
- **Then** `target` resolves as a Dynamic Link to Tree Node `P2`; `scope=branch`. A
  validation error is raised if `target` is a Tree Sheet while `scope=branch` (scope/target
  kind mismatch).
- **Covers:** `subscribe`; `SUBSCRIPTION_CHANGED`.

### NOTIFICATIONS_AND_ACK-005
- **Title:** Column-scope subscription targets a Tree Column
- **Level:** unit
- **Preconditions / fixtures:** persona **C**, column `col:budget`.
- **Given** `subscribe(scope=column, target=col:budget, event_types=[NODE_VALUE_UPDATED], delivery=email)`.
- **When** the Subscription is created.
- **Then** `target` is a Dynamic Link to Tree Column `col:budget`; `delivery=email`.
- **Covers:** `subscribe`; `SUBSCRIPTION_CHANGED`.

### NOTIFICATIONS_AND_ACK-006
- **Title:** unsubscribe removes the subscription and emits SUBSCRIPTION_CHANGED
- **Level:** integration
- **Preconditions / fixtures:** persona **E** with an existing sheet-scope subscription `SUB_E`.
- **Given** `SUB_E` exists.
- **When** E calls `unsubscribe(subscription=SUB_E)`.
- **Then** `SUB_E` is removed (or deactivated per impl), one `SUBSCRIPTION_CHANGED` is
  emitted, and subsequent matching Tree Events produce no Notification for E.
- **Covers:** `unsubscribe`; `SUBSCRIPTION_CHANGED`.

### NOTIFICATIONS_AND_ACK-007
- **Title:** unsubscribe by a non-owner of the subscription is denied
- **Level:** integration
- **Preconditions / fixtures:** personas **E** (owns `SUB_E`), **F**.
- **Given** ACL for `unsubscribe` is "owner of the subscription".
- **When** F calls `unsubscribe(subscription=SUB_E)`.
- **Then** the call is rejected by ACL; `SUB_E` is unchanged; no `SUBSCRIPTION_CHANGED`
  emitted. (Note: unlike mutating capabilities, an unauthorized `unsubscribe` does NOT
  create a Change Request — it is a notification-admin op, not a governed tree mutation.)
- **Covers:** `unsubscribe`; `SUBSCRIPTION_CHANGED`.

### NOTIFICATIONS_AND_ACK-008
- **Title:** Re-subscribing identical scope/target is idempotent (no duplicate subscriptions)
- **Level:** integration
- **Preconditions / fixtures:** persona **E** with existing `SUB_E` (`scope=sheet, target=S, event_types=[CHANGE_APPROVED], delivery=in-app`).
- **Given** `SUB_E` already exists.
- **When** E calls `subscribe` with identical scope/target/delivery/event_types.
- **Then** no second active Subscription with the same `(subscriber, scope, target, delivery)`
  is created (upsert semantics); a later matching event yields exactly ONE Notification for E,
  not two.
- **Covers:** `subscribe`; `SUBSCRIPTION_CHANGED`.

---

## B. Dispatcher fan-out & scope matching

### NOTIFICATIONS_AND_ACK-009
- **Title:** Sheet-scope subscriber notified on any matching event in the sheet
- **Level:** integration
- **Preconditions / fixtures:** persona **E** with `SUB_E (scope=sheet, target=S, event_types=[NODE_VALUE_UPDATED], delivery=in-app)`.
- **Given** B updates `col:name` on `X` (authorized, Axis 2) → `NODE_VALUE_UPDATED` emitted.
- **When** the dispatcher runs `on_tree_event`.
- **Then** exactly one Notification row exists for `(that event, E)` with `channel=in-app`,
  `delivered_at` set, `requires_ack=false`.
- **Covers:** `updateCell`/`NODE_VALUE_UPDATED`; dispatcher fan-out (`subscribe`).

### NOTIFICATIONS_AND_ACK-010
- **Title:** Branch-scope match uses NestedSet descendant range (descendant node matches)
- **Level:** integration
- **Preconditions / fixtures:** `SUB_G` (branch, target=P2). Node `Z` is a descendant of `P2`.
- **Given** D approves a CR that deletes `Z` → `NODE_DELETED` emitted with the node in payload (Z descendant of P2).
- **When** the dispatcher matches subscriptions.
- **Then** G receives a Notification (Z's `lft/rgt` fall within `P2.lft..P2.rgt`),
  `requires_ack=true`.
- **Covers:** `deleteNode`/`NODE_DELETED`; branch-scope matching.

### NOTIFICATIONS_AND_ACK-010b
- **Title:** Branch-scope match includes the branch root node itself (inclusive boundary)
- **Level:** integration
- **Preconditions / fixtures:** `SUB_G` (branch, target=P2).
- **Given** an event whose subject node IS `P2` itself (e.g. a `NODE_MOVED` of P2 — assume
  G's subscription includes that type for this case).
- **When** the dispatcher matches.
- **Then** G is matched — branch scope is inclusive of the root `P2`, not strictly its
  descendants. Asserts the boundary `lft >= P2.lft AND rgt <= P2.rgt` (inclusive), not the
  strict-descendant query in DATA-MODEL §3 which is used for *descendant-of* counting.
- **Covers:** branch-scope matching; boundary condition.

### NOTIFICATIONS_AND_ACK-011
- **Title:** Branch-scope subscriber NOT notified for events outside the branch
- **Level:** integration
- **Preconditions / fixtures:** `SUB_G` (branch, target=P2).
- **Given** A deletes `X` (descendant of P1, NOT in P2) → `NODE_DELETED` emitted.
- **When** the dispatcher matches.
- **Then** no Notification row exists for G (X is outside P2's `lft/rgt` range).
- **Covers:** `deleteNode`/`NODE_DELETED`; branch-scope negative match.

### NOTIFICATIONS_AND_ACK-012
- **Title:** Column-scope subscriber matched only for that column's value events
- **Level:** integration
- **Preconditions / fixtures:** persona **C** with `SUB_C (scope=column, target=col:budget, event_types=[NODE_VALUE_UPDATED], delivery=email)`.
- **Given** (a) C updates `col:budget` on `Y` → `NODE_VALUE_UPDATED{column=col:budget}`;
  **and** (b) B updates `col:name` on `Y` → `NODE_VALUE_UPDATED{column=col:name}`.
- **When** the dispatcher matches each event.
- **Then** (a) produces a Notification for C; (b) does NOT (event's `payload.column` ≠ target).
  Column-scope match is direct equality on `payload.column` (ARCHITECTURE §6).
- **Covers:** `updateCell`/`NODE_VALUE_UPDATED`; column-scope matching.

### NOTIFICATIONS_AND_ACK-013
- **Title:** event_types filter excludes non-subscribed event types
- **Level:** integration
- **Preconditions / fixtures:** persona **E** with `SUB_E (scope=sheet, target=S, event_types=[CHANGE_APPROVED], delivery=in-app)`.
- **Given** a `CHANGE_PROPOSED` event is emitted on `S` (E subscribed only to `CHANGE_APPROVED`).
- **When** the dispatcher runs.
- **Then** no Notification for E (event type not in subscription's `event_types`).
- **Covers:** dispatcher event_types filter.

### NOTIFICATIONS_AND_ACK-014
- **Title:** Multiple matching subscriptions for one recipient collapse to one Notification per event
- **Level:** integration
- **Preconditions / fixtures:** persona **C** with TWO subscriptions matching the same
  event: `SUB_C1 (scope=sheet, target=S, event_types=[NODE_VALUE_UPDATED])` and
  `SUB_C2 (scope=column, target=col:budget, event_types=[NODE_VALUE_UPDATED])`, both `delivery=in-app`.
- **Given** C's own update of `col:budget` on `Y` emits `NODE_VALUE_UPDATED` (or another
  actor's, so C is purely a subscriber).
- **When** the dispatcher runs (both subscriptions match).
- **Then** exactly ONE Notification row exists for `(event, C, channel=in-app)` —
  Notification is keyed by `(tree_event, recipient)` (DATA-MODEL §8); overlapping
  subscriptions do not double-notify on the same channel.
- **Covers:** dispatcher fan-out idempotency; `(tree_event, recipient)` uniqueness.

### NOTIFICATIONS_AND_ACK-014b
- **Title:** Same recipient with two different delivery channels gets one Notification per channel
- **Level:** integration
- **Preconditions / fixtures:** persona **C** with `SUB_C_inapp (delivery=in-app)` and
  `SUB_C_email (delivery=email)`, both sheet-scope matching `NODE_VALUE_UPDATED`.
- **Given** a matching `NODE_VALUE_UPDATED` event.
- **When** the dispatcher runs.
- **Then** exactly TWO Notification rows for C — one `channel=in-app`, one `channel=email`
  (Notification carries `channel`; distinct channels are distinct deliveries).
- **Covers:** dispatcher fan-out; multi-channel delivery.

### NOTIFICATIONS_AND_ACK-015
- **Title:** Dispatcher sets delivered_at and channel from the subscription
- **Level:** unit
- **Preconditions / fixtures:** persona **E**, sheet-scope `delivery=email` subscription.
- **Given** a matching event and a fixed fake clock at `T0`.
- **When** `deliver(notif, sub.delivery)` runs.
- **Then** the Notification has `channel=email` and `delivered_at=T0`.
- **Covers:** dispatcher `deliver`; Notification fields.

### NOTIFICATIONS_AND_ACK-016
- **Title:** Notification copies change_request link when the event relates to a CR
- **Level:** integration
- **Preconditions / fixtures:** `SUB_G`; a Change Request `CR1` for deleting `Z`.
- **Given** the `CHANGE_PROPOSED` event for `CR1` carries `change_request=CR1`.
- **When** the dispatcher creates G's Notification.
- **Then** the Notification's `change_request` field = `CR1` and `tree_event` = the
  `CHANGE_PROPOSED` event (DATA-MODEL §8; nullable when no CR involved).
- **Covers:** `suggestChange`/`CHANGE_PROPOSED`; Notification `change_request` linkage.

### NOTIFICATIONS_AND_ACK-017
- **Title:** Pure-read and ack actions emit no Tree Event, so produce no notifications
- **Level:** integration
- **Preconditions / fixtures:** persona **E** subscribed sheet-scope to all event types.
- **Given** E calls `getSheetSnapshot(S)` and later `acknowledge(notification)`.
- **When** each runs.
- **Then** neither emits a Tree Event (CAPABILITIES: getSheetSnapshot "read; no event",
  acknowledge "no Tree Event") → the dispatcher is never invoked → no Notification rows
  result from these calls.
- **Covers:** `getSheetSnapshot`, `acknowledge` (no-event contract).

---

## C. Proposed / approved / rejected lifecycle notifications

### NOTIFICATIONS_AND_ACK-018
- **Title:** Non-owner suggestion notifies the resolved approver on CHANGE_PROPOSED
- **Level:** integration
- **Preconditions / fixtures:** persona **C** subscribed sheet-scope to `[CHANGE_PROPOSED]`
  (`delivery=in-app`); persona **E**.
- **Given** E calls `updateCell(X, col:budget, ...)`; E ∉ `{C}` → unauthorized → CR routed
  to C, `CHANGE_PROPOSED` emitted (PERMISSIONS §3-E).
- **When** the dispatcher runs.
- **Then** C receives a Notification for the `CHANGE_PROPOSED` event, with
  `change_request` linked to the new CR.
- **Covers:** `updateCell` (suggested branch), `CHANGE_PROPOSED`; dispatcher.

### NOTIFICATIONS_AND_ACK-019
- **Title:** Approval emits the real mutation event AND CHANGE_APPROVED — subscribers to each are notified
- **Level:** integration
- **Preconditions / fixtures:** A CR `CR1` (delete `Z`, resolved_approver `D`); `SUB_G`
  (branch P2, includes `CHANGE_APPROVED` and `NODE_DELETED`).
- **Given** D calls `approveChange(CR1)`.
- **When** the handler replays the delete as D → emits `NODE_DELETED`, then emits
  `CHANGE_APPROVED` (ARCHITECTURE §5).
- **Then** the dispatcher runs TWICE (once per event). G has TWO Notification rows: one for
  `NODE_DELETED`, one for `CHANGE_APPROVED`, both `requires_ack=true`. (Asserts the surface's
  promise that the watcher sees every *approved* change in P2.)
- **Covers:** `approveChange` (`NODE_DELETED` + `CHANGE_APPROVED`); dispatcher fan-out.

### NOTIFICATIONS_AND_ACK-020
- **Title:** Rejection notifies subscribers to CHANGE_REJECTED but emits no mutation event
- **Level:** integration
- **Preconditions / fixtures:** persona **E** (requester) subscribed sheet-scope to
  `[CHANGE_REJECTED]`; CR `CR2` with `resolved_approver=C`.
- **Given** C calls `rejectChange(CR2)`.
- **When** the handler runs.
- **Then** exactly one `CHANGE_REJECTED` event is emitted (no `NODE_VALUE_UPDATED`); E gets
  one Notification for it. The CR status is `rejected`; no data mutation occurred.
- **Covers:** `rejectChange`/`CHANGE_REJECTED`; dispatcher.

### NOTIFICATIONS_AND_ACK-021
- **Title:** Withdrawal emits CHANGE_REJECTED(status=withdrawn) — approver subscription behavior
- **Level:** integration
- **Preconditions / fixtures:** persona **E** (requester of `CR3`), persona **C**
  (resolved_approver) subscribed sheet-scope to `[CHANGE_REJECTED]`.
- **Given** E calls `withdrawChange(CR3)`.
- **When** the handler runs (per CAPABILITIES, withdraw emits `CHANGE_REJECTED` with
  status=withdrawn; ARCHITECTURE §5 notes withdrawal is "silent to approvers except as a
  status change").
- **Then** a `CHANGE_REJECTED` event is emitted carrying the withdrawn status in payload.
  Assert the dispatcher's documented behavior: subscribers to `CHANGE_REJECTED` receive the
  event row, and the payload distinguishes `withdrawn` from approver-`rejected` so a UI can
  suppress/soften it. (This case pins the "silent to approvers" intent to an assertable
  payload flag rather than a separate event type.)
- **Covers:** `withdrawChange`/`CHANGE_REJECTED`; dispatcher payload semantics.

### NOTIFICATIONS_AND_ACK-022
- **Title:** Owner-self policy CR still flows through the notification ledger
- **Level:** integration
- **Preconditions / fixtures:** Sheet `S` with `settings.owners_must_use_change_requests=true`;
  persona **C** (owner of `col:budget`); persona **A** subscribed sheet-scope to `[CHANGE_PROPOSED]`.
- **Given** C calls `updateCell(Y, col:budget, ...)`. Per PERMISSIONS §1.2 the authorized
  owner still produces a CR with C as their own `resolved_approver` → `CHANGE_PROPOSED`.
- **When** the dispatcher runs.
- **Then** a `CHANGE_PROPOSED` Notification is delivered to A (and any other matching
  subscriber). The notification ledger does not special-case self-approver CRs.
- **Covers:** `updateCell` (owner-self CR), `CHANGE_PROPOSED`; dispatcher.

---

## D. Persona G — sensitive watcher (proposed AND approved) + acknowledgement

### NOTIFICATIONS_AND_ACK-023
- **Title:** G is notified on a PROPOSED change within P2 with requires_ack=true
- **Level:** integration
- **Preconditions / fixtures:** `SUB_G` (branch P2, includes `CHANGE_PROPOSED`,
  `requires_ack=true`); persona **F** (suggest-only).
- **Given** F calls `addNode(parent=Y)` (Y ∈ P2); F ≠ D → unauthorized → CR routed to D,
  `CHANGE_PROPOSED` emitted (PERMISSIONS §3-F). The CR's structural target node is within P2.
- **When** the dispatcher runs.
- **Then** G receives a Notification for the `CHANGE_PROPOSED` event with `requires_ack=1`
  and no Acknowledgement yet. Confirms persona G watches *proposed* (not only approved)
  changes in their sensitive branch.
- **Covers:** `addNode` (suggested), `CHANGE_PROPOSED`; branch matching; requires_ack.

### NOTIFICATIONS_AND_ACK-024
- **Title:** requires_ack flag is copied from subscription to Notification
- **Level:** unit
- **Preconditions / fixtures:** `SUB_G` (`requires_ack=true`); a matching event.
- **Given** the dispatcher creates G's Notification.
- **When** `create_notification` + `mark_ack_required` run.
- **Then** `Notification.requires_ack == True` (copied from subscription, DATA-MODEL §8);
  for a non-ack subscriber the same event yields `requires_ack == False`.
- **Covers:** dispatcher `mark_ack_required`; Notification `requires_ack`.

### NOTIFICATIONS_AND_ACK-025
- **Title:** acknowledge creates exactly one Acknowledgement row and sets acked_at
- **Level:** integration
- **Preconditions / fixtures:** persona **G** with an undelivered-ack Notification `N1`
  (`requires_ack=1`); fake clock at `T1`.
- **Given** `N1` has no Acknowledgement.
- **When** G calls `acknowledge(notification=N1)`.
- **Then** exactly one Acknowledgement row exists with `notification=N1`, `user=G`,
  `acked_at=T1`; **no Tree Event is emitted** (CAPABILITIES: acknowledge "Acknowledgement
  row; no Tree Event").
- **Covers:** `acknowledge`; Acknowledgement row (no event).

### NOTIFICATIONS_AND_ACK-026
- **Title:** acknowledge is idempotent — second ack does not create a duplicate row
- **Level:** integration
- **Preconditions / fixtures:** persona **G**, Notification `N1` already acked at `T1`.
- **Given** an Acknowledgement `(N1, G)` exists.
- **When** G calls `acknowledge(N1)` again.
- **Then** still exactly one Acknowledgement row for `(N1, G)` (uniqueness constraint
  `(notification, user)`, DATA-MODEL §13); `acked_at` is unchanged (or the op is a no-op
  success). No error that loses the original ack.
- **Covers:** `acknowledge`; `(notification, user)` uniqueness.

### NOTIFICATIONS_AND_ACK-027
- **Title:** acknowledge by a non-recipient is denied
- **Level:** integration
- **Preconditions / fixtures:** persona **G** owns Notification `N1`; persona **F**.
- **Given** ACL for `acknowledge` is "recipient of the notification" (CAPABILITIES).
- **When** F calls `acknowledge(N1)`.
- **Then** rejected by ACL; no Acknowledgement row created; `N1` still unacked. (Not a CR —
  ack is a ledger op, not a governed tree mutation.)
- **Covers:** `acknowledge` (ACL deny).

### NOTIFICATIONS_AND_ACK-028
- **Title:** acknowledge on a notification that does not require ack is a harmless no-op / rejected per policy
- **Level:** integration
- **Preconditions / fixtures:** persona **E** with Notification `N2` where `requires_ack=false`.
- **Given** `N2.requires_ack == False`.
- **When** E calls `acknowledge(N2)`.
- **Then** the system follows its documented contract: it does NOT count toward any
  accountability report (the report counts only `requires_ack=1` notifications). Assert that
  either (impl choice, pin one) the ack row is created but excluded from `M acked` for
  ack-required ledgers, OR the call is a validated no-op. Either way `accountability` for
  ack-required events is unaffected.
- **Covers:** `acknowledge`; accountability isolation.

---

## E. Accountability report ("N notified / M acked")

### NOTIFICATIONS_AND_ACK-029
- **Title:** Report counts notified vs acked for a single ack-required event
- **Level:** integration
- **Preconditions / fixtures:** `SUB_G` (`requires_ack=true`); a `CHANGE_APPROVED` event in P2.
- **Given** the dispatcher created G's Notification (`requires_ack=1`) and G has NOT acked yet.
- **When** `accountability(event)` is computed.
- **Then** result = `{notified: 1, acked: 0}` → renders "1 notified / 0 acked". After G
  acknowledges, recomputing yields `{notified: 1, acked: 1}` → "1 notified / 1 acked"
  (matches PERMISSIONS §3-G worked example).
- **Covers:** accountability aggregate; `acknowledge`; `CHANGE_APPROVED`.

### NOTIFICATIONS_AND_ACK-030
- **Title:** Report counts only requires_ack notifications in the denominator
- **Level:** integration
- **Preconditions / fixtures:** one event matched by `SUB_G` (`requires_ack=true`) AND by
  `SUB_E` (sheet-scope, `requires_ack=false`).
- **Given** the dispatcher created Notifications for both G (ack-required) and E (not).
- **When** `accountability(event)` is computed.
- **Then** `notified == 1` (only G's ack-required Notification counts), not 2; `acked`
  reflects only ack-required acks. The non-ack subscriber E is excluded from the ledger
  numerator/denominator (ARCHITECTURE §6: count over `Notification where requires_ack`).
- **Covers:** accountability aggregate scoping.

### NOTIFICATIONS_AND_ACK-031
- **Title:** Report aggregates across multiple ack-required recipients
- **Level:** integration
- **Preconditions / fixtures:** two sensitive watchers G and G2, each branch-subscribed to
  P2 with `requires_ack=true`; one `CHANGE_APPROVED` event in P2.
- **Given** both are notified; only G acknowledges.
- **When** `accountability(event)` is computed.
- **Then** `{notified: 2, acked: 1}` → "2 notified / 1 acked". After G2 acks → "2/2".
- **Covers:** accountability aggregate (multi-recipient).

### NOTIFICATIONS_AND_ACK-032
- **Title:** Report can be scoped to a Change Request spanning its proposed + approved events
- **Level:** integration
- **Preconditions / fixtures:** CR `CR1` (delete Z in P2); `SUB_G` (`requires_ack=true`,
  watching both `CHANGE_PROPOSED` and `CHANGE_APPROVED`). CR proposed then approved.
- **Given** G is notified on `CHANGE_PROPOSED` (and `NODE_DELETED` + `CHANGE_APPROVED` on
  approval), all carrying `change_request=CR1`.
- **When** `accountability(CR1)` is computed (CR-scoped, per ARCHITECTURE §6 "for a given
  Tree Event **or** Change Request").
- **Then** the report aggregates all ack-required Notifications whose `change_request=CR1`;
  `notified` counts those Notifications, `acked` counts the corresponding Acknowledgements.
  Asserts the report works keyed by CR, not only by single event.
- **Covers:** accountability aggregate (CR-scoped); Notification `change_request` linkage.

### NOTIFICATIONS_AND_ACK-033
- **Title:** Report for an event with no ack-required subscribers is "0 notified / 0 acked"
- **Level:** unit
- **Preconditions / fixtures:** an event matched only by non-ack subscribers (or none).
- **Given** no Notification has `requires_ack=1` for the event.
- **When** `accountability(event)` is computed.
- **Then** `{notified: 0, acked: 0}` (no division-by-zero; clean zero state).
- **Covers:** accountability aggregate (empty/boundary).

---

## F. External subscriber, agent actor, and channel boundaries

### NOTIFICATIONS_AND_ACK-034
- **Title:** External (webhook) subscriber is fanned out as a Notification with channel=webhook
- **Level:** integration
- **Preconditions / fixtures:** **EXT** as a subscriber with `delivery=webhook`
  (`subscriber_kind=external`), sheet-scope, `event_types=[CHANGE_APPROVED]`.
- **Given** a `CHANGE_APPROVED` event on `S`.
- **When** the dispatcher runs.
- **Then** a Notification with `channel=webhook` is produced for EXT. (Webhook *delivery*
  internals — HMAC/retries — are the webhook surface and out of scope here; this asserts
  only that the notification dispatcher treats webhook as a delivery channel and creates the
  ledger row. The actual transmission is handled by the webhook dispatcher consuming the
  same event.)
- **Covers:** dispatcher fan-out; `channel=webhook`; subscriber_kind=external.

### NOTIFICATIONS_AND_ACK-035
- **Title:** Agent-actor mutation drives identical notifications as a human actor
- **Level:** integration
- **Preconditions / fixtures:** the agent (its own Frappe User, `actor_type=agent`) owns/edits
  `col:notes`-equivalent authority for a direct edit; persona **E** subscribed sheet-scope to
  `[NODE_VALUE_UPDATED]`.
- **Given** the agent executes an authorized `updateCell` → `NODE_VALUE_UPDATED` with
  `actor_type=agent`.
- **When** the dispatcher runs.
- **Then** E's Notification is created identically to a human-actor case; the dispatcher does
  not branch on `actor_type` (surface parity, ARCHITECTURE §11). The Notification carries the
  event whose `actor_type=agent`.
- **Covers:** dispatcher fan-out (actor-agnostic); `NODE_VALUE_UPDATED`.

### NOTIFICATIONS_AND_ACK-036
- **Title:** Unauthorized agent action notifies approver via CHANGE_PROPOSED like any human non-owner
- **Level:** integration
- **Preconditions / fixtures:** agent User lacking `col:budget` authority; persona **C**
  subscribed sheet-scope to `[CHANGE_PROPOSED]`.
- **Given** the agent calls `updateCell(Y, col:budget, ...)` → unauthorized → CR to C,
  `CHANGE_PROPOSED` (ARCHITECTURE §8: agent = human under ACL).
- **When** the dispatcher runs.
- **Then** C is notified of a `CHANGE_PROPOSED` whose CR `requester` is the agent User; the
  notification path is identical to persona E's suggestion (case 018).
- **Covers:** `updateCell` (agent suggested), `CHANGE_PROPOSED`; dispatcher parity.

---

## G. Ordering, integrity, and edge conditions

### NOTIFICATIONS_AND_ACK-037
- **Title:** Branch subscription created AFTER an event does not retroactively notify
- **Level:** integration
- **Preconditions / fixtures:** persona **G**; node deletion event in P2 emitted at `T0`.
- **Given** the `NODE_DELETED` event already exists; G then creates `SUB_G` at `T1 > T0`.
- **When** the dispatcher only runs on new events (`on_tree_event`).
- **Then** no Notification for G for the pre-existing event; subscriptions are not
  back-filled. Only events emitted after subscription creation match.
- **Covers:** dispatcher temporal semantics; `subscribe`.

### NOTIFICATIONS_AND_ACK-038
- **Title:** Subscription removed before an event yields no notification (no stale fan-out)
- **Level:** integration
- **Preconditions / fixtures:** persona **E** with `SUB_E`; E unsubscribes; then a matching
  event is emitted.
- **Given** `SUB_E` removed via `unsubscribe`, then `NODE_VALUE_UPDATED` emitted.
- **When** the dispatcher runs.
- **Then** no Notification for E (matching reads live subscriptions only).
- **Covers:** `unsubscribe`; dispatcher matching.

### NOTIFICATIONS_AND_ACK-039
- **Title:** Notifications reference an append-only event; the report is stable across re-query
- **Level:** integration
- **Preconditions / fixtures:** any ack-required event with N notified / M acked.
- **Given** the Tree Event is append-only (DATA-MODEL §12) and Notifications/Acks are not
  mutated after creation (except Acknowledgement insert).
- **When** `accountability(event)` is queried twice with no new acks between.
- **Then** identical `{notified, acked}` both times (deterministic aggregate; no event
  mutation can change the denominator after the fact).
- **Covers:** accountability stability; Tree Event append-only.

### NOTIFICATIONS_AND_ACK-040
- **Title:** A node moved into a watched branch is matched by current NestedSet position
- **Level:** integration
- **Preconditions / fixtures:** `SUB_G` (branch P2). Node `X` originally under P1; an
  authorized `moveNode(X → P2)` (or approval thereof) emits `NODE_MOVED`, then a subsequent
  `NODE_VALUE_UPDATED` on X occurs while X is now inside P2. Assume G's subscription includes
  `NODE_VALUE_UPDATED` for this case.
- **Given** after the move, X's `lft/rgt` fall within P2's range.
- **When** the post-move `NODE_VALUE_UPDATED` on X is dispatched.
- **Then** G IS matched (branch membership is evaluated against the node's current
  NestedSet position at dispatch time, not its position when the subscription was created).
- **Covers:** branch-scope matching after `moveNode`; NestedSet range correctness.

### NOTIFICATIONS_AND_ACK-041
- **Title:** Dispatcher creates no Notification for a subscriber whose target node was deleted
- **Level:** integration
- **Preconditions / fixtures:** a branch subscription whose `target` branch root was deleted
  (cascade) in a prior event.
- **Given** the branch-root node no longer exists (or its subtree is gone).
- **When** a later event is dispatched.
- **Then** the dispatcher handles the dangling target gracefully — no match / no crash; the
  orphaned subscription matches nothing (and may be flagged for cleanup). Asserts robustness,
  not retroactive deletion of past notifications.
- **Covers:** dispatcher robustness; branch-scope matching (dangling target).

### NOTIFICATIONS_AND_ACK-042
- **Title:** Surface parity — same subscribe via REST method and via executeAction yield identical Subscription + event
- **Level:** e2e
- **Preconditions / fixtures:** persona **E**; identical `subscribe` params.
- **Given** the same call is issued (a) as `POST /api/method/arbor.subscribe` and
  (b) via `execute_action("subscribe", params, actor=E)`.
- **When** each path runs.
- **Then** both create an equivalent Subscription and emit one `SUBSCRIPTION_CHANGED`; the
  authority decision and resulting rows are identical (ARCHITECTURE §11 parity invariant).
- **Covers:** `subscribe`; `SUBSCRIPTION_CHANGED`; surface parity.

### NOTIFICATIONS_AND_ACK-043
- **Title:** End-to-end sensitive-change ledger: F proposes in P2, D approves, G acks, report closes
- **Level:** e2e
- **Preconditions / fixtures:** `SUB_G` (branch P2, `requires_ack=true`, watching
  `CHANGE_PROPOSED` + `CHANGE_APPROVED` + `NODE_DELETED`); personas F (requester), D (approver).
- **Given** F calls `deleteNode(Z)` (Z ∈ P2); F ≠ D → CR to D + `CHANGE_PROPOSED`.
- **When** (1) dispatcher notifies G (proposed, ack-required); (2) D calls
  `approveChange(CR)` → `NODE_DELETED` + `CHANGE_APPROVED`; dispatcher notifies G for both;
  (3) G acknowledges all ack-required notifications for the CR.
- **Then** at step (1) `accountability(CR) = {notified:1, acked:0}`; after step (2)
  notified increases to reflect each ack-required notification; after step (3)
  `acked == notified` → report reads fully acknowledged "N notified / N acked". Asserts the
  whole proposed→approved→ack loop for the sensitive-watcher persona.
- **Covers:** `deleteNode` (suggested), `approveChange`, `acknowledge`; `CHANGE_PROPOSED`,
  `NODE_DELETED`, `CHANGE_APPROVED`; accountability (CR-scoped).
