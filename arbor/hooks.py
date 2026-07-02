"""Frappe app hooks for Arbor (the ADAPTER).

This is the ONE place that names the Frappe app identity and wires the lanes
together. The open-source core (``arbor.core``) is framework-free and is never
named here.

Module-path note
----------------
On a Frappe bench the app package root is the repo-level ``arbor/`` package (the
one declaring ``__version__``). Therefore:

* the pure core resolves as ``arbor.core.*`` (it lives at ``<repo>/arbor/core``);
* the Frappe *adapter* modules resolve as ``arbor.arbor.*`` (they live at
  ``<repo>/arbor/arbor``), so doc_events / scheduler / overrides below reference
  the ``arbor.arbor.*`` paths;
* the DOCUMENTED collapsed public paths (``arbor.execute_action``,
  ``arbor.agent.chat``, ``arbor.auth.*``, ``arbor.accountability``) are exposed
  via ``override_whitelisted_methods`` aliases below + the thin re-export shims
  ``arbor/api.py``, ``arbor/agent/chat.py``, ``arbor/adapter/*``.
"""

app_name = "arbor"
app_title = "Arbor"
app_publisher = "Arbor"
app_description = "Governed, API-first, agent-native tree-table SaaS"
app_email = "dev@arbor.example"
app_license = "MIT"

# ---------------------------------------------------------------------------
# DocType doc_events — the dispatch lane (DRY: ONE doc_event feeds BOTH the
# notification dispatcher AND the webhook dispatcher off the SAME new Tree Event
# row). Neither dispatcher emits a Tree Event, so there is no recursion.
# (Manifest: dispatchers lane.)
# ---------------------------------------------------------------------------
doc_events = {
    "Tree Event": {
        "after_insert": "arbor.arbor.dispatch.frappe_dispatch.on_tree_event_insert",
    },
}

# ---------------------------------------------------------------------------
# Scheduler — webhook retry runner on the core backoff schedule
# (0s, 30s, 5m, 30m, 2h, 12h). A per-minute tick is fine-grained enough for the
# 30s slot; the runner only POSTs deliveries whose ``next_retry_at`` is due.
# (Manifest: dispatchers lane.)
# ---------------------------------------------------------------------------
scheduler_events = {
    "cron": {
        "* * * * *": [
            "arbor.arbor.dispatch.frappe_dispatch.run_webhook_retries",
            # Process/SLA sweep (Area 3): mark the current stage of over-due active
            # runs breached + notify the stage owner once (when the process opts in
            # via sla_breach_notify). Bounded to active runs with a past due_at.
            "arbor.arbor.dispatch.frappe_dispatch.run_process_sla_sweep",
        ],
    },
}

