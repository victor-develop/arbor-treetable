"""The ONE subscription/endpoint matcher shared by BOTH dispatchers
(ARCHITECTURE §6/§7; WEBHOOKS-037). DRY: there is exactly one place that decides
"does this Tree Event match this scoped selector?", and both the notification
subscription and the webhook endpoint go through it. No divergent branch logic.

Pure functions. Branch scope uses NestedSet ranges (inclusive of the branch
root, NOTIFICATIONS_AND_ACK-010b); column scope uses direct equality on
``payload.column``; sheet scope uses sheet equality.

A selector is anything exposing ``scope``, ``target`` and ``event_types`` — i.e.
both :class:`~arbor.dispatch.ports.SubscriptionView` and
:class:`~arbor.dispatch.ports.WebhookEndpointView` satisfy it.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .ports import EventView


def event_node(event: EventView) -> Optional[str]:
    """The Tree Node a node/structure event concerns, read from its payload.

    Covers every node-bearing event in the closed set (DATA-MODEL §12):
    NODE_CREATED/DELETED/MOVED carry ``node``; NODE_VALUE_UPDATED carries
    ``node``; a structural CHANGE_PROPOSED carries the target node nested under
    ``payload.action`` params. We look at the common keys.
    """
    p = event.payload or {}
    for key in ("node", "branch_root", "parent"):
        if p.get(key):
            return p[key]
    # CR-related events carry the original capability params under "params".
    params = p.get("params") or {}
    for key in ("node", "branch_root", "parent"):
        if params.get(key):
            return params[key]
    return None


def event_column(event: EventView) -> Optional[str]:
    """The Tree Column a value/schema event concerns (``payload.column``)."""
    p = event.payload or {}
    if p.get("column"):
        return p["column"]
    params = p.get("params") or {}
    return params.get("column")


def matches_event_type(selector_event_types: list[str], event_type: str) -> bool:
    """event_types filter (WEBHOOKS-043, NOTIFICATIONS_AND_ACK-013). An empty/None
    list means "all types" is NOT assumed — selectors always carry an explicit
    subset, so empty matches nothing."""
    return event_type in (selector_event_types or [])


def matches_scope(
    *,
    scope: str,
    target: str,
    event: EventView,
    node_range: Callable[[str], Optional[tuple[int, int]]],
) -> bool:
    """Scope match for one selector against one event.

    - ``sheet``  → event.sheet == target (WEBHOOKS-049 isolation).
    - ``branch`` → the event's node lies within target's NestedSet range,
      INCLUSIVE of the root (``target.lft <= node.lft AND node.rgt <= target.rgt``;
      NOTIFICATIONS_AND_ACK-010/010b/011). Evaluated against the node's CURRENT
      position so a node moved into the branch matches (NOTIFICATIONS_AND_ACK-040,
      WEBHOOKS-042). A dangling/deleted target matches nothing
      (NOTIFICATIONS_AND_ACK-041).
    - ``column`` → direct equality on the event's ``payload.column``
      (NOTIFICATIONS_AND_ACK-012, WEBHOOKS-007).
    """
    if scope == "sheet":
        return event.sheet == target

    if scope == "column":
        return event_column(event) == target

    if scope == "branch":
        node = event_node(event)
        if node is None:
            return False
        node_rng = node_range(node)
        if node_rng is None:
            # The event's node no longer exists (NODE_DELETED): its live range is
            # gone and the ancestor ranges have already shrunk, so a range compare
            # is unsound. Match by the ancestor-or-self chain the handler captured
            # at emit time — target is in-branch iff it is an ancestor-or-self.
            ancestors = (event.payload or {}).get("ancestor_ids")
            if ancestors is not None:
                return target in ancestors
            return False
        target_range = node_range(target)
        if target_range is None:
            return False  # dangling branch root — graceful no-match
        t_lft, t_rgt = target_range
        n_lft, n_rgt = node_rng
        return t_lft <= n_lft and n_rgt <= t_rgt  # inclusive of the root

    return False


def selector_matches(
    selector: Any,
    event: EventView,
    node_range: Callable[[str], Optional[tuple[int, int]]],
) -> bool:
    """True iff ``selector`` (Subscription OR Webhook Endpoint) should receive
    ``event``: its event_types include the event type AND its scope matches.
    This single predicate is what makes webhooks and notifications provably share
    one matcher (WEBHOOKS-037)."""
    if not matches_event_type(getattr(selector, "event_types", None), event.type):
        return False
    return matches_scope(
        scope=selector.scope,
        target=selector.target,
        event=event,
        node_range=node_range,
    )
