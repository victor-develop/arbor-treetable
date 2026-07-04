"""External-agent access tokens + capability scope (the PAT brain).

Arbor supports TWO LLM-driven surfaces (design note, "two-tier auth"):

* **Internal agent chat** (AgentDock) runs inside the authenticated web session
  and reuses the SSO JWT / Frappe session verbatim. Nothing here touches it.
* **External LLM agent** (ChatGPT / Claude / the user's own agent) lives OUTSIDE
  Arbor. It authenticates with a **Frappe-native API key** — the ARCHITECTURE
  already resolved that "an external system is just a normal Frappe User + API
  key bound by [the two-axis] ACL" (auth/provider.py, RESOLVED OPEN QUESTION 4).
  So we do NOT invent a bearer/JWT scheme; we add a thin, OPTIONAL *down-scope*
  on top of that key: an **Arbor Agent Token**.

An Arbor Agent Token is a single opaque secret the user pastes into the external
agent's bootstrap prompt. It resolves (adapter side) to the issuing user and an
:class:`AgentScope`. The scope is a CEILING that is intersected with — never
widens — the user's own authority:

* the token resolves to a Frappe User, so the full two-axis ACL + mutate-or-
  suggest executor still runs underneath (defense in depth: a leaked read-write
  token still cannot write a column the user doesn't own — it degrades to a
  Change Request);
* :func:`authorize_scope` is an ADDITIONAL gate applied at dispatch BEFORE the
  executor, rejecting anything the token's mode/sheets don't permit.

This module is PURE (zero frappe imports) so the whole scope decision + the
token secret math are unit-tested bench-free. The adapter (arbor.arbor.api)
only does the doctype lookup and calls in here.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any, Literal, Optional

from .registry import get_capability, has_capability

# ---------------------------------------------------------------------------
# Read-only capability set.
# ---------------------------------------------------------------------------
# A "read" token may ONLY call these — the bounded, side-effect-free explore /
# snapshot reads. It is an EXPLICIT allowlist (not inferred) because inference is
# unsafe: e.g. ``acknowledge`` has ``handler=None`` and ``emits=()`` yet WRITES an
# Acknowledgement row, so a "looks like a read" heuristic would wrongly admit it.
# The drift-guard test (test_agent_scope) asserts every id here is genuinely
# non-mutating (Axis.NONE, Operation.NONE, no emits, no handler) AND that the
# known false-positive ``acknowledge`` is deliberately excluded — so adding a new
# read capability without listing it here trips the test.
READ_ONLY_CAPABILITY_IDS: frozenset[str] = frozenset(
    {
        "getSheetSnapshot",
        "getSheetOverview",
        "getSheetDefinition",
        "listChildren",
        "getSubtree",
        "getNode",
        "searchNodes",
        "getCells",
    }
)

TokenMode = Literal["read", "write"]


class ScopeError(Exception):
    """An external-agent token attempted a capability outside its scope.

    Adapter maps this to HTTP 403 (like a control-action AuthorizationError):
    it is a hard denial that NEVER degrades to a Change Request — the token
    simply may not reach the executor for this action.
    """


@dataclass(frozen=True)
class AgentScope:
    """The ceiling an Arbor Agent Token grants.

    Two independent dimensions (kept intentionally coarse for the MVP —
    "whole-account read" / "whole-account write" is the common case):

    * ``mode`` — ``"read"`` limits to :data:`READ_ONLY_CAPABILITY_IDS`; ``"write"``
      permits every LLM-exposed capability (still floored by the user's ACL).
    * ``sheets`` — ``None`` means every sheet the user can reach; a frozenset
      restricts to those sheet ids. Under a sheet-restricted token a capability
      that carries no ``sheet`` param (account-level ops: createSheet, roles,
      subscribe-by-notification, approve-by-CR…) is DENIED — conservative by
      design; widen later if a concrete need appears.
    """

    mode: TokenMode = "write"
    sheets: Optional[frozenset[str]] = None

    def __post_init__(self) -> None:
        if self.mode not in ("read", "write"):
            raise ValueError(f"invalid token mode {self.mode!r} (want 'read' | 'write')")
        if self.sheets is not None and not isinstance(self.sheets, frozenset):
            # Normalize any iterable to a frozenset so equality / membership are stable.
            object.__setattr__(self, "sheets", frozenset(self.sheets))


def is_read_capability(action_id: str) -> bool:
    """True iff ``action_id`` is a side-effect-free read (in the allowlist)."""
    return action_id in READ_ONLY_CAPABILITY_IDS


def authorize_scope(scope: AgentScope, action_id: str, params: dict[str, Any]) -> None:
    """Raise :class:`ScopeError` unless ``scope`` permits ``action_id`` on ``params``.

    Applied at dispatch for requests carrying an Arbor Agent Token, BEFORE the
    executor / ACL. Order: unknown-capability → mode → sheet restriction. A pass
    here is necessary but NOT sufficient — the executor's ACL still runs.
    """
    if not has_capability(action_id):
        # Mirror the registry's own error surface; the adapter turns it into 404.
        raise ScopeError(f"unknown capability {action_id!r}")

    # An agent token may never reach a capability hidden from LLMs (internalReset,
    # role admin, impersonation): those are is_exposed_to_llm=False and are simply
    # off-limits to external agents regardless of mode.
    cap = get_capability(action_id)
    if not cap.is_exposed_to_llm:
        raise ScopeError(f"capability {action_id!r} is not available to external agents")

    if scope.mode == "read" and not is_read_capability(action_id):
        raise ScopeError(
            f"read-only token cannot call {action_id!r} (a mutating capability)"
        )

    if scope.sheets is not None:
        sheet = params.get("sheet") if isinstance(params, dict) else None
        if sheet is None:
            raise ScopeError(
                f"sheet-scoped token cannot call {action_id!r} "
                "(no sheet target; account-level actions are out of scope)"
            )
        if sheet not in scope.sheets:
            raise ScopeError(
                f"token is not scoped to sheet {sheet!r}"
            )


# ---------------------------------------------------------------------------
# Token secret math (pure stdlib; mirrors core.security's HMAC style).
# ---------------------------------------------------------------------------
#: Human-recognizable prefix so a leaked secret is greppable / classifiable
#: (mirrors ``sk-``/``ghp_`` conventions). NOT a secret; the entropy is the tail.
TOKEN_PREFIX = "arbor_pat_"


def generate_token() -> str:
    """Mint a fresh opaque token secret: ``arbor_pat_<43 url-safe chars>`` (256
    bits of entropy). This is the ONLY value the user ever sees; Arbor stores
    only its hash (:func:`hash_token`)."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(plaintext: str, secret: Optional[str] = None) -> str:
    """Return the hex digest Arbor persists for ``plaintext``.

    With ``secret`` (the site's own key) this is a keyed HMAC-SHA256 so a DB
    leak alone cannot reverse or forge tokens; without it, a plain SHA-256 (the
    token already carries full entropy). The adapter always passes the site
    secret; the parameter is optional only to keep this callable bench-free.
    """
    data = plaintext.encode("utf-8")
    if secret:
        return hmac.new(secret.encode("utf-8"), data, hashlib.sha256).hexdigest()
    return hashlib.sha256(data).hexdigest()


def verify_token(plaintext: str, stored_hash: str, secret: Optional[str] = None) -> bool:
    """Constant-time check that ``plaintext`` hashes to ``stored_hash``."""
    return hmac.compare_digest(hash_token(plaintext, secret), stored_hash or "")
