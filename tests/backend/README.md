# Backend integration lane (`tests/backend`)

**Status: NEEDS A FRAPPE BENCH + SITE.** Every module here is marked
`@pytest.mark.bench` and is auto-skipped when `frappe` is not importable (see
`conftest.py`), so a bench-free checkout can still run `pytest` over the repo.

These tests prove the **governance keystone** once, at the backend integration
layer, against the ONE canonical seed (`arbor.adapter.seed.seed_canonical_sheet`,
which mirrors `tests/fixtures/canonical.py` field-for-field — enforced by
`tests/adapter/test_seed_parity.py`). They bind to the real seams the build lanes
shipped and re-implement NO governance logic:

| File | Surface under test | Source catalog |
|---|---|---|
| `test_permissions_acl.py` | two-axis ACL: structural ancestor-walk, delegation routing, **nearest-grant-wins**, axis independence, moveNode dual-end + co-approver, delegation lifecycle, owner-self policy | `permissions-and-delegation.md` §1–§5, §8 |
| `test_change_request_lifecycle.py` | `suggest → proposed → approve/reject/withdraw` state machine, replay AS the resolved approver, role separation, idempotency/terminal guards, **moveNode dual-approval**, event-log ordering/append-only, decision-time re-resolution | `change-request-lifecycle.md` A–M (+ permissions §7) |
| `test_notifications_ack.py` | dispatcher fan-out + scope matching (sheet/branch-NestedSet/column), `event_types` filter, multi-channel, CR linkage, requires_ack + **acknowledge ledger**, **accountability "N notified / M acked"** | `notifications-and-ack.md` A–G |

Shared bench-side helpers live in `_helpers.py` (persona login, the seed,
Tree-Event / CR / cell / notification / ack queries, the `accountability`
aggregate). No ACL/registry/executor/dispatcher logic is duplicated — helpers only
*invoke* the shipped code and *query* the resulting rows.

## Running

```bash
# via bench (transactional rollback per test, site context)
tests/backend/run_backend_tests.sh <site>

# or inside a bench-activated venv
PYTEST=1 tests/backend/run_backend_tests.sh
pytest tests/backend -m bench

# bench-free: cleanly skipped (no frappe)
pytest tests/backend            # -> 3 skipped
```

## Dispatcher wiring (hook-independent)

The notification dispatcher reacts to new `Tree Event` rows via a
`doc_events["Tree Event"]["after_insert"]` hook
(`arbor.dispatch.frappe_dispatch.on_tree_event_insert`). To avoid depending on
whether the integrator has assembled that hook into `hooks.py` yet, the
notification tests drive the dispatcher explicitly through
`_helpers.dispatch_pending_events(sheet)`. Dispatch is idempotent per
`(tree_event, recipient, channel)`, so this is safe even when a real hook is also
active. **The integrator must still wire the `doc_events` entry** (declared in
this lane's returned manifest) for production fan-out.

## Decision-time re-resolution (known gap)

ARCHITECTURE §5 / PERMISSIONS §1 require `approveChange` to **re-resolve** the
approver at decision time when grants/columns changed since proposal. The shipped
core (`arbor.core.change_request.approve_change`) currently approves against the
CR's *stored* `resolved_approver` and does not recompute. The three re-resolution
cases (`CHANGE_REQUEST_LIFECYCLE-053/054/055`, `PERMISSIONS_AND_DELEGATION-054`)
are written exactly to the contract and bound to the real endpoints, but marked
`@pytest.mark.xfail(strict=False)`: they pass automatically once the core adds
re-resolution, and flag (not error) until then.
