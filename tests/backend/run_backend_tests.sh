#!/usr/bin/env bash
# Backend integration suite runner — NEEDS A FRAPPE BENCH + SITE.
#
# Every module under tests/backend is marked @pytest.mark.bench and drives the
# REAL adapter (FrappeRepository / FrappeEventSink), the whitelisted REST funnel
# (arbor.api.*), and the notification dispatcher (arbor.dispatch.frappe_dispatch)
# against the canonical seed (arbor.adapter.seed.seed_canonical_sheet).
#
# These CANNOT run on a bench-free checkout: without an importable `frappe` they
# are auto-skipped by tests/backend/conftest.py.
#
# Usage:
#   tests/backend/run_backend_tests.sh <site>          # via bench (preferred)
#   PYTEST=1 tests/backend/run_backend_tests.sh        # via a bench-activated venv
set -euo pipefail

MODULES=(
  tests.backend.test_permissions_acl
  tests.backend.test_change_request_lifecycle
  tests.backend.test_notifications_ack
)

if [[ "${PYTEST:-0}" == "1" ]]; then
  # Run inside a Frappe bench's activated virtualenv (frappe importable, site set
  # via FRAPPE_SITE / site_config). pytest collects the same modules.
  exec python3 -m pytest tests/backend -m bench "$@"
fi

SITE="${1:-}"
if [[ -z "${SITE}" ]]; then
  echo "usage: $0 <site>   (or: PYTEST=1 $0)" >&2
  exit 2
fi

# Frappe's own test runner gives transactional rollback per test + site context.
for m in "${MODULES[@]}"; do
  echo "=== bench --site ${SITE} run-tests --module ${m} ==="
  bench --site "${SITE}" run-tests --module "${m}"
done
