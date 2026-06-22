"""Public re-export shim: ``arbor.api`` -> ``arbor.arbor.api``.

On a Frappe bench the app package root is the repo-level ``arbor/`` (the one
declaring ``__version__``), so the Frappe *module* code lives at
``arbor.arbor.*``. The DOCUMENTED public surface (ARCHITECTURE §8.1) and the
agent / backend test bind points use the collapsed ``arbor.api`` path. This shim
makes the collapsed path resolve to the single real implementation — it adds NO
logic of its own (DRY: one whitelisted facade, in ``arbor.arbor.api``).
"""

from __future__ import annotations

from arbor.arbor.api import *  # noqa: F401,F403
from arbor.arbor.api import (  # noqa: F401  explicit re-export of the callables
    acknowledge,
    add_column,
    add_node,
    approve_change,
    delegate_branch,
    delete_column,
    delete_node,
    execute_action,
    get_event_sink,
    get_repository,
    get_sheet_snapshot,
    grant_column,
    internal_reset,
    move_node,
    reject_change,
    revoke_delegation,
    subscribe,
    suggest_change,
    unsubscribe,
    update_cell,
    update_column,
    withdraw_change,
)
