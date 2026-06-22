"""Provider selection (ARCHITECTURE §10).

The active :class:`~arbor.auth.provider.AuthProvider` is named by site config
key ``arbor.auth.provider_class`` (a dotted import path). This resolver imports
and instantiates it. The default is the open-source :class:`LocalAuthProvider`.

An isolated private deployment sets::

    "arbor.auth.provider_class": "arbor_sso_overlay.provider.EmployeeSSOProvider"

…and that string is the ONLY coupling point — core never imports the overlay
app; it is loaded dynamically by name only when configured. This is what keeps
the open-source build free of any overlay dependency and lets the SSO app be
omitted entirely.
"""

from __future__ import annotations

import importlib
from functools import lru_cache
from typing import Optional

from .local import LocalAuthProvider
from .provider import AuthProvider

PROVIDER_CLASS_KEY = "arbor.auth.provider_class"
DEFAULT_PROVIDER_CLASS = "arbor.auth.local.LocalAuthProvider"


def resolve_provider_class() -> str:
    """Return the configured provider dotted-path, or the local default."""
    try:
        import frappe

        return frappe.conf.get(PROVIDER_CLASS_KEY) or DEFAULT_PROVIDER_CLASS
    except Exception:
        return DEFAULT_PROVIDER_CLASS


def _load(dotted: str) -> AuthProvider:
    module_path, _, cls_name = dotted.rpartition(".")
    if not module_path:
        raise ValueError(f"{PROVIDER_CLASS_KEY} must be a dotted path, got {dotted!r}")
    cls = getattr(importlib.import_module(module_path), cls_name)
    return cls()


@lru_cache(maxsize=8)
def _cached(dotted: str) -> AuthProvider:
    return _load(dotted)


def get_auth_provider(provider_class: Optional[str] = None, *, cache: bool = True) -> AuthProvider:
    """Instantiate the active provider.

    ``provider_class`` overrides site config (handy for tests). The instance is
    memoized per dotted-path unless ``cache=False``. Falls back to
    :class:`LocalAuthProvider` if the configured class fails to import.
    """
    dotted = provider_class or resolve_provider_class()
    try:
        return _cached(dotted) if cache else _load(dotted)
    except Exception:
        # Never hard-fail auth resolution; degrade to the safe local default.
        return LocalAuthProvider()
