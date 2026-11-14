"""Capacity recipe bindings and assertions; no HTTP, scheduler or outcome accounting."""

from __future__ import annotations

import re
from typing import Any

from .capacity_models import Assertion


class CorrectnessFailure(RuntimeError):
    """Fixed diagnostics only: never retain a response body or private actor values."""


def pointer(value: object, path: str) -> object:
    if path == "":
        return value
    if not path.startswith("/"):
        raise CorrectnessFailure("invalid_json_pointer")
    for raw in path[1:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise CorrectnessFailure("missing_assertion_field")
    return value


def template(value: object, values: dict[str, object], *, depth: int = 0) -> Any:
    """Only literal JSON and exact $name references; no Python/string expression evaluation."""
    if depth > 12:
        raise CorrectnessFailure("template_depth")
    if isinstance(value, str):
        if value.startswith("$"):
            if value[1:] not in values:
                raise CorrectnessFailure("missing_template_binding")
            return values[value[1:]]
        return value
    if isinstance(value, dict):
        return {k: template(v, values, depth=depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [template(v, values, depth=depth + 1) for v in value]
    return value


def checked_path(path: str, values: dict[str, object]) -> str:
    # format_map is restricted to plain names; attribute/index/conversion specs are refused.

    for key in re.findall(r"\{([^{}]*)\}", path):
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", key) is None:
            raise CorrectnessFailure("invalid_path_template")
        value = values.get(key)
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:~-]{0,511}", value) is None
        ):
            raise CorrectnessFailure("invalid_path_binding")
    try:
        result = path.format_map(values)
    except (KeyError, ValueError):
        raise CorrectnessFailure("missing_path_binding") from None
    if (
        not result.startswith("/v1/")
        or ".." in result
        or "\\" in result
        or "#" in result
        or "://" in result
    ):
        raise CorrectnessFailure("invalid_relative_path")
    return result


def assert_json(
    value: object, assertions: tuple[Assertion, ...], bindings: dict[str, object]
) -> None:
    for rule in assertions:
        actual, expected = pointer(value, rule.pointer), template(rule.value, bindings)
        valid = False
        if rule.check == "equals":
            valid = type(actual) is type(expected) and actual == expected
        elif rule.check == "nonempty":
            valid = isinstance(actual, (str, list, dict)) and bool(actual)
        elif rule.check == "contains":
            valid = isinstance(actual, (str, list, dict)) and expected in actual
        elif rule.check == "length_le":
            valid = (
                type(expected) is int
                and isinstance(actual, (str, list, dict))
                and len(actual) <= expected
            )
        elif rule.check == "at_least":
            valid = (
                type(actual) in (int, float)
                and type(expected) in (int, float)
                and actual >= expected
            )
        if not valid:
            raise CorrectnessFailure("business_assertion_failed")
