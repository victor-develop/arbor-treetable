# Arbor — Delivery Report

> Governed, API-first, agent-native **Tree Table** SaaS. Built on Frappe (backend)
> + standalone React (frontend), with an isolated employee SSO module. This report
> records what was built, how it was verified, the bugs found while verifying, and
> what remains.

Repo: https://github.com/victor-develop/arbor

---

## 1. What the product solves

Fine-grained **governance over hierarchical data** that a flat Google Sheet cannot:

- **Two orthogonal ownership axes** — *structural* (add/move/delete rows, subtree-scoped, **delegable**) and *column* (edit cell values, field-scoped). A cell's existence is governed by its branch owner; its value by its column owner; the axes resolve independently.
- **Suggest → approve** — any action a caller isn't authorized for becomes a **Change Request** routed to the resolved approver (the governance keystone). The same `execute_action` path either mutates or files a CR — for humans, the API, and the agent identically.
- **Subscriptions + acknowledgement ledger** — "N notified / M acked" accountability; sensitive watchers get every proposed/approved change.
- **Webhooks** — external systems subscribe to the same Tree Event stream (HMAC-signed, retried).
- **API as a first-class peer to the Web UI** — every capability is a documented REST method.
- **Server-side Re-Act agent** — runs under its own identity, subject to the same ACL, so unauthorized agent actions become Change Requests (no privileged bypass).

See `docs/ARCHITECTURE.md` (+ DATA-MODEL, CAPABILITIES, PERMISSIONS) for the canonical spec, and `tests/TEST-PLAN.md` for the 506-case catalog.

---

## 2. Architecture (ports & adapters)

- `arbor/core/` — **pure domain core, zero Frappe imports**: capability registry, ACL resolver, centralized executor, CR state machine, snapshot serializer, HMAC, backoff, Re-Act loop. Unit-testable without a bench.
- `arbor/arbor/` — **Frappe adapter**: 14 DocTypes, `FrappeRepository`/`FrappeEventSink`, 20 whitelisted capability methods, notification + webhook dispatchers, LiteLLM agent.
- `frontend/` — **standalone Vite + React** thin shell over the capability API (snapshot-driven; edit-vs-suggest from ACL hints).
- `arbor_sso_overlay/` — **isolated** employee-SSO module (`@your-org/employee-sso-sdk`); the open-source core never imports it.

One registry, one executor, one ACL resolver, one event emitter, one snapshot serializer, one dispatcher fan-out — no surface re-implements governance.

---

## 3. Verification — actually run, on a real stack

Environment: macOS arm64 · Python 3.12 (uv) · **Frappe v15** · **MariaDB 12.3** · **Redis** · Node 24 · Playwright/Chromium. The Frappe app was installed into a real bench (`bench get-app` + `new-site arbor.test` + `install-app`), data seeded, and the full suite executed.

| Tier | Tooling | Result |
|---|---|---|
| **Pure domain core** (bench-free) | pytest | **261 passed, 4 skipped** |
| **All bench-free lanes** (core/dispatch/webhooks/api/agent/auth/adapter/doctype) | pytest | **0 failed** |
| **Frontend components** | Vitest + RTL | **92 passed** (16 files) |
| **Backend integration** (real Frappe + MariaDB + Redis) | pytest (`-m bench`) | **116 passed, 2 skipped, 2 xfail, 1 xpass, 0 failed** |
| **Browser e2e** | Playwright (real frontend ↔ real backend) | **6 passed** of the runnable set (see §5) |

The 2 `xfail` are pre-flagged product gaps (Change-Request re-resolution at decision time, `tests/TEST-PLAN.md` §5.6).

---

## 4. Bugs found *because* we ran it end-to-end (~18, all fixed)

Real defects the integration/e2e runs surfaced that bench-free mocks had hidden:

1. `Tree Node.validate()` chained `super().validate()` — NestedSet/Document have no such hook (every node insert crashed).
2. Cell values stored raw into a MariaDB **JSON** column → `json_valid` CHECK failures (must store JSON-encoded).
3. `Tree Event` append-only guard fired on the **initial insert** (`is_new()` is already False in `on_update`; gate on `flags.in_insert`).
4. Repository writes missing `ignore_permissions` — Frappe's role ACL blocked the governed writes (Arbor's ACL is the authority).
5. Sheet-less capabilities (`revokeDelegation`) `KeyError`'d on `params["sheet"]` — executor now resolves the sheet from the target.
6. Persona identity **case-sensitivity** — Frappe lowercases User emails; ACL compared against mixed-case ids.
7. `Subscription.event_types` (text column) rejected a raw Python list (encode JSON).
8. `Subscription.target` Dynamic Link needed `target_doctype` set per scope.
9. `Change Request.approvals` is a **child table** — adapter must round-trip user ids ↔ child rows.
10. `acknowledge` wasn't idempotent (duplicate ack raised instead of no-op).
11. Schema validator rejected `None` for optional params (`subscribe.subscriber` default).
12. `subscribe` didn't default `subscriber` to the actor on an explicit `None`.
13. **Branch-scoped matching of deleted nodes** — a `NODE_DELETED` event can't look up the gone node's range; now matched via ancestor ids captured at emit time.
14. Notification **back-fill** — a subscription created after an event was retroactively notified under batch dispatch (added a creation-time guard).
15. `CHANGE_PROPOSED`/`CHANGE_APPROVED` events lacked the target node/column → branch/column subscribers weren't matched (now carry the location).
16. Column **editors** couldn't approve a column CR; single-approver CRs now complete on one approval.
17. `internalReset` was authorized for everyone (axis resolver has no role concept) — now gated to admin / SYSTEM actor; unknown links return 404.
18. **Frontend didn't unwrap Frappe's `{message: …}` envelope** — the React app crashed against the real backend (vitest mocks returned bare values). This is the headline e2e find.
19. **Drag-to-reparent into a leaf** was impossible (`TreeRow` only allowed an "inside" drop on nodes that already had children).
20. **Executed moves didn't re-render** — the move had no optimistic value and didn't refetch, so the restructured tree stayed visually stale.
21. **Sibling order wasn't user-controllable** — Frappe NestedSet orders siblings by name; reorder now lives in `idx`, carried through the snapshot and honored by the frontend tree builder.
22. **StrictMode double-mount** fired the mount refetch twice; under fast interaction the second landed mid-edit and clobbered the optimistic commit (dev-only; removed the wrapper).

