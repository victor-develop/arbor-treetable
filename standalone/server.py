"""Arbor standalone — deployment smoke server (Phase 0).

The FIRST thing shipped to App Center Internal Apps (iapp). Deliberately tiny:
its only job is to prove the four platform facts the real standalone adapter
(FastAPI + SQLAlchemy over the arbor.core ports) will depend on:

1. python runtime + requirements.txt auto-install work;
2. the app is reachable on the platform's fixed port 3000;
3. the injected ``DATABASE_URL`` MySQL is connectable and writable;
4. MySQL rows SURVIVE a redeploy (container files don't — the guide's rule 2).

Endpoints: ``/healthz`` (liveness), ``/`` (human status page), ``/db`` (SELECT 1
+ a smoke ledger row so redeploy persistence is observable).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import create_engine, text

app = FastAPI(title="arbor-standalone smoke")

VERSION = "0.0.1"


def _engine():
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    # The platform injects a mysql://-style URL; SQLAlchemy needs the pymysql
    # driver spelled out.
    if url.startswith("mysql://"):
        url = "mysql+pymysql://" + url[len("mysql://"):]
    return create_engine(url, pool_pre_ping=True)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "version": VERSION}


@app.get("/db")
def db() -> dict:
    eng = _engine()
    if eng is None:
        return {"ok": False, "error": "DATABASE_URL not injected"}
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS smoke_ledger ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "version VARCHAR(32) NOT NULL,"
                    "at DATETIME NOT NULL)"
                )
            )
            conn.execute(
                text("INSERT INTO smoke_ledger (version, at) VALUES (:v, :t)"),
                {"v": VERSION, "t": datetime.now(timezone.utc).replace(tzinfo=None)},
            )
            count = conn.execute(text("SELECT COUNT(*) FROM smoke_ledger")).scalar()
            first = conn.execute(text("SELECT MIN(at) FROM smoke_ledger")).scalar()
        # count grows across requests AND redeploys; `first` staying put across a
        # redeploy is the persistence proof.
        return {"ok": True, "rows": count, "first_row_at": str(first), "version": VERSION}
    except Exception as exc:  # surface the reason, never 500 opaquely
        return {"ok": False, "error": str(exc)[:200]}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    has_db = "yes" if os.environ.get("DATABASE_URL") else "no"
    return f"""<!doctype html><meta charset="utf-8">
<title>Arbor standalone — smoke</title>
<body style="font-family: system-ui; max-width: 40rem; margin: 4rem auto; line-height: 1.6">
<h1>🌳 Arbor standalone</h1>
<p>Deployment pipeline smoke v{VERSION} — the real Arbor (framework-free core +
SQL adapter) lands here next.</p>
<ul>
<li>runtime: python + auto-installed requirements ✅</li>
<li>port 3000 ✅ (you are reading this)</li>
<li>DATABASE_URL injected: <b>{has_db}</b> — <a href="/db">probe MySQL</a></li>
<li><a href="/healthz">healthz</a></li>
</ul>
</body>"""
