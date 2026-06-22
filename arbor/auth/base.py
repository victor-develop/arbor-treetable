"""Shared base for the bundled providers (DRY for User provisioning).

``BaseAuthProvider`` factors out the one piece every provider needs and that
must behave identically across them: turning a normalized
:class:`~arbor.auth.provider.UserIdentity` into a Frappe User row (find-or-create)
and establishing the login session. Local and OIDC both reuse it; the employee
SSO app may reuse it too (it is plain Frappe code, no overlay SDK) or inline
its own — either way the *interface* it satisfies is the core Protocol.

Frappe is imported lazily inside methods so this module stays importable for
linting / typing on a bench-free checkout; the methods themselves require a live
Frappe site to run.
"""

from __future__ import annotations

from typing import Optional

from .provider import AuthResult, UserIdentity


class BaseAuthProvider:
    """Common provisioning helpers. Not abstract: subclasses override the four
    Protocol methods; this only supplies ``ensure_user`` / ``login_user``."""

    #: Roles granted to a freshly auto-provisioned User. Kept minimal; the
    #: two-axis ACL (Branch Grant / column ownership) is what actually authorizes
    #: actions, so SSO users start with no Arbor authority until granted.
    default_roles: tuple[str, ...] = ()
    #: Whether to create a Frappe User on first sight of an unknown identity.
    auto_create_user: bool = True

    def ensure_user(self, identity: UserIdentity) -> str:
        """Find-or-create the Frappe User keyed by ``identity.email``; returns
        the User name. Raises if the user is unknown and ``auto_create_user`` is
        off. This is the ONLY place auth provisions a User, so every provider
        lands users on the same two-axis-ACL footing."""
        import frappe  # local import: requires a live site

        email = (identity.email or "").strip().lower()
        if not email:
            raise ValueError("identity has no email; cannot resolve a Frappe User")

        if frappe.db.exists("User", email):
            return email

        if not self.auto_create_user:
            raise frappe.AuthenticationError(f"unknown user {email!r} and auto-create is disabled")

        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": identity.first_name or identity.full_name or email,
                "last_name": identity.last_name or "",
                "enabled": 1,
                "user_type": "System User",
            }
        )
        user.flags.ignore_permissions = True
        user.insert(ignore_permissions=True)
        for role in self.default_roles:
            user.add_roles(role)
        return user.name

    def login_user(self, user: str, identity: Optional[UserIdentity] = None) -> AuthResult:
        """Establish the Frappe login session for ``user`` and return a success
        :class:`AuthResult`."""
        import frappe
        from frappe.auth import LoginManager

        frappe.local.login_manager = LoginManager()
        frappe.local.login_manager.login_as(user)
        return AuthResult.ok(user=user, identity=identity)
