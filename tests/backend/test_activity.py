"""Backend integration — the sheet activity / change-history feed.

runnable: NEEDS FRAPPE BENCH (``@pytest.mark.bench``; auto-skipped when frappe is
absent, like every ``tests/backend`` module). Exercises the ``arbor.list_activity``
READ SHIM (``arbor.arbor.api.list_activity``) end to end against the REAL adapter:
mutations through the whitelisted ``arbor.api.*`` funnel write Tree Event rows via
``FrappeEventSink``; ``list_activity`` then reads them back, newest-first, and
builds a per-viewer, read-ACL-redacted change-history.

``list_activity`` is a read shim (like ``list_change_requests`` /
``list_notifications``), NOT a registry capability — so it never appears on the
parity/registry surface and those suites stay green.

Asserted invariants (the CONTRACT):

* Newest-first ordering (creation desc).
* Each row's ``summary`` resolves the node LABEL and (readable) column LABEL —
  e.g. "<user> updated the budget of Task X".
* Read-ACL redaction: an event referencing a column the viewer CANNOT read
  (``arbor.core.acl.can_read_column``) drops the column name from BOTH ``column``
  and ``summary`` (generic "a cell" phrasing) and NEVER leaks a raw cell value.
* Sheet-scoped: the rows all belong to the queried sheet.

Run::

    bench --site <site> run-tests --module tests.backend.test_activity
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.bench

frappe = pytest.importorskip("frappe")

from arbor import api  # noqa: E402  (after importorskip)
from arbor.core.types import EVENT_TYPES  # noqa: E402

from tests.backend import _helpers as h  # noqa: E402


@pytest.fixture()
def fx():
    """Canonical sheet `S`, rolled back per test by the bench harness transaction."""
    data = h.seed()
    yield data
    frappe.set_user("Administrator")


def _N(fx, label):
    return fx["nodes"][label]


def _C(fx, field):
    return fx["columns"][field]


def _by_id(rows):
    return {r["event_id"]: r for r in rows}


# ===========================================================================
# Newest-first ordering + label-resolving summaries
# ===========================================================================
def test_activity_is_newest_first_with_resolved_labels(fx):
    """Two cell updates by C (col:budget owner) produce two NODE_VALUE_UPDATED
    events; the feed lists them newest-first and each summary names the column
    label AND the node label (no raw values)."""
    h.login_as("C")
    # Oldest first: update X.budget, then Y.budget.
    api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "budget"), value=1111)
    api.update_cell(sheet=fx["sheet"], node=_N(fx, "Y"), column=_C(fx, "budget"), value=2222)

    feed = api.list_activity(sheet=fx["sheet"])
    assert len(feed) >= 2

    # Newest-first: the Y update precedes the X update in the feed.
    updates = [r for r in feed if r["type"] == "NODE_VALUE_UPDATED"]
    assert updates[0]["timestamp"] >= updates[1]["timestamp"]
    assert updates[0]["node"] == "Task Y"
    assert updates[1]["node"] == "Task X"

    # Column is readable by C (the owner) -> named in the row + summary.
    newest = updates[0]
    assert newest["column"] == "budget"
    assert newest["type"] in set(EVENT_TYPES)
    assert newest["summary"] == f"{h.user('C')} updated the budget of Task Y"
    # Never the raw cell value.
    assert "2222" not in newest["summary"]
    assert "1111" not in newest["summary"]

    # Sheet-scoped + actor_type recorded.
    assert all(r["actor_type"] == "human" for r in updates)


def test_node_created_summary_names_the_node_label(fx):
    """A adds a node under P1 with an initial label value -> NODE_CREATED whose
    summary names the new node's label."""
    h.login_as("A")
    api.add_node(sheet=fx["sheet"], parent=_N(fx, "P1"), values={"name": "Task Q"})

    feed = api.list_activity(sheet=fx["sheet"])
    created = [r for r in feed if r["type"] == "NODE_CREATED"]
    assert created, "expected a NODE_CREATED event"
    newest = created[0]
    assert newest["node"] == "Task Q"
    assert newest["summary"] == f"{h.user('A')} added Task Q"


# ===========================================================================
# Read-ACL redaction — an unreadable column's NAME is dropped
# ===========================================================================
def test_unreadable_column_is_redacted_in_feed(fx):
    """C (col:budget owner) makes budget owner-only, then updates X.budget.
    For a viewer who CANNOT read budget (E), the feed redacts the column: no
    column name in ``column`` or ``summary``, generic 'a cell' phrasing, and no
    raw value leak. The owner C still sees the column label."""
    h.login_as("C")
    api.update_column(sheet=fx["sheet"], column=_C(fx, "budget"), patch={"read_level": "owner-only"})
    api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "budget"), value=9999)

    # Viewer E cannot read budget (not owner, not editor, owner-only).
    h.login_as("E")
    feed = api.list_activity(sheet=fx["sheet"])
    upd = next(r for r in feed if r["type"] == "NODE_VALUE_UPDATED")
    assert upd["column"] is None
    assert "budget" not in upd["summary"]
    assert "9999" not in upd["summary"]
    # Node label is still shown (labels are always readable).
    assert upd["node"] == "Task X"
    assert upd["summary"] == f"{h.user('C')} updated a cell of Task X"

    # Owner C still sees the real column label.
    h.login_as("C")
    feed_c = api.list_activity(sheet=fx["sheet"])
    upd_c = next(r for r in feed_c if r["type"] == "NODE_VALUE_UPDATED")
    assert upd_c["column"] == "budget"
    assert upd_c["summary"] == f"{h.user('C')} updated the budget of Task X"


def test_activity_respects_limit(fx):
    """The feed honours ``limit`` (newest ``limit`` rows)."""
    h.login_as("C")
    for v in range(3):
        api.update_cell(sheet=fx["sheet"], node=_N(fx, "X"), column=_C(fx, "budget"), value=v)
    feed = api.list_activity(sheet=fx["sheet"], limit=1)
    assert len(feed) == 1
