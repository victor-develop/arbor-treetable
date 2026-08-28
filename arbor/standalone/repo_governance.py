"""GovernanceRepoMixin — branch grants, change requests, roles, impersonation.

The governance slice of the standalone ``Repository`` (ports.py): Axis-1 branch
grants + column authority, the mutate-or-suggest Change Request ledger, the role
system (roles / grants / applications / admins), and the "act as" impersonation
overlay (Area 1). Semantics mirror ``core.testing.InMemoryRepository`` (the
executable reference) with the Frappe adapter as prior art; storage is a plain
SQLAlchemy ``self.session`` supplied by the composing class.

Nothing here imports frappe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select

from .models import (
    BranchGrant,
    ChangeRequest,
    Column,
    ImpersonationSession,
    Role,
    RoleApplication,
    RoleGrant,
    User,
    utcnow,
)


# ---------------------------------------------------------------------------
# Lightweight read views (duck-typed to ports.BranchGrantView / RoleView /
# RoleGrantView). Defined here — not in the shared views module — because only
# the governance mixin builds them (parallel-authorship isolation).
# ---------------------------------------------------------------------------
@dataclass
class GrantView:
    name: str
    sheet: str
    branch_root: str
    grantee: str
    scope: str = "structure"
    active: bool = True
    granted_by: Optional[str] = None


@dataclass
class RoleView:
    name: str
    role: str
    label: str = ""
    applicable: bool = True
    active: bool = True


@dataclass
class RoleGrantView:
    name: str
    role: str
    grantee: str
    granted_by: str
    active: bool = True
    source: str = "admin-grant"
    granted_via: Optional[str] = None


def _change_item(ch: dict[str, Any]) -> dict[str, Any]:
    """Normalize one multi-change (batch) CR item to the core's dict shape.
    ``changes`` is a JSON column here, so items round-trip as plain dicts —
    normalization keeps reads shape-stable regardless of what the writer sent
    (parity with the frappe adapter's child-row projection)."""
    return {
        "action": ch["action"],
        "target_kind": ch.get("target_kind"),
        "operation": ch.get("operation"),
        "payload": ch.get("payload") or {},
        "resolved_approver": ch.get("resolved_approver"),
        "item_approved": bool(ch.get("item_approved")),
        "approved_by": ch.get("approved_by"),
    }


class GovernanceRepoMixin:
    """Branch grants + change requests + roles + impersonation over SQLAlchemy.

    Assumes ``self.session`` (a SQLAlchemy ``Session``) from the composing
    class. Mutators ``flush`` so python-side id defaults materialize before the
    new row's ``name`` is returned; commit/rollback stays with the caller's
    unit-of-work.
    """

    # ---- branch grants ------------------------------------------------------
    def find_active_branch_grant(
        self, sheet: str, branch_root: str, scope: str = "structure"
    ) -> Optional[GrantView]:
        row = self.session.scalars(
            select(BranchGrant)
            .where(
                BranchGrant.sheet == sheet,
                BranchGrant.branch_root == branch_root,
                BranchGrant.scope == scope,
                BranchGrant.active.is_(True),
            )
            .order_by(BranchGrant.creation)
        ).first()
        return self._grant_view(row) if row else None

    def get_branch_grant(self, branch_grant: str) -> Optional[GrantView]:
        row = self.session.get(BranchGrant, branch_grant)
        return self._grant_view(row) if row else None

    def create_branch_grant(
        self, sheet: str, branch_root: str, grantee: str, granted_by: str
    ) -> str:
        row = BranchGrant(
            sheet=sheet,
            branch_root=branch_root,
            grantee=grantee,
            granted_by=granted_by,
            scope="structure",
            active=True,
        )
        self.session.add(row)
        self.session.flush()
        return row.name

    def deactivate_branch_grant(self, branch_grant: str) -> None:
        self.session.get(BranchGrant, branch_grant).active = False
        self.session.flush()

    @staticmethod
    def _grant_view(row: BranchGrant) -> GrantView:
        return GrantView(
            name=row.name,
            sheet=row.sheet,
            branch_root=row.branch_root,
            grantee=row.grantee,
            scope=row.scope,
            active=bool(row.active),
            granted_by=row.granted_by,
        )

    # ---- column authority (Axis 2 re-grant) ---------------------------------
    def set_column_authority(
        self,
        sheet: str,
        column: str,
        column_owner: Optional[str] = None,
        editors: Optional[list[str]] = None,
    ) -> None:
        row = self._authority_column_row(sheet, column)
        if column_owner is not None:
            row.column_owner = column_owner
        if editors is not None:
            # JSON column: assign a NEW list (in-place mutation is untracked).
            row.editors = list(editors)
        self.session.flush()

    def _authority_column_row(self, sheet: str, column: str) -> Column:
        """The Column ORM row by name, else by field key within the sheet —
        the same two-step lookup as InMemoryRepository.get_column."""
        row = self.session.get(Column, column)
        if row is not None:
            return row
        row = self.session.scalars(
            select(Column).where(Column.sheet == sheet, Column.field == column)
        ).first()
        if row is None:
            raise KeyError(f"no column {column!r} in {sheet!r}")
        return row

    # ---- change requests ----------------------------------------------------
    def create_change_request(self, data: dict[str, Any]) -> str:
        row = ChangeRequest(
            sheet=data["sheet"],
            target_kind=data["target_kind"],
            operation=data["operation"],
            payload=data.get("payload") or {},
            requester=data["requester"],
            # Impersonation trace (Area 1): the truly-authenticated admin when
            # the CR was proposed under an "act as" overlay; None otherwise.
            real_requester=data.get("real_requester"),
            resolved_approver=data.get("resolved_approver"),
            status=data.get("status", "proposed"),
            # approvals[] = flat list of approver user-ids collected so far.
            approvals=list(data.get("approvals") or []),
            # changes[] = the items of a multi-change (batch) CR.
            changes=[_change_item(ch) for ch in (data.get("changes") or [])],
        )
        self.session.add(row)
        self.session.flush()
        return row.name

    def get_change_request(self, change_request: str) -> dict[str, Any]:
        doc = self.session.get(ChangeRequest, change_request)
        if doc is None:
            raise KeyError(change_request)
        return {
            "name": doc.name,
            "sheet": doc.sheet,
            "target_kind": doc.target_kind,
            "operation": doc.operation,
            "payload": doc.payload or {},
            "requester": doc.requester,
            "real_requester": doc.real_requester,
            "resolved_approver": doc.resolved_approver,
            "status": doc.status,
            "approvals": list(doc.approvals or []),
            "changes": [_change_item(ch) for ch in (doc.changes or [])],
            "decided_by": doc.decided_by,
            "resulting_event": doc.resulting_event,
        }

    def update_change_request(self, change_request: str, patch: dict[str, Any]) -> None:
        doc = self.session.get(ChangeRequest, change_request)
        for k, v in (patch or {}).items():
            if k == "decided_by" and v:
                doc.decided_by = v
                doc.decided_at = utcnow()
            elif k == "approvals":
                # core passes a flat list of approver user-ids.
                doc.approvals = list(v or [])
            elif k == "changes":
                # core passes the full item list; renormalize wholesale.
                doc.changes = [_change_item(ch) for ch in (v or [])]
            elif hasattr(doc, k):
                setattr(doc, k, v)
        self.session.flush()

    # ---- roles / role grants / role applications (Feature: roles) -----------
    def get_role(self, role: str) -> Optional[RoleView]:
        d = self.session.get(Role, role)
        if d is None:
            return None
        return RoleView(
            name=d.name,
            role=d.role,
            label=d.label or d.role,
            applicable=bool(d.applicable),
            active=bool(d.active),
        )

    def list_active_role_grantees(self, role: str) -> list[str]:
        return sorted(
            self.session.scalars(
                select(RoleGrant.grantee).where(
                    RoleGrant.role == role, RoleGrant.active.is_(True)
                )
            ).all()
        )

    def find_active_role_grant(self, role: str, grantee: str) -> Optional[RoleGrantView]:
        d = self.session.scalars(
            select(RoleGrant)
            .where(
                RoleGrant.role == role,
                RoleGrant.grantee == grantee,
                RoleGrant.active.is_(True),
            )
            .order_by(RoleGrant.creation)
        ).first()
        if d is None:
            return None
        return RoleGrantView(
            name=d.name,
            role=d.role,
            grantee=d.grantee,
            granted_by=d.granted_by,
            active=bool(d.active),
            source=d.source or "admin-grant",
            granted_via=d.granted_via,
        )

    def create_role_grant(
        self,
        role: str,
        grantee: str,
        granted_by: str,
        source: str = "admin-grant",
        granted_via: Optional[str] = None,
    ) -> str:
        row = RoleGrant(
            role=role,
            grantee=grantee,
            granted_by=granted_by,
            source=source,
            granted_via=granted_via,
            active=True,
        )
        self.session.add(row)
        self.session.flush()
        return row.name

    def deactivate_role_grant(self, role_grant: str) -> None:
        self.session.get(RoleGrant, role_grant).active = False
        self.session.flush()

    def create_role_application(self, data: dict[str, Any]) -> str:
        row = RoleApplication(
            role=data["role"],
            requester=data["requester"],
            status=data.get("status", "proposed"),
            justification=data.get("justification"),
        )
        self.session.add(row)
        self.session.flush()
        return row.name

    def get_role_application(self, role_application: str) -> dict[str, Any]:
        d = self.session.get(RoleApplication, role_application)
        if d is None:
            raise KeyError(role_application)
        return {
            "name": d.name,
            "role": d.role,
            "requester": d.requester,
            "status": d.status,
            "justification": d.justification,
            "decided_by": d.decided_by,
            "resulting_grant": d.resulting_grant,
            "decided_event": d.decided_event,
        }

    def update_role_application(self, role_application: str, patch: dict[str, Any]) -> None:
        doc = self.session.get(RoleApplication, role_application)
        for k, v in (patch or {}).items():
            if k == "decided_by" and v:
                doc.decided_by = v
                doc.decided_at = utcnow()
            elif hasattr(doc, k):
                setattr(doc, k, v)
        self.session.flush()

    def find_open_role_application(self, role: str, requester: str) -> Optional[dict[str, Any]]:
        name = self.session.scalars(
            select(RoleApplication.name)
            .where(
                RoleApplication.role == role,
                RoleApplication.requester == requester,
                RoleApplication.status == "proposed",
            )
            .order_by(RoleApplication.creation)
        ).first()
        return self.get_role_application(name) if name else None

    def list_admins(self) -> list[str]:
        """Enabled users with the ``is_admin`` flag (the System Manager analog)
        — the role-application recipients + the admin capability gate."""
        return sorted(
            self.session.scalars(
                select(User.email).where(User.is_admin.is_(True), User.enabled.is_(True))
            ).all()
        )

    # ---- impersonation sessions (Area 1) -------------------------------------
    def create_impersonation_session(
        self, real_user: str, impersonated_user: str, reason: Optional[str] = None
    ) -> str:
        """Persist (and activate) an "act as" overlay for ``real_user`` acting as
        ``impersonated_user``. At most one active session per real_user: any
        prior active one is force-ended first (the handler contract). The row is
        the durable audit record of the window (no Tree Event for begin/end)."""
        self.end_impersonation(real_user)  # collapse any prior active overlay
        row = ImpersonationSession(
            real_user=real_user,
            impersonated_user=impersonated_user,
            reason=reason,
            active=True,
        )
        self.session.add(row)
        self.session.flush()
        return row.name

    def get_active_impersonation(self, real_user: str) -> Optional[dict[str, Any]]:
        """The active overlay row for ``real_user`` (the single source of truth
        ``_actor()`` reads), or None. If more than one active row somehow
        exists, the most recent wins."""
        d = self.session.scalars(
            select(ImpersonationSession)
            .where(
                ImpersonationSession.real_user == real_user,
                ImpersonationSession.active.is_(True),
            )
            .order_by(ImpersonationSession.creation.desc())
        ).first()
        if d is None:
            return None
        return {
            "name": d.name,
            "real_user": d.real_user,
            "impersonated_user": d.impersonated_user,
            "reason": d.reason,
            "active": bool(d.active),
        }

    def end_impersonation(self, real_user: str) -> None:
        """Deactivate every active overlay for ``real_user`` (idempotent: a
        no-op when none is active). Stamps ``ended_at`` so the window is
        bounded in the audit trail."""
        rows = self.session.scalars(
            select(ImpersonationSession).where(
                ImpersonationSession.real_user == real_user,
                ImpersonationSession.active.is_(True),
            )
        ).all()
        for row in rows:
            row.active = False
            row.ended_at = utcnow()
        if rows:
            self.session.flush()