# ---------------------------------------------------------------------------
# Whitelisted-method aliases — make the DOCUMENTED collapsed public paths
# (ARCHITECTURE §8.1) resolve to the single real implementations. Every
# capability method already carries @frappe.whitelist() in ``arbor.arbor.api``;
# the re-export shim ``arbor.api`` exposes the same callables. These aliases give
# the short ``arbor.<verb>`` URLs without re-implementing anything.
#
# Auth endpoints (auth-sso lane): the plain callables in ``arbor.auth.api`` are
# intentionally undecorated (so the module stays import-clean for the bench-free
# suite); they are exposed here through whitelisted shims.
#
# accountability (dispatchers lane, optional): exposes the "N notified / M acked"
# aggregate over REST.
# ---------------------------------------------------------------------------
override_whitelisted_methods = {
    # Generic dispatch + snapshot
    "arbor.execute_action": "arbor.arbor.api.execute_action",
    "arbor.get_sheet_snapshot": "arbor.arbor.api.get_sheet_snapshot",
    # Explore: bounded, navigable LLM read API (used above EXPLORE_THRESHOLD)
    "arbor.sheet_overview": "arbor.arbor.api.sheet_overview",
    "arbor.list_children": "arbor.arbor.api.list_children",
    "arbor.get_subtree": "arbor.arbor.api.get_subtree",
    "arbor.get_node": "arbor.arbor.api.get_node",
    "arbor.search_nodes": "arbor.arbor.api.search_nodes",
    "arbor.get_cells": "arbor.arbor.api.get_cells",
    # One per capability (camelCase id -> snake_case method)
    "arbor.add_node": "arbor.arbor.api.add_node",
    "arbor.update_cell": "arbor.arbor.api.update_cell",
    "arbor.move_node": "arbor.arbor.api.move_node",
    "arbor.delete_node": "arbor.arbor.api.delete_node",
    "arbor.add_column": "arbor.arbor.api.add_column",
    "arbor.update_column": "arbor.arbor.api.update_column",
    "arbor.delete_column": "arbor.arbor.api.delete_column",
    "arbor.suggest_change": "arbor.arbor.api.suggest_change",
    "arbor.suggest_changes": "arbor.arbor.api.suggest_changes",
    "arbor.define_process": "arbor.arbor.api.define_process",
    "arbor.enable_process": "arbor.arbor.api.enable_process",
    "arbor.disable_process": "arbor.arbor.api.disable_process",
    "arbor.start_process_run": "arbor.arbor.api.start_process_run",
    # Process READ shims (NOT capabilities) — definition, kanban/flow dashboard,
    # per-stage run drill-down, and the cross-sheet per-user inbox (Area 3).
    "arbor.get_process": "arbor.arbor.api.get_process",
    "arbor.process_dashboard": "arbor.arbor.api.process_dashboard",
    "arbor.list_process_runs": "arbor.arbor.api.list_process_runs",
    "arbor.inbox": "arbor.arbor.api.inbox",
    # Impersonation ("act as") — traceable, admin-gated overlay (Area 1). Both
    # funnel through the SAME executor as every capability; begin/end emit NO
    # Tree Event (the Arbor Impersonation Session row IS the audit record).
    "arbor.begin_impersonation": "arbor.arbor.api.begin_impersonation",
    "arbor.end_impersonation": "arbor.arbor.api.end_impersonation",
    "arbor.create_sheet": "arbor.arbor.api.create_sheet",
    "arbor.list_sheets": "arbor.arbor.api.list_sheets",
    "arbor.list_change_requests": "arbor.arbor.api.list_change_requests",
    "arbor.list_notifications": "arbor.arbor.api.list_notifications",
    "arbor.list_activity": "arbor.arbor.api.list_activity",
    "arbor.approve_change": "arbor.arbor.api.approve_change",
    "arbor.reject_change": "arbor.arbor.api.reject_change",
    "arbor.withdraw_change": "arbor.arbor.api.withdraw_change",
    "arbor.subscribe": "arbor.arbor.api.subscribe",
    "arbor.unsubscribe": "arbor.arbor.api.unsubscribe",
    "arbor.acknowledge": "arbor.arbor.api.acknowledge",
    "arbor.delegate_branch": "arbor.arbor.api.delegate_branch",
    "arbor.revoke_delegation": "arbor.arbor.api.revoke_delegation",
    "arbor.grant_column": "arbor.arbor.api.grant_column",
    "arbor.internal_reset": "arbor.arbor.api.internal_reset",
    # Role management (Feature: roles)
    "arbor.assign_role": "arbor.arbor.api.assign_role",
    "arbor.revoke_role": "arbor.arbor.api.revoke_role",
    "arbor.apply_for_role": "arbor.arbor.api.apply_for_role",
    "arbor.approve_role_application": "arbor.arbor.api.approve_role_application",
    "arbor.reject_role_application": "arbor.arbor.api.reject_role_application",
    "arbor.withdraw_role_application": "arbor.arbor.api.withdraw_role_application",
    "arbor.list_roles": "arbor.arbor.api.list_roles",
    "arbor.list_role_grants": "arbor.arbor.api.list_role_grants",
    "arbor.list_role_applications": "arbor.arbor.api.list_role_applications",
    # Personal cell draft box (Feature: cell drafts) — per-user staging, scoped
    # to actor.user; submit promotes ALL drafts to ONE multi-change CR.
    "arbor.save_cell_draft": "arbor.arbor.api.save_cell_draft",
    "arbor.list_cell_drafts": "arbor.arbor.api.list_cell_drafts",
    "arbor.discard_cell_draft": "arbor.arbor.api.discard_cell_draft",
    "arbor.discard_cell_drafts": "arbor.arbor.api.discard_cell_drafts",
    "arbor.submit_cell_drafts": "arbor.arbor.api.submit_cell_drafts",
    # Per-cell comments drawer (Feature: comments, Area 2) — threaded, cell-keyed
    # collaboration metadata. NOT registry capabilities and NOT Tree Events; read/
    # post gated by can_read_column, resolve by column approvers, delete by
    # author-or-approver. add fans out a source='comment' Notification directly.
    "arbor.add_cell_comment": "arbor.arbor.api.add_cell_comment",
    "arbor.list_cell_comments": "arbor.arbor.api.list_cell_comments",
    "arbor.resolve_cell_comment": "arbor.arbor.api.resolve_cell_comment",
    "arbor.delete_cell_comment": "arbor.arbor.api.delete_cell_comment",
    # Server-side Re-Act agent
    "arbor.agent.chat": "arbor.arbor.agent.chat.chat",
    # Accountability aggregate (N notified / M acked)
    "arbor.accountability": "arbor.arbor.dispatch.frappe_dispatch.accountability",
    # Auth seam (auth-sso lane). login_url / oidc_callback allow guests.
    "arbor.auth.login_url": "arbor.auth.api.login_url",
    "arbor.auth.oidc_callback": "arbor.auth.api.oidc_callback",
    "arbor.auth.whoami": "arbor.auth.api.whoami",
}

# ---------------------------------------------------------------------------
# Fixtures — none required. The 13 DocTypes carry ``module = "Arbor"`` and ship
# as code; the canonical sheet is created at runtime by
# ``arbor.adapter.seed.seed_canonical_sheet`` (tests / demos), not as a fixture.
# ---------------------------------------------------------------------------
fixtures: list = []

# NOTE on append-only Tree Event: the controller's on_update / on_trash guards
# rely on ``flags.ignore_arbor_append_only`` being set ONLY by the internalReset
# admin purge path. We deliberately register NO write/update doc_event for Tree
# Event that would bypass this guard.
