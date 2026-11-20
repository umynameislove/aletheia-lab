"""Transport rejection checks without a server, IdP, provider, or live data."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from aletheia_lab.hosted.request_boundary import (
    BodyLimits,
    RequestRejected,
    decode_json_model,
    read_json_body,
)
from aletheia_lab.hosted_contract.session_capabilities import Capabilities, Session

ROOT = Path(__file__).resolve().parents[2]
HEADERS = [(b"content-type", b"application/json")]
LIMITS = BodyLimits(1024, 1.0, 2.0, 8)


def _collect(events, headers=HEADERS, limits=LIMITS):
    pending = iter(events)

    async def receive():
        return next(pending)

    return asyncio.run(read_json_body(receive, headers, limits))


def _event(body, more=False):
    return {"type": "http.request", "body": body, "more_body": more}


def test_collects_chunked_body_and_checks_exact_byte_limit():
    encoded = '{"name":"Kiên"}'.encode()
    headers = HEADERS + [(b"content-length", str(len(encoded)).encode())]
    limits = BodyLimits(len(encoded), 1.0, 2.0, 8)
    assert _collect([_event(encoded[:4], True), _event(encoded[4:])], headers, limits) == encoded
    with pytest.raises(RequestRejected) as caught:
        _collect([_event(encoded + b" ")], HEADERS, limits)
    assert caught.value.status == 413


def test_cumulative_bytes_without_content_length_are_bounded():
    with pytest.raises(RequestRejected) as caught:
        _collect([_event(b"123", True), _event(b"456")], limits=BodyLimits(5, 1.0, 2.0, 8))
    assert caught.value.status == 413


@pytest.mark.parametrize("content_type", [
    b"application/json; charset=utf-8",
    b'Application/JSON; Charset="UTF-8"',
])
def test_accepts_utf8_json_content_type(content_type):
    assert _collect([_event(b"{}")], [(b"Content-Type", content_type)]) == b"{}"


@pytest.mark.parametrize("headers", [
    [],
    [(b"content-type", b"text/plain")],
    HEADERS + HEADERS,
    [(b"content-type", b"application/json; charset=latin-1")],
    HEADERS + [(b"content-encoding", b"gzip")],
])
def test_rejects_media_type_before_reading_body(headers):
    with pytest.raises(RequestRejected) as caught:
        _collect([], headers)
    assert caught.value.status == 415


@pytest.mark.parametrize("values", [
    [(b"content-length", b"1025")],
    [(b"content-length", b"-1")],
    [(b"content-length", b"+2")],
    [(b"content-length", b"2"), (b"content-length", b"2")],
    [(b"content-length", b"2"), (b"transfer-encoding", b"chunked")],
])
def test_rejects_unsafe_lengths_before_reading_body(values):
    with pytest.raises(RequestRejected) as caught:
        _collect([], HEADERS + values)
    assert caught.value.status in (400, 413)


@pytest.mark.parametrize("declared", [b"1", b"3"])
def test_rejects_actual_length_mismatch(declared):
    with pytest.raises(RequestRejected) as caught:
        _collect([_event(b"{}")], HEADERS + [(b"content-length", declared)])
    assert caught.value.code == "invalid_body_length"


def test_disconnect_and_event_budget_do_not_return_partial_input():
    with pytest.raises(RequestRejected) as caught:
        _collect([_event(b'{"a":', True), {"type": "http.disconnect"}])
    assert caught.value.code == "body_incomplete"
    with pytest.raises(RequestRejected) as caught:
        _collect([_event(b"", True)] * 2, limits=BodyLimits(1024, 1.0, 2.0, 2))
    assert caught.value.code == "body_event_limit"


@pytest.mark.parametrize("idle,total", [(0.01, 1.0), (1.0, 0.01)])
def test_idle_and_total_timeout_are_both_enforced(idle, total):
    async def run():
        async def receive():
            await asyncio.Event().wait()

        return await read_json_body(receive, HEADERS, BodyLimits(1024, idle, total, 8))

    with pytest.raises(RequestRejected) as caught:
        asyncio.run(run())
    assert (caught.value.status, caught.value.code) == (408, "body_timeout")


def test_trickling_chunks_do_not_restart_the_total_deadline():
    async def run():
        async def receive():
            await asyncio.sleep(0.01)
            return _event(b" ", True)

        return await read_json_body(receive, HEADERS, BodyLimits(1024, 1.0, 0.05, 100))

    with pytest.raises(RequestRejected) as caught:
        asyncio.run(run())
    assert caught.value.code == "body_timeout"


def test_cancellation_propagates_to_the_server():
    async def run():
        async def receive():
            raise asyncio.CancelledError

        await read_json_body(receive, HEADERS, LIMITS)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())


@pytest.mark.parametrize("body", [
    b'{"a":1,"a":2}',
    b'{"a":{"key":1,"key":2}}',
    b'{"a":1,"\\u0061":2}',
    b'{"a":NaN}',
    b'{"a":Infinity}',
    b'{"a":1e999}',
    b'{"a":"\xff"}',
    b'{"a":',
    b'{} {}',
])
def test_rejects_invalid_json_before_schema_validation(body):
    with pytest.raises(RequestRejected) as caught:
        decode_json_model(body, Session, maximum_bytes=1024)
    assert caught.value.code == "invalid_json"


@pytest.mark.parametrize("body", [b"null", b"[]", b'"text"', b"42"])
def test_requires_a_json_object(body):
    with pytest.raises(RequestRejected) as caught:
        decode_json_model(body, Session, maximum_bytes=1024)
    assert caught.value.code == "invalid_json_object"


@pytest.mark.parametrize("name,model", [("session", Session), ("capabilities", Capabilities)])
def test_uses_the_existing_j01_models_and_synthetic_payloads(name, model):
    body = (ROOT / "fixtures" / "j01" / "producer-draft" / f"{name}.json").read_bytes()
    decoded = decode_json_model(body, model, maximum_bytes=10000)
    expected = json.loads(body)
    if model is Session:
        expected["can_create_workspace"] = False
    assert json.loads(decoded.model_dump_json()) == expected
    source = json.loads(body)
    source["schema_version"] = "unknown-version"
    with pytest.raises(RequestRejected) as caught:
        decode_json_model(json.dumps(source).encode(), model, maximum_bytes=10000)
    assert caught.value.code == "schema_invalid"


def test_rejects_extra_fields_and_does_not_echo_private_values():
    source = json.loads((ROOT / "fixtures" / "j01" / "producer-draft" / "session.json").read_bytes())
    source["private_source"] = "C:/private/example"
    source["credential"] = "synthetic-sensitive-value"
    body = json.dumps(source).encode()
    with pytest.raises(RequestRejected) as caught:
        decode_json_model(body, Session, maximum_bytes=10000)
    assert caught.value.status == 422
    assert "private" not in str(caught.value)
    assert "synthetic-sensitive-value" not in str(caught.value)


def test_model_boundary_enforces_its_own_size_budget():
    with pytest.raises(RequestRejected) as caught:
        decode_json_model(b"{} ", Session, maximum_bytes=2)
    assert caught.value.status == 413


@pytest.mark.parametrize("changes", [
    {"maximum_bytes": 0},
    {"maximum_bytes": True},
    {"maximum_events": 0},
    {"idle_timeout_seconds": float("nan")},
    {"total_timeout_seconds": float("inf")},
])
def test_rejects_unbounded_or_invalid_configuration(changes):
    source = dict(maximum_bytes=1024, idle_timeout_seconds=1.0, total_timeout_seconds=2.0, maximum_events=8)
    source.update(changes)
    with pytest.raises(ValueError):
        BodyLimits(**source)
