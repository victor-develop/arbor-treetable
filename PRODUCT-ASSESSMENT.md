# Arbor — Product Readiness Assessment (PM view)

> Question: *at this level, keep going — or is it already a working product?*
> Short answer: **It is a working product at MVP / internal-pilot grade. Stop
> open-ended building; the next work should be goal-driven, not "keep improving".**

## 1. What "working product" means here

Arbor's thesis: *governed, hierarchical, API-first, agent-native tree tables* —
the thing a flat Google Sheet structurally cannot be. A working product must
prove the **differentiated core** works end-to-end on real infrastructure, for
real consumers (human, API, webhook, agent). Judge against that, not against
"a finished commercial SaaS".

## 2. Readiness scorecard

| Dimension | Status | Evidence |
|---|---|---|
| **Problem–solution fit (the moat)** | ✅ Strong | Two orthogonal ownership axes (delegable structural + column), suggest→approve, ack ledger, webhooks, API-first, server-side agent — all implemented and exercised |
| **Core functionally complete** | ✅ Yes | 19 capabilities through one DRY `execute_action`; 13 DocTypes; CR lifecycle; notifications/ack; webhooks |
| **Proven on real infra** | ✅ Yes | Real Frappe v15 + MariaDB + Redis; app installs; **116 integration tests, 0 fail** |
| **Deterministic test depth** | ✅ Strong | 261 pure-core + 92 frontend + 116 integration = **469 green**; ~27 real bugs found & fixed via integration/e2e |
| **Web UI in a real browser** | ✅ Yes (e2e) | tree, inline edit (direct vs suggest), drag re-parent/reorder, agent sidebar |
| **API as a first-class peer** | ✅ Yes | every capability a REST method; cross-surface parity asserted |
| **Agent with a real LLM** | ✅ Yes | server-side Re-Act loop on z.ai/GLM via LiteLLM; both agent e2e specs green |
| **Open-source readiness** | ✅ Mostly | SSO isolated behind an `AuthProvider` seam; core is standalone-installable |
| **Real SSO implementation** | ⚠️ Seam only | the employee SSO provider module is a documented boundary, not a built+tested integration |
| **Deploy / packaging / CI** | ❌ Not yet | no Dockerfile, no CI pipeline, no migration/release runbook |
| **Ops hardening** | ❌ Not yet | no rate limiting, observability, webhook-delivery worker at scale, large-tree perf validation |
| **Onboarding / admin UX** | ⚠️ Thin | functional shell; no polished admin flows, empty states, or first-run experience |
| **Residual e2e gaps** | ⚠️ Minor | `dnd-038` full-suite timing flake (passes isolated); import/export round-trip (undiagnosed); unsubscribe control unmounted (tracked) |

## 3. PM verdict

**Yes — it is a working product.** The differentiated value proposition is
implemented and proven end-to-end across all four consumers (web, API, webhook,
agent) on real infrastructure, with deep deterministic test coverage. It is
**demo-ready and internal-pilot-ready today.**

It is **not** a production-hardened, market-ready commercial SaaS — and it
shouldn't pretend to be. The gaps above (deploy/CI, ops hardening, real SSO,
onboarding) are the difference between "the concept works" and "an org can run
this in production unattended."

## 4. Should we continue? — goal-conditional

A professional PM does not answer "keep improving forever." The answer depends
on the **goal**, which has not been set:

- **Goal = validate the architecture / prove the concept** → **DONE. Stop.**
  Ship it as a reference implementation / internal demo. Further open-ended
  iteration is low-ROI.
- **Goal = internal pilot with one real team** → ~1 short milestone: real SSO
  wire-up, a deploy runbook, seed/import of their real data, fix the 3 e2e gaps.
- **Goal = public open-source launch** → add Docker + CI + CONTRIBUTING polish +
  a docs site + the import/export + unsubscribe gaps; harden webhook delivery.
- **Goal = commercial SaaS** → all of the above **plus** multi-tenancy story,
  billing, observability, rate limiting, SLA-grade webhook delivery, security
  review, and — most importantly — **design partners and usage validation**.

## 5. Recommendation

**Stop the build-everything loop.** We have crossed the "working product"
threshold; continued undirected coding adds cost without de-risking anything
that matters. The highest-value next step is **not more code — it's a scope
decision**: pick the goal above, and I'll execute that specific, bounded
milestone. Absent a goal, the right PM call is to **freeze scope and ship the
MVP** as-is.

— Assessed against the committed state on `main` (469 deterministic tests green;
real-browser + real-LLM e2e verified). See `DELIVERY.md` for the full record.
