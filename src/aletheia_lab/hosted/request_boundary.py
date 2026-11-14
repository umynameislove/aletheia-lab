"""Bounded ASGI body collection and strict JSON/model validation.

These helpers do not expose routes, authenticate requests, or run domain work.
The HTTP adapter must map RequestRejected through the shared Problem model.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError

Model = TypeVar("Model", bound=BaseModel)
Receive = Callable[[], Awaitable[Mapping[str, object]]]


class RequestRejected(ValueError):
    """An internal rejection, with no input or parser diagnostics in its text."""

    def __init__(self, status: int, code: str, safe_message: str) -> None:
        self.status = status
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


@dataclass(frozen=True)
class BodyLimits:
    """Explicit adapter configuration; no implicit production policy."""

    maximum_bytes: int
    idle_timeout_seconds: float
    total_timeout_seconds: float
    maximum_events: int

    def __post_init__(self) -> None:
        for value in (self.maximum_bytes, self.maximum_events):
            if type(value) is not int or value <= 0:
                raise ValueError("body size and event limits must be positive integers")
        for timeout in (self.idle_timeout_seconds, self.total_timeout_seconds):
            if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("body timeouts must be finite and positive")


def _header_values(headers: Sequence[tuple[bytes, bytes]], name: bytes) -> list[bytes]:
    return [value.strip() for key, value in headers if key.lower() == name]


def _declared_length(headers: Sequence[tuple[bytes, bytes]], maximum: int) -> int | None:
    lengths = _header_values(headers, b"content-length")
    if not lengths:
        return None
    if (
        len(lengths) != 1
        or not lengths[0]
        or not lengths[0].isdigit()
        or len(lengths[0]) > 20
        or _header_values(headers, b"transfer-encoding")
    ):
        raise RequestRejected(400, "invalid_body_length", "Request body length is invalid.")
    length = int(lengths[0])
    if length > maximum:
        raise RequestRejected(413, "body_too_large", "Request body exceeds the configured limit.")
    return length


def _require_json_headers(headers: Sequence[tuple[bytes, bytes]]) -> None:
    content_types = _header_values(headers, b"content-type")
    encodings = _header_values(headers, b"content-encoding")
    supported = encodings in ([], [b"identity"])
    if len(content_types) != 1 or not supported:
        raise RequestRejected(415, "unsupported_media_type", "A UTF-8 JSON body is required.")
    parts = [part.strip().lower() for part in content_types[0].split(b";")]
    if parts[0] != b"application/json" or (
        len(parts) > 1 and parts[1:] not in ([b"charset=utf-8"], [b'charset="utf-8"'])
    ):
        raise RequestRejected(415, "unsupported_media_type", "A UTF-8 JSON body is required.")


async def read_json_body(
    receive: Receive,
    headers: Sequence[tuple[bytes, bytes]],
    limits: BodyLimits,
) -> bytes:
    """Read ASGI request events under idle, total, byte and event budgets.

    The production ASGI server and edge still own header timeouts, framing,
    connection limits and what happens to a rejected connection's unread body.
    """
    _require_json_headers(headers)
    return await _collect_body(receive, headers, limits)


async def read_zip_body(receive: Receive, headers: Sequence[tuple[bytes, bytes]], limits: BodyLimits) -> bytes:
    if (_header_values(headers, b"content-type") != [b"application/zip"]
            or _header_values(headers, b"content-encoding") not in ([], [b"identity"])):
        raise RequestRejected(415, "unsupported_media_type", "A ZIP body is required.")
    return await _collect_body(receive, headers, limits)


async def _collect_body(receive: Receive, headers: Sequence[tuple[bytes, bytes]], limits: BodyLimits) -> bytes:
    declared = _declared_length(headers, limits.maximum_bytes)
    chunks: list[bytes] = []
    size = 0
    try:
        async with asyncio.timeout(limits.total_timeout_seconds):
            for _ in range(limits.maximum_events):
                async with asyncio.timeout(limits.idle_timeout_seconds):
                    event = await receive()
                if event.get("type") == "http.disconnect":
                    raise RequestRejected(400, "body_incomplete", "Request body was not completed.")
                body = event.get("body", b"")
                more = event.get("more_body", False)
                if event.get("type") != "http.request" or not isinstance(body, bytes) or type(more) is not bool:
                    raise RequestRejected(400, "invalid_body_event", "Request body is invalid.")
                size += len(body)
                if size > limits.maximum_bytes:
                    raise RequestRejected(413, "body_too_large", "Request body exceeds the configured limit.")
                if declared is not None and size > declared:
                    raise RequestRejected(400, "invalid_body_length", "Request body length is invalid.")
                chunks.append(body)
                if not more:
                    if declared is not None and size != declared:
                        raise RequestRejected(400, "invalid_body_length", "Request body length is invalid.")
                    return b"".join(chunks)
    except TimeoutError:
        raise RequestRejected(408, "body_timeout", "Request body was not completed in time.") from None
    raise RequestRejected(413, "body_event_limit", "Request body exceeds the configured event limit.")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    if value == 0.0 and any(digit in "123456789" for digit in text.partition("e")[0].partition("E")[0]):
        raise ValueError("nonzero number underflow")
    return value


def _invalid_constant(text: str) -> object:
    raise ValueError("non-JSON numeric constant")


def _check_json_depth(body: bytes, maximum_depth: int) -> None:
    """Bound structural depth before recursive decoders; strings stay opaque."""
    depth = 0
    quoted = False
    escaped = False
    for char in body:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):
            depth += 1
            if depth > maximum_depth:
                raise RequestRejected(400, "json_too_deep", "JSON nesting exceeds the configured limit.")
        elif char in (93, 125):
            depth -= 1


def decode_json_model(
    body: bytes,
    model: type[Model],
    *,
    maximum_bytes: int,
    maximum_depth: int = 64,
) -> Model:
    """Check JSON syntax, then delegate shape/version/types to one producer model.

    JSON-mode model validation preserves strict Pydantic datetime handling.
    Error text deliberately omits field values and validation diagnostics.
    """
    if type(maximum_bytes) is not int or maximum_bytes <= 0:
        raise ValueError("maximum_bytes must be a positive integer")
    if type(maximum_depth) is not int or maximum_depth <= 0:
        raise ValueError("maximum_depth must be a positive integer")
    if len(body) > maximum_bytes:
        raise RequestRejected(413, "body_too_large", "Request body exceeds the configured limit.")
    _check_json_depth(body, maximum_depth)
    try:
        text = body.decode("utf-8", errors="strict")
        parsed = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_float=_finite_float,
            parse_constant=_invalid_constant,
        )
    except (UnicodeError, ValueError, RecursionError):
        raise RequestRejected(400, "invalid_json", "Request body must be valid UTF-8 JSON.") from None
    if not isinstance(parsed, dict):
        raise RequestRejected(400, "invalid_json_object", "Request body must be a JSON object.")
    try:
        return model.model_validate_json(text)
    except (ValidationError, RecursionError):
        raise RequestRejected(422, "schema_invalid", "Request body does not match the operation schema.") from None
