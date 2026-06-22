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
}


def test_all_26_capabilities_registered():
    ids = {c.id for c in all_capabilities()}
    assert ids == EXPECTED_IDS
    assert len(all_capabilities()) == 26


def test_unknown_capability_raises():
    with pytest.raises(UnknownCapabilityError):
        get_capability("frobnicate")
    assert has_capability("addNode")
    assert not has_capability("frobnicate")


def test_internal_reset_hidden_from_llm():
    tool_names = {t["function"]["name"] for t in get_llm_tools()}
    assert "internalReset" not in tool_names
    # every OTHER capability is exposed.
    assert tool_names == EXPECTED_IDS - {"internalReset"}
    assert len(get_llm_tools()) == 25


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
