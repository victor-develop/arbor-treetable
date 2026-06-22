# Arbor — Decision Record (ADRs)

> Records the four previously-open questions, now **resolved and locked**. These
> are applied throughout the core (`arbor/core/`) and must not be re-opened.
> Companion to [`ARCHITECTURE.md`](./ARCHITECTURE.md),
> [`CAPABILITIES.md`](./CAPABILITIES.md), [`PERMISSIONS.md`](./PERMISSIONS.md).

---

## ADR-001 — `moveNode` dual-end authority

**Status:** Accepted (locked).

**Context.** A `moveNode` touches two branches: the source parent's branch and
the destination parent's branch. Each may resolve (via the Axis-1 walk) to a
different structural approver. PERMISSIONS §4.4 requires the move to be
authorized only when the actor holds authority over **both** ends; otherwise it
must be suggested.

**Decision.** Model the suggestion as a **SINGLE Change Request** with
`payload.co_approvers` and an `approvals[]` child list. The CR is routed to the
destination approver as `resolved_approver`; the source approver is recorded in
`payload.co_approvers`. The CR transitions to `approved` (and replays the
`moveNode` handler) **only once** `resolved_approver` **AND every co-approver**
has approved. Each approval is recorded in `approvals[]`; a partial approval
keeps the CR `proposed` and returns the still-pending approvers.

**Rationale.** One CR (not two) keeps the audit trail and notification fan-out
coherent: there is exactly one governance object per proposed move, with a clear
"all required parties have signed off" gate. It reuses the existing CR replay
path verbatim, so an approved move emits the identical `NODE_MOVED` event a
directly-authorized move would (surface parity, ARCHITECTURE §11).

**Implementation.** `core/acl.py::_resolve_structure_authority` (moveNode branch
sets `resolved_approver=dest`, `co_approvers=(src,)` when `src != dest`);
`core/change_request.py::approve_change` (multi-approval gate +
`_all_required_approvers`). Tested in
`tests/core/test_change_request.py::test_move_node_dual_approval_requires_both`.

---

## ADR-002 — `addColumn` authority + column-creation policy

**Status:** Accepted (locked).

**Context.** Adding a column is schema co-design. CAPABILITIES.md assigns it the
`meta` axis. The open question was whose authority gates it and whether it should
be configurable.

**Decision.** `addColumn` authority = the sheet's `structural_owner`. Add a
`settings.column_creation` **policy placeholder** defaulting to `"owner-only"`.
The resolver reads this flag; under `owner-only` only the structural owner may
add a column directly (others suggest). The placeholder leaves room for future
policies (e.g. `any-column-owner`) without re-opening the capability contract.

**Rationale.** The sheet owner co-designs the schema with column owners; gating
column creation on the root structural owner keeps schema growth governed by a
single accountable party, while the policy field future-proofs the decision
without a data-model change.

**Implementation.** `core/acl.py::_resolve_meta_authority` (addColumn branch
reads `settings.column_creation`, defaults to `owner-only`, authority = sheet
`structural_owner`).

---

## ADR-003 — `withdrawChange` event semantics

**Status:** Accepted (locked).

**Context.** Withdrawing a Change Request is a requester-initiated close. The
event-type set is closed (DATA-MODEL §12) and there is no `CHANGE_WITHDRAWN`
type. The open question was which event a withdrawal emits.

**Decision.** `withdrawChange` keeps the **closed event set**: it emits
`CHANGE_REJECTED` with `payload.reason = "withdrawn"`. The Change Request `status`
becomes `withdrawn` (distinct from `rejected`), but the emitted Tree Event reuses
`CHANGE_REJECTED` so downstream consumers (webhooks, notifications) need no new
event type.

**Rationale.** Withdrawal and rejection are both "the proposed change will not
happen" terminal closes from a consumer's standpoint. Reusing `CHANGE_REJECTED`
(with a discriminating `reason`) avoids expanding the closed event set while the
CR `status` still records the precise lifecycle outcome for the ledger.

**Implementation.** `core/change_request.py::withdraw_change` (requester-only;
emits `CHANGE_REJECTED` with `payload.reason="withdrawn"`; sets status
`withdrawn`). Tested in
`tests/core/test_change_request.py::test_withdraw_by_requester_emits_rejected_reason_withdrawn`.

---

## ADR-004 — External system identity

**Status:** Accepted (locked).

**Context.** Arbor must serve external/headless consumers (persona EXT). The
question was whether an external system is a special principal type or a normal
user, and how webhook endpoints relate to it.

**Decision.** An external system is a **normal Frappe User + API key**, bound by
the **same two-axis ACL** as any human or the agent. It has no special
privileges: as an API writer it resolves Axis-1/Axis-2 identically and an
unauthorized write becomes a Change Request (surface parity). A **Webhook
Endpoint is independent of any User** — it is a derived consumer of the Tree
Event stream (URL + secret + scope), not a principal that performs mutations.

**Rationale.** Treating EXT as a normal user means zero new authorization code
paths — the governance keystone (ARCHITECTURE §4.2) already covers it. Decoupling
Webhook Endpoint from User reflects that outbound delivery is a subscription, not
an actor, and lets an endpoint exist without a backing login.

**Implementation.** No special-casing in `core/` — `execute_action` treats EXT's
`Actor` like any other. Webhook delivery uses `core/security.py` (HMAC) and
`core/backoff.py` (retry schedule); the Webhook Endpoint DocType (Frappe adapter,
later lane) carries no User link.