---

## 5. Browser e2e — ALL GREEN

**The full 12-spec Playwright suite passes: 12 passed / 0 failed / 0 flaky** (Vite ↔ real Frappe, per-persona API keys, real z.ai LLM). Live screenshot: `docs/evidence/arbor-web-ui.png`.

| Spec | Cases | Status |
|---|---|---|
| tree expand/collapse | WEB_UI-004/005/048 | ✅ |
| inline edit (executed + suggested w/ approver) | WEB_UI-011/014 | ✅ |
| drag re-parent / sibling reorder / suggested+co-approver | WEB_UI-036/038/041 | ✅ |
| import/export round-trip (+IMPORT_COMPLETED) | WEB_UI-082 | ✅ |
| subscribe / unsubscribe | WEB_UI-091/092 | ✅ |
| **AI agent — executed + Change-Request (real z.ai GLM)** | WEB_UI-065/066 | ✅ |

Every previously-open gap is now closed (see §6). The earlier "flakiness" was a test
artifact (one-shot DOM reads racing the async refetch + tight timeouts for the
multi-call real-LLM loop + a transient reset deadlock), not product correctness —
fixed with polling assertions, generous agent timeouts, and a deadlock-retry on the
reset endpoint.

**Bottom line:** all known bugs are fixed. Deterministic tiers (core 261 + bench-free
lanes + frontend 92 + **bench integration 121, 0 xfail**) and the **full browser e2e
(12/12)** are green, including the real-LLM agent end-to-end.

## 6. Known-bug fixes (this pass)

- **WEBHOOKS-044** — Webhook Endpoint controller now rejects event types outside the closed set (+ bench test).
- **CR decision-time re-resolution** — `approve`/`reject` re-resolve the approver from current grants/ownership (revoked grant → ancestor owner; nearer grant wins; removed editor drops out). Closes CHANGE_REQUEST_LIFECYCLE-053/054/055 + PERMISSIONS-054 (xfail markers removed → now real passes; bench suite 116+2xfail → **121 green**).
- **Subscribe/unsubscribe Web UI** — snapshot now emits `viewer{can_add_column,subscribed,subscription}`; `SubscriptionControl` mounted; WEB_UI-091/092 green.
- **Import/export** — export triggers a real download; import replays a governed plan with node-id remapping + field-keyed values + completion banner; WEB_UI-082 green.
- **Full-suite stability** — polling assertions for post-move re-render, agent-loop timeouts, and a reset-endpoint deadlock retry → 12/12 e2e, 0 flaky.

### Agent / LLM integration (§4 cont.)
23. `agent.chat` never injected the active sheet into the system prompt → the LLM couldn't know which sheet to act on.
24. The Re-Act loop re-sent prior `tool_calls` in core shape; the OpenAI/LiteLLM wire format needs `{id,type:"function",function:{…}}` or the provider rejects the follow-up turn ("Tool type cannot be empty").
25. The frontend read `agent.chat` as an NDJSON stream, but the Frappe endpoint returns one batch JSON — now replays the transcript as frames; empty finals fall back to a summary (an empty node is invisible).
26. The snapshot serializer dropped column `options`, so select-split cells couldn't render segments; the seed also wasn't forwarding `options` to `create_column`.
27. `SelectSplitCell` only accepted array values; a single-select scalar ("done") never marked its segment selected.

---

## 6. How to run

See `RUNNING.md` for full setup. Quick reference:

```bash
# bench-free (no Frappe needed)
python -m pytest tests/core            # pure core
cd frontend && npm test                # vitest

# backend integration (needs a Frappe bench + site arbor.test)
ARBOR_TEST_SITE=arbor.test ARBOR_SITES_PATH=sites \
  <bench>/env/bin/python -m pytest apps/arbor/tests -m bench

# browser e2e (frontend on :5173 proxying /api to Frappe on :8000, seeded sheet S,
# per-persona ARBOR_E2E_<P>_KEY API keys)
node_modules/.bin/playwright test --config tests/e2e/playwright.config.ts
```

SSO: the open-source build runs without `arbor_sso_overlay`; a private deployment injects it to override the auth provider.
