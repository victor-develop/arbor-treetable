# ACME showcase — captured walkthrough

Screenshots of the `ACME` demo (the [DEMO-JOURNEYS.md](../../DEMO-JOURNEYS.md)
tour), captured as **admin** against the live stack by
[`tests/e2e/acme-capture.e2e.spec.ts`](../../../tests/e2e/acme-capture.e2e.spec.ts)
on the [`demo/showcase/seed.py`](../../../demo/showcase/seed.py) state.

Regenerate (public frontend on :5174, bench on :8000, ACME seeded):

```
ARBOR_E2E_BASE_URL=http://localhost:5174 ARBOR_E2E_ADMIN_KEY="<api_key:secret>" \
  npx playwright test --config tests/e2e/playwright.config.ts acme-capture
```

| # | Shot | Shows |
|---|---|---|
| 01 | `01-sheet-list.png` | Sheet-list home page (no `?sheet=`) — WIDE / ECOM / ACME |
| 02 | `02-tree-table.png`, `02-tree-table-full.png` | ACME tree + columns (J1): groups/leaves, multi-select STAGE pills |
| 03 | `03-change-requests.png` | Change Request inbox (J2): 6 pending incl. batch + dual-end move |
| 04 | `04-roles.png` | Roles (J5): pending applications, grants (incl. `role:pm`), assign form |
| 05 | `05-delegations.png` | Branch delegations (J6): the Growth → dana.demo grant |
| 06 | `06-activity.png` | Activity timeline (J13): newest-first audit feed + filters |
| 07 | `07-activity-filtered.png` | Activity filtered by type (J13) — `CHANGE_PROPOSED` only |
| 08 | `08-view-popover.png` | View column manager (J11): drag handles, checkboxes, width fields |
| 09 | `09-mobile.png` | Mobile (390×844): responsive layout |
