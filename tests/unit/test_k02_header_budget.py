"""Reject excessive ASGI headers before routes or body collection."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from pydantic import ValidationError

from aletheia_lab.hosted.composition import ProcessApplication
from aletheia_lab.hosted.settings import RuntimeSettings

HOST = (b"host", b"app.example.invalid")
PRIVATE = b"private-header-value-do-not-reflect"


def _settings(tmp_path, **api):
    return RuntimeSettings.model_validate({
        "environment": "local", "public_origin": "https://app.example.invalid",
        "identity": {"issuer": "https://identity.example.invalid", "client_id": "synthetic",
                     "redirect_uri": "https://app.example.invalid/callback", "client_kind": "public"},
        "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)},
        "api": api,
    })


class _Backend:
    async def is_ready(self):
        return True


async def _http(app, calls, method, path, headers):
    messages = []

    async def receive():
        calls.append("body")
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(event):
        messages.append(event)

    await app({"type": "http", "scheme": "https", "method": method, "path": path,
               "headers": headers}, receive, send)
    return messages


def _exercise(tmp_path, limits, requests):
    async def run():
        calls, output, responses = [], [], []

        @asynccontextmanager
        async def resources():
            calls.append("open")
            try:
                yield _Backend()
            finally:
                calls.append("close")

        def routes(fallback):
            async def route(scope, receive, send):
                if scope["path"] != "/effect":
                    return await fallback(scope, receive, send)
                await receive()
                calls.append("effect")
                await send({"type": "http.response.start", "status": 204, "headers": []})
                await send({"type": "http.response.body", "body": b""})

            return route

        app = ProcessApplication(_settings(tmp_path, **limits), resources, routes=routes)
        events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

        async def lifespan_receive():
            return next(events)

        async def lifespan_send(message):
            output.append(message["type"])
            if message["type"] != "lifespan.startup.complete":
                return
            for request in requests:
                responses.append(await _http(app, calls, *request))

        await app({"type": "lifespan"}, lifespan_receive, lifespan_send)
        assert output == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
        assert calls[0] == "open" and calls[-1] == "close"
        return calls, responses

    return asyncio.run(run())


@pytest.mark.parametrize("headers", [
    [HOST, (b"x-data", PRIVATE)],
    [HOST, (b"x-repeat", b"a"), (b"x-repeat", PRIVATE)],
])
def test_exact_header_byte_budget_is_allowed_and_next_byte_is_rejected(tmp_path, headers):
    maximum = sum(len(name) + len(value) + 4 for name, value in headers)
    excess = [*headers[:-1], (headers[-1][0], headers[-1][1] + b"x")]
    calls, responses = _exercise(tmp_path, {"maximum_header_bytes": maximum, "maximum_header_count": 100}, [
        ("GET", "/effect", headers), ("GET", "/effect", excess), ("GET", "/health/live", [HOST]),
    ])
    assert [messages[0]["status"] for messages in responses] == [204, 431, 200]
    assert calls.count("body") == calls.count("effect") == 1
    assert json.loads(responses[1][1]["body"])["code"] == "headers_too_large"
    assert PRIVATE not in responses[1][1]["body"]


def test_duplicate_fields_count_individually_before_route_and_body(tmp_path):
    allowed = [HOST, (b"x-repeat", PRIVATE)]
    rejected = [*allowed, (b"x-repeat", b"")]
    calls, responses = _exercise(tmp_path, {"maximum_header_count": 2, "maximum_header_bytes": 10000}, [
        ("GET", "/effect", allowed), ("POST", "/effect", rejected), ("GET", "/health/ready", [HOST]),
    ])
    assert [messages[0]["status"] for messages in responses] == [204, 431, 200]
    assert calls.count("body") == calls.count("effect") == 1
    start, end = responses[1]
    assert dict(start["headers"])[b"cache-control"] == b"no-store"
    value = json.loads(end["body"])
    assert value["status"] == 431 and value["code"] == "headers_too_large"
    assert value["request_id"].encode("ascii") == dict(start["headers"])[b"x-request-id"]


def test_head_rejection_has_no_body_or_effect(tmp_path):
    calls, responses = _exercise(tmp_path, {"maximum_header_count": 1}, [
        ("HEAD", "/effect", [HOST, (b"x-private", PRIVATE)]),
        ("GET", "/health/live", [HOST]),
    ])
    assert [messages[0]["status"] for messages in responses] == [431, 200]
    assert responses[0][1]["body"] == b""
    assert "body" not in calls and "effect" not in calls
    assert dict(responses[0][0]["headers"])[b"cache-control"] == b"no-store"


def test_header_rejection_send_has_deadline_without_second_response(tmp_path):
    async def run():
        @asynccontextmanager
        async def resources():
            yield _Backend()

        app = ProcessApplication(_settings(tmp_path, maximum_header_count=1,
                                           backend_timeout_seconds=0.02), resources)
        messages = []

        async def receive():
            raise AssertionError("Header rejection must not read a body")

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.start":
                await asyncio.Event().wait()

        async with asyncio.timeout(1.0):
            with pytest.raises(TimeoutError):
                await app({"type": "http", "scheme": "https", "method": "POST", "path": "/effect",
                           "headers": [HOST, (b"x-private", PRIVATE)]}, receive, send)
        assert [message["type"] for message in messages] == ["http.response.start"]
        assert messages[0]["status"] == 431

    asyncio.run(run())


@pytest.mark.parametrize("field", ["maximum_header_bytes", "maximum_header_count"])
@pytest.mark.parametrize("value", [0, -1, True])
def test_header_budgets_require_positive_integers(tmp_path, field, value):
    with pytest.raises(ValidationError):
        _settings(tmp_path, **{field: value})
