# Arbor — developer task runner.
#
# The pure domain core (arbor/core) is bench-free: `make test-core` runs the
# full pure suite with plain pytest, NO Frappe site required. The Frappe app is
# the adapter; its bench-backed tests are a later lane.

.PHONY: help test-core test-frontend test lint lint-py lint-frontend install-frontend

help:
	@echo "Arbor targets:"
	@echo "  make test-core       Run the bench-free pure-core pytest suite"
	@echo "  make test-frontend   Run the Vitest frontend suite"
	@echo "  make test            Run both core and frontend suites"
	@echo "  make lint            Run ruff (python) + tsc (frontend type-check)"

# Bench-free core tests. Exact command (also see README / structured output).
test-core:
	python3 -m pytest tests/core -m core

test-frontend:
	cd frontend && npm test

test: test-core test-frontend

lint: lint-py lint-frontend

lint-py:
	ruff check arbor tests

lint-frontend:
	cd frontend && npm run lint

install-frontend:
	cd frontend && npm install
