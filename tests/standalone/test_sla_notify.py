"""SLA sweep + notify wiring (standalone) — the sink that makes SLA audible.

Regression anchor: the pure machine's notify branches gate on ``notify is not
None``; the standalone app passed ``notify=None`` so breaches were MARKED but
NOBODY was notified (and notify_on_expect was equally silent). These tests pin
the real ``_process_notifier`` against a real SQL repo.
"""

from __future__ import annotations

import os

import pytest

from arbor.core import process as process_machine
from arbor.standalone.testing import SQLTestRepository


def _notifier(repo):
    # Import lazily: the app module builds its engine at import time from
    # DATABASE_URL, so pin a throwaway sqlite first when unset.
    os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/arbor-test-app-import.db")
    from arbor.standalone.app import _process_notifier

    return _process_notifier(repo)


@pytest.fixture()
def seeded():
    repo = SQLTestRepository()
    repo.add_sheet("S", structural_owner="owner@x.com")
    repo.add_column("col:due", "S", "due", column_owner="col.owner@x.com")
    process = repo.upsert_process(
        {
            "sheet": "S",
            "title": "P",
            "rules": [
                {
                    "rule_key": "r1",
                    "trigger_kind": "row",
                    "trigger_op": "created",
                    "expected_columns": ["col:due"],
                    "within_seconds": 60,
                    "notify_on_expect": False,
                }
            ],
            "sla_breach_notify": True,
        }
    )
    repo.set_process_enabled(process, True)
    node = repo.create_node("S", None)
    run = repo.create_process_run(
        {
            "process": process,
            "sheet": "S",
            "node": node,
            "status": "active",
            "started_at": "2026-01-01T00:00:00",
            "expectations": [
                {
                    "rule_key": "r1",
                    "expected_column": "col:due",
                    "opened_at": "2026-01-01T00:00:00",
                    "due_at": "2026-01-01T00:01:00",  # long past
                }
            ],
        }
    )
    return repo, process, run


def test_sweep_with_notifier_breaches_and_notifies_column_owner(seeded):
    repo, process, run = seeded
    transitions = process_machine.sla_sweep(
        repo,
        "2026-01-02T00:00:00",
        process_of=repo.get_process_by_name,
        notify=_notifier(repo),
    )
    assert any(t["kind"] == "breached" and t["column"] == "col:due" for t in transitions)
    # The expectation is marked breached...
    stored = repo.get_process_run(process, run) or repo.list_process_runs("S")[0]
    exps = stored.get("expectations") or []
    assert any(e.get("breached") for e in exps)
    # ...and the column owner got an in-app 'sla' notification row.
    notes = [n for n in repo.notifications.values() if n.get("recipient") == "col.owner@x.com"]
    assert any(n.get("source") == "sla" for n in notes), notes


def test_sweep_is_idempotent_one_notification_total(seeded):
    repo, process, run = seeded
    notify = _notifier(repo)
    for now in ("2026-01-02T00:00:00", "2026-01-03T00:00:00"):
        process_machine.sla_sweep(
            repo, now, process_of=repo.get_process_by_name, notify=notify
        )
    notes = [
        n
        for n in repo.notifications.values()
        if n.get("recipient") == "col.owner@x.com" and n.get("source") == "sla"
    ]
    assert len(notes) == 1, notes
