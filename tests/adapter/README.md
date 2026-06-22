# Adapter lane tests

Tests for the Frappe ADAPTER (`arbor.arbor.adapter` + `arbor.arbor.api`).

Two groups:

- **`test_seed_parity.py`** — BENCH-FREE. Asserts the Frappe-side canonical seed
  spec (`arbor.adapter.seed`) matches the pure fixture
  (`tests/fixtures/canonical.py`) field-for-field, so the two seeds can never
  silently diverge (the DRY guarantee for the canonical fixture). It imports
  the seed module's *spec constants* only; it `importorskip`s `frappe` because
  the module imports it at top level. Where frappe is absent, the spec is
  re-derived and compared structurally without importing the module.

- **`test_api_integration.py`** — **REQUIRES A FRAPPE BENCH + SITE** (marked
  `@pytest.mark.bench`). Exercises the whitelisted REST funnel end-to-end
  against `FrappeRepository`/`FrappeEventSink`: surface-parity (API-010/011),
  the suggest path (200 not 403), control-denial 403, 404/409 error contracts,
  and `get_sheet_snapshot` shape + ACL hints. These are skipped automatically
  when `frappe` is not importable.

Run bench-free only:

    pytest tests/adapter -m "not bench"

Run the full adapter suite on a bench:

    bench --site <site> run-tests --module tests.adapter.test_api_integration
