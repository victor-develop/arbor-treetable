"""The ONE matcher governs BOTH dispatchers (WEBHOOKS-037 DRY invariant).

A webhook endpoint and a notification subscription with identical
scope=branch/target=P2/event_types must agree on which events match — proving a
single NestedSet-range matcher serves both consumers with no divergent logic.
"""

from __future__ import annotations

from arbor.arbor.dispatch.matcher import selector_matches
from arbor.arbor.dispatch.testing import FakeEndpoint, FakeEvent, FakeSubscription

RANGES = {"P1": (2, 5), "X": (3, 4), "P2": (6, 11), "Y": (7, 8), "Z": (9, 10)}


def _range(node):
    return RANGES.get(node)


def test_endpoint_and_subscription_agree():
    """WEBHOOKS-037: identical scope/target selectors agree for Z (in) and X (out)."""
    sub = FakeSubscription("SUB", "G", "branch", "P2", ["NODE_DELETED"], "in-app")
    ep = FakeEndpoint("EP", "u", "s", ["NODE_DELETED"], "branch", "P2")
    in_branch = FakeEvent("eZ", "S", "NODE_DELETED", {"node": "Z"})
    out_branch = FakeEvent("eX", "S", "NODE_DELETED", {"node": "X"})

    assert selector_matches(sub, in_branch, _range) == selector_matches(ep, in_branch, _range) is True
    assert selector_matches(sub, out_branch, _range) == selector_matches(ep, out_branch, _range) is False


def test_event_type_filter_shared():
    """Both selectors honor the same event_types filter."""
    sub = FakeSubscription("SUB", "G", "sheet", "S", ["CHANGE_APPROVED"], "in-app")
    ep = FakeEndpoint("EP", "u", "s", ["CHANGE_APPROVED"], "sheet", "S")
    ev = FakeEvent("e", "S", "CHANGE_PROPOSED", {})
    assert selector_matches(sub, ev, _range) is False
    assert selector_matches(ep, ev, _range) is False


def test_column_scope_shared():
    sub = FakeSubscription("SUB", "C", "column", "col:budget", ["NODE_VALUE_UPDATED"], "email")
    ep = FakeEndpoint("EP", "u", "s", ["NODE_VALUE_UPDATED"], "column", "col:budget")
    ev = FakeEvent("e", "S", "NODE_VALUE_UPDATED", {"node": "Y", "column": "col:budget"})
    assert selector_matches(sub, ev, _range) is True
    assert selector_matches(ep, ev, _range) is True
