"""Minimal, dependency-free JSON-schema validation.

The capability ``params_schema`` records (CAPABILITIES.md) use a small subset of
JSON-schema: ``type`` (incl. union lists and ``"null"``), ``required``,
``properties``, ``enum``, ``const``, ``items``. We validate exactly that subset
with no third-party dependency so the core stays import-clean.
"""

from __future__ import annotations

from typing import Any

from .types import SchemaValidationError

_PY_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
    "null": (type(None),),
}


def _matches_type(value: Any, type_spec: Any) -> bool:
    types = type_spec if isinstance(type_spec, list) else [type_spec]
    for t in types:
        py = _PY_TYPES.get(t)
        if py is None:
            continue
        # bool is a subclass of int — keep them distinct.
        if t in ("number", "integer") and isinstance(value, bool):
            continue
        if t == "boolean" and not isinstance(value, bool):
            continue
        if isinstance(value, py):
            return True
    return False


def validate_schema(params: dict[str, Any], schema: dict[str, Any]) -> None:
    """Raise :class:`SchemaValidationError` if ``params`` violate ``schema``."""
    if not isinstance(params, dict):
        raise SchemaValidationError("params must be an object")

    for key in schema.get("required", []):
        if key not in params:
            raise SchemaValidationError(f"missing required field: {key!r}")

    required = set(schema.get("required", []))
    props: dict[str, Any] = schema.get("properties", {})
    for key, value in params.items():
        prop = props.get(key)
        if prop is None:
            continue  # extra keys tolerated (forward-compat)

        # A None for a non-required field means "not provided" (e.g. subscribe's
        # ``subscriber`` defaults to the actor). Skip type/enum checks so optional
        # params may be passed explicitly as None by the surface layers.
        if value is None and key not in required:
            continue

        if "const" in prop and value != prop["const"]:
            raise SchemaValidationError(f"field {key!r} must equal {prop['const']!r}")

        if "enum" in prop and value not in prop["enum"]:
            raise SchemaValidationError(
                f"field {key!r}={value!r} not in {prop['enum']!r}"
            )

        if "type" in prop and not _matches_type(value, prop["type"]):
            raise SchemaValidationError(
                f"field {key!r}={value!r} is not of type {prop['type']!r}"
            )

        if prop.get("type") == "array" and "items" in prop:
            item_schema = prop["items"]
            for i, item in enumerate(value):
                if "type" in item_schema and not _matches_type(item, item_schema["type"]):
                    raise SchemaValidationError(
                        f"field {key!r}[{i}] is not of type {item_schema['type']!r}"
                    )
