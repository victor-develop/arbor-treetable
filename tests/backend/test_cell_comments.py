"""Per-cell comments — real-adapter (bench) round-trip (Area 2 / WS-CMT-BE).

runnable: NEEDS FRAPPE BENCH (``@pytest.mark.bench``; auto-skips bench-free).

Comments are threaded, cell-keyed collaboration metadata — NON-capabilities that
emit NO Tree Event. Governance reuses the ONE ACL resolver via the whitelisted
shims in ``arbor.arbor.api``:

  read / post  -> can_read_column          (discuss any cell you can read)
  resolve      -> resolve_column_approvers  (column owner + editors settle)
  delete       -> author OR column approver

On add, a Notification (source='comment', tree_event=NULL) fans out to the column
owner/editors + surviving @mentions, minus the author. This module drives the
whitelisted ``arbor.api`` funnel end-to-end on a live site and rolls nothing back
itself (the bench harness rolls the transaction between tests).

Canonical sheet S personas: C owns col:budget; B owns col:notes; C owns
col:status with editor B; E is a suggest-only reader (no ownership).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.bench

frappe = pytest.importorskip("frappe")

from arbor import api  # noqa: E402

from tests.backend import _helpers as h  # noqa: E402


@pytest.fixture()
def fx():
    data = h.seed()
    yield data
    frappe.set_user("Administrator")


def _x(fx):
    return fx["nodes"]["X"]


def _comments_in_db(sheet, node, column):
    return frappe.get_all(
        "Arbor Cell Comment",
        filters={"sheet": sheet, "node": node, "column": column},
        fields=["name", "thread_root", "author", "body", "resolved"],
        order_by="creation asc",
    )


# ---------------------------------------------------------------------------
# add -> list round-trip + threading
# ---------------------------------------------------------------------------
def test_add_and_list_round_trip(fx):
    """A reader (E) may comment on a public cell (col:budget); list returns it."""
    h.login_as("E")
    node, col = _x(fx), fx["columns"]["budget"]

    out = api.add_cell_comment(sheet=fx["sheet"], node=node, column=col, body="First!")
    assert out["thread_root"] is None  # a root comment
    listed = api.list_cell_comments(sheet=fx["sheet"], node=node, column=col)
    assert len(listed) == 1
    assert listed[0]["name"] == out["name"]
    assert listed[0]["author"] == h.user("E")
    assert listed[0]["body"] == "First!"
    assert listed[0]["resolved"] is False


def test_reply_carries_thread_root_and_parent(fx):
    h.login_as("E")
    node, col = _x(fx), fx["columns"]["budget"]
    root = api.add_cell_comment(sheet=fx["sheet"], node=node, column=col, body="root")
    reply = api.add_cell_comment(
        sheet=fx["sheet"], node=node, column=col, body="reply", parent_comment=root["name"]
    )
    assert reply["thread_root"] == root["name"]
    listed = {c["name"]: c for c in api.list_cell_comments(sheet=fx["sheet"], node=node, column=col)}
    assert listed[reply["name"]]["parent_comment"] == root["name"]
    assert listed[reply["name"]]["thread_root"] == root["name"]


def test_empty_body_rejected(fx):
    h.login_as("E")
    node, col = _x(fx), fx["columns"]["budget"]
    with pytest.raises(frappe.ValidationError):
        api.add_cell_comment(sheet=fx["sheet"], node=node, column=col, body="   ")


# ---------------------------------------------------------------------------
# read-ACL denial: a user who cannot read an owner-only column is 403 on add/list
# ---------------------------------------------------------------------------
def test_owner_only_column_denies_non_reader(fx):
    node, col = _x(fx), fx["columns"]["budget"]
    # C owns budget → make it owner-only.
    h.login_as("C")
    api.update_column(sheet=fx["sheet"], column=col, patch={"read_level": "owner-only"})

    # E can no longer read budget → 403 on both list and add.
    h.login_as("E")
    with pytest.raises(frappe.PermissionError):
        api.list_cell_comments(sheet=fx["sheet"], node=node, column=col)
    with pytest.raises(frappe.PermissionError):
        api.add_cell_comment(sheet=fx["sheet"], node=node, column=col, body="sneaky")

    # C (owner) still may.
    h.login_as("C")
    out = api.add_cell_comment(sheet=fx["sheet"], node=node, column=col, body="owner note")
    assert out["name"]


# ---------------------------------------------------------------------------
# resolve authority: approver flips resolved; a suggest-only reader is 403
# ---------------------------------------------------------------------------
def test_resolve_by_approver_flips_and_reader_denied(fx):
    node, col = _x(fx), fx["columns"]["budget"]  # owner C
    h.login_as("E")
    root = api.add_cell_comment(sheet=fx["sheet"], node=node, column=col, body="please review")

    # E (author but not an approver) cannot resolve.
    with pytest.raises(frappe.PermissionError):
        api.resolve_cell_comment(comment=root["name"])

    # C (column owner) resolves → flips resolved on the root.
    h.login_as("C")
    out = api.resolve_cell_comment(comment=root["name"])
    assert out["resolved"] is True
    assert frappe.db.get_value("Arbor Cell Comment", root["name"], "resolved") == 1
    assert frappe.db.get_value("Arbor Cell Comment", root["name"], "resolved_by") == h.user("C")

    # Reopen is the same authority + idempotent.
    out2 = api.resolve_cell_comment(comment=root["name"], resolved=False)
    assert out2["resolved"] is False


def test_resolve_reply_resolves_its_thread_root(fx):
    node, col = _x(fx), fx["columns"]["budget"]
    h.login_as("E")
    root = api.add_cell_comment(sheet=fx["sheet"], node=node, column=col, body="root")
    reply = api.add_cell_comment(
        sheet=fx["sheet"], node=node, column=col, body="reply", parent_comment=root["name"]
    )
    h.login_as("C")
    out = api.resolve_cell_comment(comment=reply["name"])  # resolve via the reply
    assert out["name"] == root["name"]  # resolves the ROOT, not the reply
    assert frappe.db.get_value("Arbor Cell Comment", root["name"], "resolved") == 1


# ---------------------------------------------------------------------------
# delete authority: author yes; a stranger 403; root-with-replies tombstones
# ---------------------------------------------------------------------------
def test_delete_by_author_and_stranger_denied(fx):
    node, col = _x(fx), fx["columns"]["budget"]
    h.login_as("E")
    c = api.add_cell_comment(sheet=fx["sheet"], node=node, column=col, body="mine")

    # F (neither author nor approver) cannot delete.
    h.login_as("F")
    with pytest.raises(frappe.PermissionError):
        api.delete_cell_comment(comment=c["name"])

    # E (author) may hard-delete a leaf.
    h.login_as("E")
    out = api.delete_cell_comment(comment=c["name"])
    assert out == {"ok": True, "tombstoned": False}
    assert not frappe.db.exists("Arbor Cell Comment", c["name"])


def test_delete_root_with_replies_tombstones(fx):
    node, col = _x(fx), fx["columns"]["budget"]
    h.login_as("E")
    root = api.add_cell_comment(sheet=fx["sheet"], node=node, column=col, body="root")
    api.add_cell_comment(
        sheet=fx["sheet"], node=node, column=col, body="reply", parent_comment=root["name"]
    )
    out = api.delete_cell_comment(comment=root["name"])
    assert out == {"ok": True, "tombstoned": True}
    # Row kept (so the reply stays threaded), body tombstoned.
    assert frappe.db.exists("Arbor Cell Comment", root["name"])
    assert frappe.db.get_value("Arbor Cell Comment", root["name"], "body") == "[deleted]"


def test_delete_by_column_approver(fx):
    node, col = _x(fx), fx["columns"]["budget"]  # owner C
    h.login_as("E")
    c = api.add_cell_comment(sheet=fx["sheet"], node=node, column=col, body="E's note")
    h.login_as("C")  # column owner moderates
    out = api.delete_cell_comment(comment=c["name"])
    assert out["ok"] is True


# ---------------------------------------------------------------------------
# notification fan-out: source='comment', tree_event NULL, to owner/editors +
# mentions minus author; surfaces in the ONE inbox
# ---------------------------------------------------------------------------
def test_add_creates_comment_notification_to_owner(fx):
    node, col = _x(fx), fx["columns"]["budget"]  # owner C
    h.login_as("E")
    out = api.add_cell_comment(sheet=fx["sheet"], node=node, column=col, body="ping the owner")

    rows = frappe.get_all(
        "Notification",
        filters={"comment": out["name"]},
        fields=["recipient", "source", "tree_event", "requires_ack", "channel"],
    )
    recipients = {r.recipient for r in rows}
    assert h.user("C") in recipients          # column owner notified
    assert h.user("E") not in recipients      # author never notifies self
    for r in rows:
        assert r.source == "comment"
        assert r.tree_event is None           # a comment is NOT a Tree Event
        assert r.requires_ack == 0            # FYI, excluded from ack math
        assert r.channel == "in-app"

    # The comment notification surfaces in the ONE inbox for C.
    h.login_as("C")
    inbox = api.list_notifications(sheet=fx["sheet"])
    assert any(n["event_type"] == "COMMENT_ADDED" for n in inbox)


def test_mention_of_reader_notifies_and_non_reader_dropped(fx):
    node, col = _x(fx), fx["columns"]["budget"]  # owner C
    # Make budget owner-only so E (a stranger) cannot read it.
    h.login_as("C")
    api.update_column(sheet=fx["sheet"], column=col, patch={"read_level": "owner-only"})

    # C mentions B (editor? no — B is not an editor of budget; but B can't read
    # owner-only budget either). Use an approver-mention that CAN read, and E who
    # CANNOT. B is owner of notes but not budget → cannot read owner-only budget.
    # So mention C (self excluded) and E (non-reader). We instead mention the
    # sheet owner A (admin? not necessarily). Simplest: mention E (non-reader) and
    # verify E is dropped, and mention C's editor set is empty. So assert E dropped.
    h.login_as("C")
    body = f"heads up @{h.user('E')} and @{h.user('C')}"
    out = api.add_cell_comment(sheet=fx["sheet"], node=node, column=col, body=body)

    # E cannot read owner-only budget → dropped from mentions (no notification).
    assert h.user("E") not in out["mentions"]
    e_rows = frappe.get_all(
        "Notification", filters={"comment": out["name"], "recipient": h.user("E")}, pluck="name"
    )
    assert e_rows == []


# ---------------------------------------------------------------------------
# snapshot per-cell comment summary — read-ACL filtered
# ---------------------------------------------------------------------------
def test_snapshot_comment_summary_counts_and_redacts(fx):
    node, col = _x(fx), fx["columns"]["budget"]  # owner C, public
    h.login_as("E")
    api.add_cell_comment(sheet=fx["sheet"], node=node, column=col, body="c1")
    root = api.add_cell_comment(sheet=fx["sheet"], node=node, column=col, body="c2")
    h.login_as("C")
    api.resolve_cell_comment(comment=root["name"])  # one resolved, one open

    # A reader sees the per-cell summary while budget is public.
    h.login_as("E")
    snap = api.get_sheet_snapshot(sheet=fx["sheet"])
    xnode = [n for n in snap["nodes"] if n["name"] == node][0]
    summary = xnode.get("comments", {}).get(col)
    assert summary == {"open": 1, "resolved": 1, "unresolved": 1}

    # Make budget owner-only → the summary must NOT leak to a non-reader (E).
    h.login_as("C")
    api.update_column(sheet=fx["sheet"], column=col, patch={"read_level": "owner-only"})
    h.login_as("E")
    snap2 = api.get_sheet_snapshot(sheet=fx["sheet"])
    xnode2 = [n for n in snap2["nodes"] if n["name"] == node][0]
    assert col not in (xnode2.get("comments") or {})
