"""The ``arbor.agent.chat`` whitelisted endpoint (ARCHITECTURE §8, §8.1).

    POST /api/method/arbor.agent.chat  {sheet, message}

Runs the server-side Re-Act agent for one user turn. Because the agent is
server-side, this single endpoint serves the React sidebar AND any headless/API
consumer identically (API parity).

Key governance property (ARCHITECTURE §8): the agent acts under the **caller's
OWN Frappe User** (``frappe.session.user``), stamped ``actor_type = "agent"``. It
is therefore subject to the same two-axis ACL — an unauthorized agent action
becomes a Change Request via the same ``execute_action`` path, never a privileged
bypass.

This module imports ``frappe`` and the Frappe adapter (the ``FrappeRepository`` /
``FrappeEventSink`` built by the adapter lane) lazily, so the rest of the agent
lane stays importable and unit-testable without a bench.
"""

from __future__ import annotations

from typing import Any, Optional

from arbor.core.types import Actor, ActorType

from .config import load_config
from .provider import get_provider
from .react import run_agent_session


def _adapter():
    """Resolve the Frappe adapter façade built by the adapter lane.

    Expected (integrator-wired) surface in ``arbor.arbor.api`` (or
    ``arbor.api``):
      - ``get_repository()`` -> Repository  (FrappeRepository over ORM+NestedSet)
      - ``get_event_sink()`` -> EventSink    (FrappeEventSink writing Tree Event)
      - ``get_sheet_snapshot(sheet, actor)`` -> dict  (shared serializer + hints)

    Imported lazily and by name so this lane does not hard-depend on the adapter
    lane's exact module path at import time.
    """
    import importlib

    for path in ("arbor.arbor.api", "arbor.api"):
        try:
            return importlib.import_module(path)
        except ModuleNotFoundError:
            continue
    raise ImportError(
        "Arbor agent: the Frappe adapter façade (get_repository / get_event_sink "
        "/ get_sheet_snapshot) was not found under arbor.arbor.api. The integrator "
        "must expose it (see the agent lane manifest)."
    )


def chat(
    sheet: Optional[str] = None,
    message: Optional[str] = None,
    max_steps: Optional[int] = None,
) -> dict[str, Any]:
    """Whitelisted entrypoint. Decorated at import time on a live bench.

    Returns the streamed session dict: ``{final_message, transcript[],
    tool_calls[], terminated_by}`` (the React sidebar renders the transcript as
    Thought/Action/Observation; headless callers read ``final_message``).
    """
    import frappe  # local: this module must import off-bench

    if not message:
        frappe.throw("`message` is required")

    actor = Actor(user=frappe.session.user, actor_type=ActorType.AGENT)

    adapter = _adapter()
    repo = adapter.get_repository()
    sink = adapter.get_event_sink()

    def snapshot_fn(sheet_name: str, act: Actor) -> dict[str, Any]:
        # Reuse the ONE shared snapshot serializer (via the adapter) so the
        # agent's read matches web/REST exactly.
        return adapter.get_sheet_snapshot(sheet_name, act)

    cfg = load_config()
    provider = get_provider(cfg)

    # Bind the active sheet into the system prompt so the agent knows which sheet
    # to pass as the ``sheet`` argument of every tool (otherwise it can't act).
    from .react import _DEFAULT_SYSTEM

    system = _DEFAULT_SYSTEM
    if sheet:
        system = (
            f"{_DEFAULT_SYSTEM} You are operating on the sheet named '{sheet}'. "
            f"Always pass sheet='{sheet}' as the 'sheet' argument to every tool call, "
            f"and call getSheetSnapshot for '{sheet}' before any mutation."
        )

    session = run_agent_session(
        message=message,
        actor=actor,
        repo=repo,
        sink=sink,
        provider=provider,
        snapshot_fn=snapshot_fn,
        system=system,
        max_steps=max_steps or cfg.max_steps,
    )
    return session.as_dict()


def _register_whitelist() -> None:
    """Apply ``frappe.whitelist`` to ``chat`` only when frappe is importable, so
    the function is a plain callable off-bench (keeping the lane bench-free for
    unit tests) and a proper API method on a live site."""
    try:  # pragma: no cover - exercised only on a live bench
        import frappe

        globals()["chat"] = frappe.whitelist()(chat)
    except Exception:  # pragma: no cover - no bench in pure tests
        pass


_register_whitelist()
