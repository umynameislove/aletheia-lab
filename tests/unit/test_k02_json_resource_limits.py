"""JSON resource limits using a transport-only model, independent of J01."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ConfigDict

from aletheia_lab.hosted.request_boundary import RequestRejected, decode_json_model


class _Envelope(BaseModel):
    """Synthetic test schema; not an API DTO or product contract."""

    model_config = ConfigDict(extra="forbid", strict=True)
    payload: object


def test_depth_limit_counts_root_object_and_nested_arrays():
    body = b'{"payload":[[[null]]]}'
    value = decode_json_model(body, _Envelope, maximum_bytes=1024, maximum_depth=4)
    assert value.payload == [[[None]]]
    with pytest.raises(RequestRejected) as caught:
        decode_json_model(body, _Envelope, maximum_bytes=1024, maximum_depth=3)
    assert (caught.value.status, caught.value.code) == (400, "json_too_deep")


def test_depth_limit_counts_objects_inside_arrays():
    body = b'{"payload":[{"nested":{}}]}'
    assert decode_json_model(body, _Envelope, maximum_bytes=1024, maximum_depth=4).payload == [{"nested": {}}]
    with pytest.raises(RequestRejected) as caught:
        decode_json_model(body, _Envelope, maximum_bytes=1024, maximum_depth=3)
    assert caught.value.code == "json_too_deep"


def test_small_but_deep_payload_is_rejected_by_the_depth_budget():
    body = b'{"payload":' + b"[" * 300 + b"null" + b"]" * 300 + b"}"
    assert len(body) < 1024
    with pytest.raises(RequestRejected) as caught:
        decode_json_model(body, _Envelope, maximum_bytes=1024)
    assert caught.value.code == "json_too_deep"


@pytest.mark.parametrize("text", [
    "[" * 200 + "{" * 200,
    'quoted "[{]}" value',
    "backslash \\ then a quote \" and braces {[]}",
    "Tiếng Việt: Kiên, dữ liệu {mẫu} [tổng hợp]",
    "\\" * 4 + '"[{',
    "\\" * 3 + '"[{',
])
def test_brackets_quotes_and_escapes_inside_strings_do_not_add_depth(text):
    body = json.dumps({"payload": text}, ensure_ascii=False).encode("utf-8")
    value = decode_json_model(body, _Envelope, maximum_bytes=4096, maximum_depth=1)
    assert value.payload == text


def test_wide_shallow_data_is_not_confused_with_deep_data():
    source = {str(i): i for i in range(100)}
    body = json.dumps({"payload": source}).encode()
    value = decode_json_model(body, _Envelope, maximum_bytes=4096, maximum_depth=2)
    assert value.payload == source


@pytest.mark.parametrize("maximum_depth", [0, -1, True, 1.5, "64"])
def test_depth_configuration_requires_a_positive_integer(maximum_depth):
    with pytest.raises(ValueError, match="maximum_depth"):
        decode_json_model(b'{"payload":null}', _Envelope, maximum_bytes=1024, maximum_depth=maximum_depth)


def test_byte_budget_still_takes_priority_over_the_depth_budget():
    with pytest.raises(RequestRejected) as caught:
        decode_json_model(b'{"payload":[[null]]}', _Envelope, maximum_bytes=2, maximum_depth=1)
    assert (caught.value.status, caught.value.code) == (413, "body_too_large")


@pytest.mark.parametrize("body", [
    b'{"payload":"unterminated [{',
    b'{"payload":[}',
    b'{"payload":1} trailing',
])
def test_depth_scan_does_not_replace_json_syntax_validation(body):
    with pytest.raises(RequestRejected) as caught:
        decode_json_model(body, _Envelope, maximum_bytes=1024)
    assert caught.value.code == "invalid_json"
