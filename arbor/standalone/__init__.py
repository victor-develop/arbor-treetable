"""Arbor standalone adapter — the frappe-free deployment lane.

This package implements the ``arbor.core`` ports (ports.py Protocols) over
SQLAlchemy 2.0 instead of the Frappe ORM + NestedSet, so the exact same domain
kernel (registry, ACL resolver, executor, CR state machine, agent loop) runs as
a plain Python service: sqlite for tests, MySQL via ``DATABASE_URL`` in deploy.
No redis, no celery, no bench.

Layout:

* ``models``  — SQLAlchemy Declarative tables mirroring the Frappe doctypes the
  Repository reads/writes (one class per doctype, child tables folded into
  JSON columns).
* ``db``      — engine factory (``DATABASE_URL`` → engine), schema bootstrap
  (``create_all``) and the session factory.

The behavioral contract is fixed elsewhere: ``arbor.core.ports`` (the Protocol
docstrings) says WHAT each Repository method must do, and
``arbor.core.testing.InMemoryRepository`` is the executable reference for HOW
(row shapes, ordering, idempotency). The frappe adapter under ``arbor.arbor``
is read-only prior art; nothing in this package imports frappe.
"""
