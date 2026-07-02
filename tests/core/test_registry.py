"""Registry + getLLMTools filtering + schema validation (CAPABILITIES.md)."""

from __future__ import annotations

import pytest

from arbor.core.registry import (
    all_capabilities,
    get_capability,
    get_llm_tools,
    has_capability,
)
from arbor.core.schema import validate_schema
from arbor.core.types import SchemaValidationError, UnknownCapabilityError

EXPECTED_IDS = {
    "getSheetSnapshot",
    "getSheetOverview",
    "listChildren",
    "getSubtree",
    "getNode",
    "searchNodes",
    "getCells",
    "addNode",
    "updateCell",
    "moveNode",
    "deleteNode",
    "addColumn",
    "updateColumn",
    "deleteColumn",
    "suggestChange",
    "suggestChanges",
    "approveChange",
    "rejectChange",
    "withdrawChange",
    "subscribe",
    "unsubscribe",
    "acknowledge",
    "delegateBranch",
    "revokeDelegation",
    "grantColumn",
    "internalReset",
    # role management (Feature: roles)
    "assignRole",
    "revokeRole",
    "applyForRole",
    "approveRoleApplication",
    "rejectRoleApplication",
    "withdrawRoleApplication",
    # impersonation (Area 1)
    "beginImpersonation",
    "endImpersonation",
    # process / SLA (Area 3)
    "defineProcess",
    "enableProcess",
    "disableProcess",
    "startProcessRun",
}

# Capabilities hidden from the LLM agent: internalReset + the privilege-granting
# role admin/decision caps (the agent must never self-escalate or approve a role).
# applyForRole + withdrawRoleApplication stay exposed (they still need approval).
LLM_HIDDEN = {
    "internalReset",
    "assignRole",
    "revokeRole",
    "approveRoleApplication",
    "rejectRoleApplication",
    # impersonation is human-admin-surface only; the agent must NEVER impersonate.
    "beginImpersonation",
    "endImpersonation",
}


def test_all_capabilities_registered():
    ids = {c.id for c in all_capabilities()}
    assert ids == EXPECTED_IDS
    assert len(all_capabilities()) == 38


def test_unknown_capability_raises():
    with pytest.raises(UnknownCapabilityError):
        get_capability("frobnicate")
    assert has_capability("addNode")
    assert not has_capability("frobnicate")


def test_internal_reset_hidden_from_llm():
    tool_names = {t["function"]["name"] for t in get_llm_tools()}
    assert "internalReset" not in tool_names
    # every non-privilege-granting capability is exposed.
    assert tool_names == EXPECTED_IDS - LLM_HIDDEN
    assert len(get_llm_tools()) == len(EXPECTED_IDS) - len(LLM_HIDDEN)


def test_llm_tool_shape():
    tools = get_llm_tools()
    one = next(t for t in tools if t["function"]["name"] == "updateCell")
    assert one["type"] == "function"
    assert one["function"]["parameters"]["required"] == ["sheet", "node", "column", "value"]


def test_schema_validation_missing_required():
    cap = get_capability("addNode")
    with pytest.raises(SchemaValidationError):
        validate_schema({"sheet": "S"}, cap.params_schema)  # missing parent


def test_schema_validation_enum_and_type():
    cap = get_capability("addColumn")
    with pytest.raises(SchemaValidationError):
        validate_schema(
            {"sheet": "S", "field": "f", "label": "L", "type": "bogus"},
            cap.params_schema,
        )
    # valid passes
    validate_schema(
        {"sheet": "S", "field": "f", "label": "L", "type": "number"},
        cap.params_schema,
    )


def test_schema_null_union_accepted():
    cap = get_capability("addNode")
    # parent may be null
    validate_schema({"sheet": "S", "parent": None}, cap.params_schema)


def test_schema_bool_not_number():
    cap = get_capability("deleteNode")
    # cascade is boolean; passing a real bool is fine
    validate_schema({"sheet": "S", "node": "n", "cascade": True}, cap.params_schema)


# --- impersonation caps (Area 1) -------------------------------------------
def test_impersonation_caps_are_admin_only_non_llm_no_event():
    from arbor.core.types import Axis, Operation, TargetKind

    for cid in ("beginImpersonation", "endImpersonation"):
        cap = get_capability(cid)
        assert cap.is_exposed_to_llm is False  # agent must never impersonate
        assert cap.emits == ()  # the session row is the record, no Tree Event
        assert cap.axis == Axis.NONE
        assert cap.target_kind == TargetKind.NONE
        assert cap.operation == Operation.NONE
    # begin requires impersonated_user; end takes no required params.
    with pytest.raises(SchemaValidationError):
        validate_schema({}, get_capability("beginImpersonation").params_schema)
    validate_schema({}, get_capability("endImpersonation").params_schema)


# --- process caps (Area 3) --------------------------------------------------
def test_process_caps_are_meta_llm_exposed_emit_column_config():
    from arbor.core.types import Axis

    for cid in ("defineProcess", "enableProcess", "disableProcess", "startProcessRun"):
        cap = get_capability(cid)
        assert cap.axis == Axis.META
        assert cap.is_exposed_to_llm is True
        assert cap.emits == ("COLUMN_CONFIG_UPDATED",)  # reuse the closed event set
    # defineProcess requires sheet + stages.
    with pytest.raises(SchemaValidationError):
        validate_schema({"sheet": "S"}, get_capability("defineProcess").params_schema)
    validate_schema(
        {"sheet": "S", "stages": [{"column": "c1"}, {"column": "c2", "sla_seconds": 60}]},
        get_capability("defineProcess").params_schema,
    )


def test_event_types_closed_set_unchanged():
    """No workstream may extend the closed 11-EventType set."""
    from arbor.core.types import EVENT_TYPES

    assert len(EVENT_TYPES) == 11
    assert set(EVENT_TYPES) == {
        "NODE_CREATED",
        "NODE_DELETED",
        "NODE_MOVED",
        "NODE_VALUE_UPDATED",
        "COLUMN_CONFIG_UPDATED",
        "CHANGE_PROPOSED",
        "CHANGE_APPROVED",
        "CHANGE_REJECTED",
        "SUBSCRIPTION_CHANGED",
        "DELEGATION_CHANGED",
        "IMPORT_COMPLETED",
    }
