"""The composed standalone Repository + EventSink (the two core ports).

``SQLRepository`` mixes the four parallel-authored slices — tree, governance,
collab, process — over ONE shared SQLAlchemy ``Session``, satisfying the whole
``arbor.core.ports.Repository`` protocol the executor / ACL / handlers run
against. ``SQLEventSink`` is the ``EventSink`` port: it appends one
``tree_events`` row per emitted event (the ONLY record of "what happened",
DATA-MODEL §12) and hands the STORED event back with its assigned
``event_id``/``timestamp`` — the exact contract of ``FrappeEventSink.emit``.

Two small seams keep the api layer's dispatch fan-out out of the storage layer
(mirroring the frappe ``doc_events`` hooks without frappe):

* ``SQLEventSink(dispatch=...)`` — called once per stored event (the analog of
  the ``Tree Event after_insert`` hook feeding notifications/webhooks/process);
* ``SQLRepository(on_notification=...)`` — called once per created Notification
  row (the analog of the ``Notification after_insert`` webhook bridge).

Both default to None (pure storage, exactly ``InMemoryRepository`` behavior).

``CellDraft`` lives here rather than in ``models.py``: drafts are API-layer
staging (frappe's ``Arbor Cell Draft``), not a Repository-port doctype — the
Repository protocol never touches them; only ``app.py`` does.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, Session, mapped_column

from arbor.core.types import ActorType, TreeEvent

from . import models as m
from .repo_collab import CollabRepoMixin
from .repo_governance import GovernanceRepoMixin
from .repo_process import ProcessRepoMixin
from .repo_tree import TreeRepoMixin


class CellDraft(m.NamedRow, m.Base):
    """Arbor Cell Draft — per-USER, private staging for cell edits BEFORE they
    become a Change Request (Feature: cell drafts). Upsert-keyed by
    ``(user, sheet, node, column)`` so two saves on one cell collapse to ONE
    draft holding the latest value. Not a Repository-port table: only the
    draft-box endpoints in ``app.py`` read/write it."""

    __tablename__ = "cell_drafts"
    __table_args__ = (
        sa.UniqueConstraint("user", "sheet", "node", "column", name="uq_cell_drafts_cell"),
    )

    user: Mapped[str] = mapped_column(sa.String(140))
    sheet: Mapped[str] = mapped_column(sa.String(140))
    node: Mapped[str] = mapped_column(sa.String(140))
    column: Mapped[str] = mapped_column(sa.String(140))
    value: Mapped[Any | None] = mapped_column(sa.JSON, default=None)
    base_version: Mapped[int | None] = mapped_column(sa.Integer, default=None)


class SQLRepository(TreeRepoMixin, GovernanceRepoMixin, CollabRepoMixin, ProcessRepoMixin):
    """The standalone ``Repository`` (ports.py) — the four mixin slices composed
    over one Session. Mutators ``flush()`` (ids/lft/rgt visible within the
    request); commit/rollback belongs to the request layer (``app.py``'s
    per-request unit of work), exactly as each mixin documents.
    """

    def __init__(
        self,
        session: Session,
        on_notification: Callable[[str], None] | None = None,
    ) -> None:
        self.session = session
        #: Post-insert seam for the Notification -> webhook bridge (WS-A3b/A3c).
        #: The api layer wires it to ``notification_webhook.fan_out``; None keeps
        #: the repository a pure store (tests, scripts).
        self.on_notification = on_notification

    def create_notification(self, data: dict[str, Any]) -> str:
        """Create one in-app Notification row (CollabRepoMixin), then fire the
        optional fan-out seam — the standalone analog of the frappe
        ``Notification after_insert`` hook. The seam is called on the idempotent
        short-circuit too; the delivery engine's per-(endpoint, event_id) key
        makes a repeat a no-op, matching the at-least-once doc_event contract."""
        name = CollabRepoMixin.create_notification(self, data)
        if self.on_notification is not None:
            self.on_notification(name)
        return name


#: Back-compat alias — ``.auth`` lazily imports the composed class by this name.
Repository = SQLRepository


class SQLEventSink:
    """``EventSink`` over the append-only ``tree_events`` table.

    ``emit`` is the ONLY place a Tree Event row is created (ARCHITECTURE §4.3);
    rows are never updated or deleted. Returns the stored event with the
    assigned ``event_id`` (the row PK) and ``timestamp`` (``str(creation)``,
    the same shape ``FrappeEventSink`` returns). ``dispatch``, when given, is
    invoked once per stored event — the standalone analog of the frappe
    ``Tree Event after_insert`` doc_event that feeds the notification / webhook
    / process consumers; none of those emit events, so there is no recursion.
    """

    def __init__(
        self,
        session: Session,
        dispatch: Callable[[TreeEvent], None] | None = None,
    ) -> None:
        self.session = session
        self._dispatch = dispatch

    def emit(self, event: TreeEvent) -> TreeEvent:
        actor_type = event.actor_type
        if isinstance(actor_type, ActorType):
            actor_type = actor_type.value

        row = m.TreeEventRow(
            sheet=event.sheet,
            type=event.type,
            payload=dict(event.payload or {}),
            actor=event.actor,
            actor_type=actor_type,
            # Impersonation trace (Area 1): both NULL for a normal action, so a
            # non-impersonated event is byte-for-byte as before.
            real_user=event.real_user,
            impersonated_as=event.impersonated_as,
            change_request=event.change_request,
        )
        self.session.add(row)
        self.session.flush()  # assign name/creation before they are read back

        stored = replace(
            event,
            actor_type=actor_type,
            event_id=row.name,
            timestamp=str(row.creation),
        )
        if self._dispatch is not None:
            self._dispatch(stored)
        return stored
