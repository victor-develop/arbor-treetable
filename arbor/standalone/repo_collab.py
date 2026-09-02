"""CollabRepoMixin — comments + subscriptions + notifications + acks.

The collaboration slice of the standalone ``Repository``: per-cell comment
threads (Area 2), event subscriptions, in-app notification rows and the
acknowledgement ledger. Semantics mirror ``core.testing.InMemoryRepository``
(the executable reference) plus the two Frappe-side niceties the core relies
on: ``get_subscription`` derives the owning ``sheet`` from the scope target
(the executor's unsubscribe event needs it), and ack/notification creation is
idempotent exactly as the frappe adapter made it.

The mixin assumes ``self.session`` (a SQLAlchemy ``Session``) supplied by the
composing repository class; nothing here commits — transaction boundaries
belong to the request layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa

from .models import (
    Acknowledgement,
    CellComment,
    Column,
    Node,
    Notification,
    Subscription,
    utcnow,
)


# ---------------------------------------------------------------------------
# View objects (duck-typed to ports.CommentView). Defined locally: comments are
# the only collab read that returns a view object (the rest are plain dicts).
# ---------------------------------------------------------------------------
@dataclass
class CommentRowView:
    """An Arbor Cell Comment (Area 2). A complete audit record: ``author`` is the
    EFFECTIVE actor; ``real_user`` / ``impersonated_as`` carry the impersonation
    trace; ``deleted`` is the soft-delete tombstone (row preserved)."""

    name: str
    sheet: str
    node: str
    column: str
    author: str
    body: str
    thread_root: Optional[str] = None
    parent_comment: Optional[str] = None
    mentions: list[str] = field(default_factory=list)
    resolved: bool = False
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    real_user: Optional[str] = None
    impersonated_as: Optional[str] = None
    deleted: bool = False
    deleted_by: Optional[str] = None
    deleted_at: Optional[datetime] = None
    creation: Optional[datetime] = None


def _comment_view(row: CellComment) -> CommentRowView:
    return CommentRowView(
        name=row.name,
        sheet=row.sheet,
        node=row.node,
        column=row.column,
        author=row.author,
        body=row.body,
        thread_root=row.thread_root,
        parent_comment=row.parent_comment,
        mentions=list(row.mentions or []),
        resolved=bool(row.resolved),
        resolved_by=row.resolved_by,
        resolved_at=row.resolved_at,
        real_user=row.real_user,
        impersonated_as=row.impersonated_as,
        deleted=bool(row.deleted),
        deleted_by=row.deleted_by,
        deleted_at=row.deleted_at,
        creation=row.creation,
    )


class CollabRepoMixin:
    """Subscriptions / notifications / acks + per-cell comments."""

    # ---- subscriptions -----------------------------------------------------
    def create_subscription(self, data: dict[str, Any]) -> str:
        row = Subscription(
            subscriber=data["subscriber"],
            subscriber_kind=data.get("subscriber_kind", "user"),
            scope=data["scope"],
            target=data["target"],
            # [] and None both mean "all event types" to the matcher; store the
            # normalized list (parity with the frappe adapter's JSON column).
            event_types=list(data.get("event_types") or []),
            delivery=data["delivery"],
            requires_ack=bool(data.get("requires_ack")),
        )
        self.session.add(row)
        self.session.flush()
        return row.name

    def delete_subscription(self, subscription: str) -> None:
        row = self.session.get(Subscription, subscription)
        if row is not None:
            self.session.delete(row)
            self.session.flush()

    def get_subscription(self, subscription: str) -> dict[str, Any]:
        row = self.session.get(Subscription, subscription)
        if row is None:
            raise KeyError(subscription)
        # Derive the owning sheet from the scope target — the executor's
        # unsubscribe path reads ``sub["sheet"]`` for the event's sheet.
        sheet = row.target if row.scope == "sheet" else None
        if sheet is None:
            if row.scope == "branch":
                node = self.session.get(Node, row.target)
                sheet = node.sheet if node else None
            elif row.scope == "column":
                col = self.session.get(Column, row.target)
                sheet = col.sheet if col else None
        return {
            "name": row.name,
            "subscriber": row.subscriber,
            "subscriber_kind": row.subscriber_kind,
            "scope": row.scope,
            "target": row.target,
            "event_types": list(row.event_types or []),
            "delivery": row.delivery,
            "requires_ack": bool(row.requires_ack),
            "sheet": sheet,
        }

    # ---- notifications / acks ----------------------------------------------
    #: Notification keys with real columns; everything else the core attaches
    #: (``op``/``role``/``node``/...) lands in the ``extra`` JSON bag and is
    #: merged back on read, so notification dicts round-trip whole — exactly
    #: as InMemoryRepository keeps ``{"name": ..., **data}``.
    _NOTIFICATION_FIELDS = (
        "source",
        "tree_event",
        "comment",
        "change_request",
        "recipient",
        "channel",
        "requires_ack",
    )

    def create_notification(self, data: dict[str, Any]) -> str:
        """Direct in-app Notification creation (sheet-less role fan-out + the
        process notify sink). Idempotent per (tree_event, recipient, channel)."""
        channel = data.get("channel") or "in-app"
        if data.get("tree_event"):
            existing = self.session.scalar(
                sa.select(Notification.name).where(
                    Notification.tree_event == data["tree_event"],
                    Notification.recipient == data.get("recipient"),
                    Notification.channel == channel,
                )
            )
            if existing:
                return existing
        row = Notification(
            recipient=data["recipient"],
            channel=channel,
            requires_ack=bool(data.get("requires_ack")),
        )
        for k in ("source", "tree_event", "comment", "change_request"):
            if data.get(k) is not None:
                setattr(row, k, data[k])
        extra = {k: v for k, v in data.items() if k not in self._NOTIFICATION_FIELDS and k != "name"}
        if extra:
            row.extra = extra
        self.session.add(row)
        self.session.flush()
        return row.name

    def get_notification(self, notification: str) -> dict[str, Any]:
        row = self.session.get(Notification, notification)
        if row is None:
            raise KeyError(notification)
        return {
            "name": row.name,
            "source": row.source,
            "tree_event": row.tree_event,
            "comment": row.comment,
            "change_request": row.change_request,
            "recipient": row.recipient,
            "channel": row.channel,
            "requires_ack": bool(row.requires_ack),
            **(row.extra or {}),
        }

    def create_acknowledgement(self, notification: str, user: str) -> str:
        # Acknowledging is idempotent: a repeat ack by the same user returns the
        # existing row rather than duplicating the (notification, user) fact.
        existing = self.session.scalar(
            sa.select(Acknowledgement.name).where(
                Acknowledgement.notification == notification,
                Acknowledgement.user == user,
            )
        )
        if existing:
            return existing
        row = Acknowledgement(notification=notification, user=user, acked_at=utcnow())
        self.session.add(row)
        self.session.flush()
        return row.name

    # ---- per-cell comments (Area 2, promoted to capabilities) --------------
    def get_comment(self, comment: str) -> Optional[CommentRowView]:
        # Returns tombstones too (the executor authorizes delete/resolve against
        # a possibly-already-deleted row).
        row = self.session.get(CellComment, comment)
        return _comment_view(row) if row else None

    def create_comment(
        self,
        actor,
        sheet: str,
        node: str,
        column: str,
        body: str,
        parent_comment: Optional[str] = None,
        mentions: Optional[list[str]] = None,
    ) -> str:
        # Derive thread_root the same way the frappe controller does: the
        # parent's root, or the parent itself if the parent is a root; None for
        # a root comment.
        thread_root = None
        if parent_comment:
            parent = self.session.get(CellComment, parent_comment)
            if parent is not None:
                thread_root = parent.thread_root or parent.name
        row = CellComment(
            sheet=sheet,
            node=node,
            column=column,
            author=actor.user,
            body=body,
            thread_root=thread_root,
            parent_comment=parent_comment or None,
            mentions=list(mentions or []),
            # FULL audit trace: the REAL principal + the effective identity when
            # posted under an "act as" overlay (both None for a normal action).
            real_user=getattr(actor, "real_user", None),
            impersonated_as=getattr(actor, "impersonated_as", None),
        )
        self.session.add(row)
        self.session.flush()
        return row.name

    def set_comment_resolved(self, actor, comment: str, resolved: bool) -> str:
        """Resolve/reopen the comment's THREAD ROOT. Idempotent; returns the root
        id."""
        row = self.session.get(CellComment, comment)
        if row is None:
            raise KeyError(comment)
        root_name = row.thread_root or row.name
        root = self.session.get(CellComment, root_name)
        if root is None:
            raise KeyError(root_name)
        if resolved:
            root.resolved = True
            root.resolved_by = actor.user
            root.resolved_at = utcnow()
        else:
            root.resolved = False
            root.resolved_by = None
            root.resolved_at = None
        self.session.flush()
        return root.name

    def soft_delete_comment(self, actor, comment: str) -> None:
        """Soft-delete (tombstone) a comment: ``deleted=1`` + ``deleted_by`` +
        ``deleted_at``, row PRESERVED for audit. ``list_comments`` filters it
        out."""
        row = self.session.get(CellComment, comment)
        if row is None:
            raise KeyError(comment)
        row.deleted = True
        row.deleted_by = actor.user
        row.deleted_at = utcnow()
        self.session.flush()

    def list_comments(self, sheet: str, node: str, column: str) -> list[CommentRowView]:
        """Non-capability read peer of the ``list_cell_comments`` shim: the thread
        for a cell, oldest-first, EXCLUDING soft-deleted tombstones."""
        rows = self.session.scalars(
            sa.select(CellComment)
            .where(
                CellComment.sheet == sheet,
                CellComment.node == node,
                CellComment.column == column,
                CellComment.deleted.is_(False),
            )
            .order_by(CellComment.creation, CellComment.name)
        ).all()
        return [_comment_view(r) for r in rows]
