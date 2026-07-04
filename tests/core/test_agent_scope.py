"""Unit tests for the external-agent token scope brain (bench-free).

Covers the read/write mode gate, the sheet restriction, the exposed-only rule,
the read-capability allowlist drift-guard, and the token secret math.
"""

from __future__ import annotations

import pytest

from arbor.core.agent_scope import (
    READ_ONLY_CAPABILITY_IDS,
    TOKEN_PREFIX,
    AgentScope,
    ScopeError,
    authorize_scope,
    generate_token,
    hash_token,
    is_read_capability,
    verify_token,
)
from arbor.core.registry import all_capabilities, get_capability
from arbor.core.types import Axis, Operation, TargetKind


# --- read-only allowlist drift-guard --------------------------------------
def test_read_only_ids_all_exist_and_are_truly_non_mutating():
    for cid in READ_ONLY_CAPABILITY_IDS:
        cap = get_capability(cid)  # raises if it vanished from the registry
        assert cap.axis is Axis.NONE, cid
        assert cap.operation is Operation.NONE, cid
        assert cap.target_kind is TargetKind.NONE, cid
        assert cap.emits == (), cid
        assert cap.handler is None, cid
        assert cap.is_exposed_to_llm, cid  # every read is available to agents


def test_acknowledge_is_deliberately_excluded_from_reads():
    # acknowledge "looks like" a read (handler=None, emits=()) but WRITES an
    # Acknowledgement row — the explicit allowlist must never admit it.
    assert "acknowledge" not in READ_ONLY_CAPABILITY_IDS
    assert not is_read_capability("acknowledge")


def test_every_read_shaped_control_cap_is_classified():
    # Guard against a future non-mutating-shaped cap silently becoming callable by
    # a read token: any cap matching the "read shape" must be either in the
    # allowlist or a known state-changing exception.
    # acknowledge writes an Acknowledgement row; begin/endImpersonation are
    # control caps whose audit record is the Impersonation Session row (so they
    # emit nothing). All three are state-changing despite the read shape.
    known_writes_with_read_shape = {"acknowledge", "beginImpersonation", "endImpersonation"}
    for cap in all_capabilities():
        read_shaped = (
            cap.axis is Axis.NONE
            and cap.operation is Operation.NONE
            and cap.target_kind is TargetKind.NONE
            and cap.emits == ()
            and cap.handler is None
        )
        if read_shaped and cap.id not in READ_ONLY_CAPABILITY_IDS:
            assert cap.id in known_writes_with_read_shape, (
                f"{cap.id} has a read shape but is neither allowlisted nor a "
                "documented state-changing exception"
            )


# --- mode gate -------------------------------------------------------------
def test_read_token_allows_reads():
    scope = AgentScope(mode="read")
    authorize_scope(scope, "getSheetDefinition", {"sheet": "s"})  # no raise


def test_read_token_rejects_writes():
    scope = AgentScope(mode="read")
    with pytest.raises(ScopeError):
        authorize_scope(scope, "updateCell", {"sheet": "s", "node": "n", "column": "c", "value": 1})


def test_write_token_allows_writes():
    scope = AgentScope(mode="write")
    authorize_scope(scope, "updateCell", {"sheet": "s", "node": "n", "column": "c", "value": 1})


# --- exposed-only rule -----------------------------------------------------
@pytest.mark.parametrize("hidden", ["internalReset", "beginImpersonation", "assignRole"])
def test_hidden_capabilities_are_never_allowed(hidden):
    with pytest.raises(ScopeError):
        authorize_scope(AgentScope(mode="write"), hidden, {"sheet": "s"})


def test_unknown_capability_raises():
    with pytest.raises(ScopeError):
        authorize_scope(AgentScope(mode="write"), "noSuchThing", {})


# --- sheet restriction -----------------------------------------------------
def test_sheet_scoped_token_allows_its_sheet():
    scope = AgentScope(mode="write", sheets=frozenset({"acme"}))
    authorize_scope(scope, "getSheetDefinition", {"sheet": "acme"})


def test_sheet_scoped_token_denies_other_sheet():
    scope = AgentScope(mode="write", sheets=frozenset({"acme"}))
    with pytest.raises(ScopeError):
        authorize_scope(scope, "getSheetDefinition", {"sheet": "other"})


def test_sheet_scoped_token_denies_account_level_action():
    # createSheet carries no `sheet` param → out of scope for a sheet-bound token.
    scope = AgentScope(mode="write", sheets=frozenset({"acme"}))
    with pytest.raises(ScopeError):
        authorize_scope(scope, "createSheet", {"title": "New"})


def test_unrestricted_sheets_allows_account_level_action():
    authorize_scope(AgentScope(mode="write", sheets=None), "createSheet", {"title": "New"})


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        AgentScope(mode="admin")  # type: ignore[arg-type]


def test_sheets_iterable_normalized_to_frozenset():
    scope = AgentScope(mode="read", sheets={"a", "b"})
    assert scope.sheets == frozenset({"a", "b"})


# --- token secret math -----------------------------------------------------
def test_generate_token_shape_and_uniqueness():
    a, b = generate_token(), generate_token()
    assert a.startswith(TOKEN_PREFIX) and b.startswith(TOKEN_PREFIX)
    assert a != b
    assert len(a) > len(TOKEN_PREFIX) + 20  # meaningful entropy tail


def test_hash_is_deterministic_and_verify_roundtrips():
    tok = generate_token()
    h = hash_token(tok)
    assert hash_token(tok) == h
    assert verify_token(tok, h)
    assert not verify_token(tok + "x", h)
    assert not verify_token(tok, "")


def test_keyed_hash_differs_from_plain_and_from_other_key():
    tok = generate_token()
    assert hash_token(tok) != hash_token(tok, secret="site-secret")
    assert hash_token(tok, secret="k1") != hash_token(tok, secret="k2")
    assert verify_token(tok, hash_token(tok, secret="k1"), secret="k1")
    assert not verify_token(tok, hash_token(tok, secret="k1"), secret="k2")
