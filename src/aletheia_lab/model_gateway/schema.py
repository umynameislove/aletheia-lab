"""Closed JSON-Schema subset shared by gateway preparation and parsing."""

from __future__ import annotations

import json
import re
from typing import cast

from aletheia_lab.model_gateway.contracts import GatewayContractError


def _validate_schema_shape(schema: dict[str, object]) -> None:
    if schema.get("type") != "object":
        raise GatewayContractError("response schema must describe one JSON object")
    _validate_json_schema_node(schema, location="$", depth=0)


def validate_response_schema(schema: dict[str, object]) -> None:
    """Validate one response schema against the gateway's closed safe subset."""

    _validate_schema_shape(schema)


def validate_response_payload(payload: dict[str, object], schema: dict[str, object]) -> None:
    """Apply the same closed schema interpreter used at the provider boundary."""

    _validate_schema_shape(schema)
    _validate_json_value(payload, schema)


def _validate_json_schema_node(
    schema: dict[str, object],
    *,
    location: str,
    depth: int,
) -> None:
    """Validate the Structured Outputs constraints exercised by this project."""

    if depth > 8:
        raise GatewayContractError("response schema nesting exceeds the gateway limit")
    allowed_fields = {
        "additionalProperties",
        "anyOf",
        "const",
        "enum",
        "items",
        "maxItems",
        "minItems",
        "pattern",
        "properties",
        "required",
        "type",
    }
    if any(key not in allowed_fields for key in schema):
        raise GatewayContractError(f"response schema uses an unsupported constraint at {location}")
    supported_types = {
        "array",
        "boolean",
        "integer",
        "null",
        "number",
        "object",
        "string",
    }
    if "anyOf" in schema:
        if set(schema) != {"anyOf"}:
            raise GatewayContractError("response schema anyOf cannot carry sibling constraints")
        alternatives = schema["anyOf"]
        if (
            not isinstance(alternatives, list)
            or not 2 <= len(alternatives) <= 8
            or not all(isinstance(item, dict) for item in alternatives)
        ):
            raise GatewayContractError("response schema anyOf requires two to eight schema objects")
        for index, alternative in enumerate(alternatives):
            _validate_json_schema_node(
                cast(dict[str, object], alternative),
                location=f"{location}.anyOf[{index}]",
                depth=depth + 1,
            )
        return

    schema_type = schema.get("type")
    if not isinstance(schema_type, str) or schema_type not in supported_types:
        raise GatewayContractError("response schema property type is unsupported")

    if "const" in schema and not _is_json_scalar(schema["const"]):
        raise GatewayContractError("response schema const must be a finite JSON scalar")
    if "enum" in schema:
        enum = schema["enum"]
        if (
            not isinstance(enum, list)
            or not enum
            or len(enum) != len({_canonical_scalar(value) for value in enum})
            or any(not _is_json_scalar(value) for value in enum)
        ):
            raise GatewayContractError("response schema enum must contain unique JSON scalars")
        if any(not _json_type_matches(value, schema_type) for value in enum):
            raise GatewayContractError("response schema enum value contradicts its type")
    if "const" in schema and not _json_type_matches(schema["const"], schema_type):
        raise GatewayContractError("response schema const contradicts its type")

    object_fields = {"additionalProperties", "properties", "required"}
    array_fields = {"items", "maxItems", "minItems"}
    if schema_type != "object" and any(field in schema for field in object_fields):
        raise GatewayContractError("response schema object constraints require object type")
    if schema_type != "array" and any(field in schema for field in array_fields):
        raise GatewayContractError("response schema items require array type")
    if schema_type != "string" and "pattern" in schema:
        raise GatewayContractError("response schema pattern requires string type")

    if schema_type == "string" and "pattern" in schema:
        pattern = schema["pattern"]
        if not isinstance(pattern, str) or not pattern or len(pattern) > 512:
            raise GatewayContractError("response schema pattern is invalid")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise GatewayContractError("response schema pattern is invalid") from exc

    if schema_type == "object":
        _validate_object_constraints(schema, location=location, depth=depth)
    elif schema_type == "array":
        _validate_array_constraints(schema, location=location, depth=depth)


