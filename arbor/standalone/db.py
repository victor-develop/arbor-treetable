"""Engine + session plumbing for the standalone adapter.

One environment variable drives everything: ``DATABASE_URL``. The deploy
platform injects a ``mysql://`` URL (rewritten here to the pymysql driver,
same rule the Phase-0 smoke server proved); tests and local dev fall back to
a sqlite file. No pooling exotica, no migrations framework — the schema is
bootstrapped idempotently with ``create_all`` at startup (MySQL rows survive
redeploys; ``CREATE TABLE IF NOT EXISTS`` makes restart safe).
"""

from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

#: Local/test fallback when no DATABASE_URL is injected.
DEFAULT_SQLITE_URL = "sqlite:///arbor.db"


def database_url(url: Optional[str] = None) -> str:
    """Resolve the effective SQLAlchemy URL.

    Precedence: explicit ``url`` arg > ``DATABASE_URL`` env > sqlite fallback.
    A bare ``mysql://`` scheme is rewritten to ``mysql+pymysql://`` — the
    platform injects driverless URLs and SQLAlchemy needs the driver spelled
    out (same rewrite as standalone/server.py).
    """
    resolved = url or os.environ.get("DATABASE_URL") or DEFAULT_SQLITE_URL
    if resolved.startswith("mysql://"):
        resolved = "mysql+pymysql://" + resolved[len("mysql://"):]
    return resolved


def make_engine(url: Optional[str] = None, echo: bool = False) -> Engine:
    """Build an engine for ``url`` (resolved via :func:`database_url`).

    ``pool_pre_ping`` guards against MySQL's idle-connection reaping (the
    classic "server has gone away" after a quiet night); it is harmless on
    sqlite.
    """
    return create_engine(database_url(url), echo=echo, pool_pre_ping=True)


def create_all(engine: Engine) -> None:
    """Idempotent schema bootstrap: create every ``models`` table that does not
    exist yet. Called once at service startup (there is no separate migration
    step in the standalone lane; additive column changes ship as new tables or
    an explicit ALTER in the deploy notes)."""
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """The per-request Session factory. ``expire_on_commit=False`` so view
    objects read inside a request stay usable after commit (the Repository
    returns plain view data, never live ORM rows, but this keeps mapping code
    free of surprise lazy refreshes)."""
    return sessionmaker(bind=engine, expire_on_commit=False)