def _validate_object_constraints(schema: dict[str, object], *, location: str, depth: int) -> None:
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    if (
        not isinstance(required, list)
        or not all(isinstance(value, str) for value in required)
        or len(required) != len(set(required))
        or not isinstance(properties, dict)
        or not all(
            isinstance(key, str) and key and isinstance(value, dict)
            for key, value in properties.items()
        )
    ):
        raise GatewayContractError("response schema has invalid object constraints")
    if any(value not in properties for value in required):
        raise GatewayContractError("response schema required fields lack property definitions")
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        raise GatewayContractError("response schema additionalProperties must be boolean")
    for key, child in properties.items():
        _validate_json_schema_node(
            cast(dict[str, object], child),
            location=f"{location}.{key}",
            depth=depth + 1,
        )


def _validate_array_constraints(schema: dict[str, object], *, location: str, depth: int) -> None:
    items = schema.get("items")
    if not isinstance(items, dict):
        raise GatewayContractError("response schema array requires one item schema")
    minimum = schema.get("minItems", 0)
    maximum = schema.get("maxItems")
    if (
        not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or minimum < 0
        or (
            "maxItems" in schema
            and (not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < minimum)
        )
    ):
        raise GatewayContractError("response schema array bounds are invalid")
    _validate_json_schema_node(
        cast(dict[str, object], items),
        location=f"{location}[]",
        depth=depth + 1,
    )


def _validate_json_value(value: object, schema: dict[str, object]) -> None:
    if "anyOf" in schema:
        alternatives = cast(list[dict[str, object]], schema["anyOf"])
        for alternative in alternatives:
            try:
                _validate_json_value(value, alternative)
            except GatewayContractError:
                continue
            return
        raise GatewayContractError("provider response field matches no anyOf branch")

    schema_type = cast(str, schema["type"])
    if not _json_type_matches(value, schema_type):
        raise GatewayContractError("provider response field does not match schema type")
    if "const" in schema and not _json_scalar_equal(value, schema["const"]):
        raise GatewayContractError("provider response field does not match schema const")
    if "enum" in schema and not any(
        _json_scalar_equal(value, candidate) for candidate in cast(list[object], schema["enum"])
    ):
        raise GatewayContractError("provider response field does not match schema enum")
    if (
        schema_type == "string"
        and "pattern" in schema
        and re.search(cast(str, schema["pattern"]), cast(str, value)) is None
    ):
        raise GatewayContractError("provider response string does not match pattern")

    if schema_type == "object":
        _validate_object_value(cast(dict[str, object], value), schema)
    elif schema_type == "array":
        _validate_array_value(cast(list[object], value), schema)


def _validate_object_value(value: dict[str, object], schema: dict[str, object]) -> None:
    properties = cast(dict[str, dict[str, object]], schema.get("properties", {}))
    required = cast(list[str], schema.get("required", []))
    if any(key not in value for key in required):
        raise GatewayContractError("provider response is missing a required field")
    if schema.get("additionalProperties") is False and any(key not in properties for key in value):
        raise GatewayContractError("provider response contains an unknown field")
    for key, child_value in value.items():
        child_schema = properties.get(key)
        if child_schema is not None:
            _validate_json_value(child_value, child_schema)


def _validate_array_value(value: list[object], schema: dict[str, object]) -> None:
    if len(value) < cast(int, schema.get("minItems", 0)):
        raise GatewayContractError("provider response array is shorter than schema")
    maximum = schema.get("maxItems")
    if maximum is not None and len(value) > cast(int, maximum):
        raise GatewayContractError("provider response array is longer than schema")
    items = cast(dict[str, object], schema["items"])
    for item in value:
        _validate_json_value(item, items)


def _json_type_matches(value: object, schema_type: str) -> bool:
    expected_python_types: dict[str, type[object]] = {
        "array": list,
        "boolean": bool,
        "integer": int,
        "object": dict,
        "string": str,
    }
    if schema_type == "null":
        return value is None
    if schema_type == "number":
        return (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and (not isinstance(value, float) or value == value)
            and value not in {float("inf"), float("-inf")}
        )
    expected = expected_python_types.get(schema_type)
    if expected is None or not isinstance(value, expected):
        return False
    return schema_type != "integer" or not isinstance(value, bool)


def _is_json_scalar(value: object) -> bool:
    return (
        value is None
        or isinstance(value, str | bool | int)
        or (
            isinstance(value, float)
            and value == value
            and value not in {float("inf"), float("-inf")}
        )
    )


def _canonical_scalar(value: object) -> str:
    if not _is_json_scalar(value):
        raise GatewayContractError("response schema enum contains a non-scalar value")
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _json_scalar_equal(left: object, right: object) -> bool:
    return type(left) is type(right) and left == right


__all__ = ["validate_response_payload", "validate_response_schema"]
