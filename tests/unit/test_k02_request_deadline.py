"""Synthetic outer request-deadline checks; no network or database."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from aletheia_lab.hosted.composition import ProcessApplication
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted_contract.session_capabilities import Problem


class _Backend:
    async def is_ready(self):
        return True

    async def load_session(self, cookie):
        raise AssertionError("Deadline tests must not authenticate")

    async def capabilities(self, session, workspace_id):
        raise AssertionError("Deadline tests must not authorize")


def _runtime(tmp_path, routes=None, *, timeout=0.02):
    settings = RuntimeSettings.model_validate({
        "environment": "local", "public_origin": "https://app.internal",
        "identity": {"issuer": "https://identity.example.invalid", "client_id": "synthetic",
                     "redirect_uri": "https://app.internal/callback", "client_kind": "public"},
        "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)},
        "api": {"request_timeout_seconds": timeout, "backend_timeout_seconds": 0.05,
                "startup_timeout_seconds": 0.5, "resource_close_timeout_seconds": 0.5},
    })
    calls = []

    @asynccontextmanager
    async def resources():
        calls.append("open")
        try:
            yield _Backend()
        finally:
            calls.append("close")

    return ProcessApplication(settings, resources, routes=routes), calls


@asynccontextmanager
async def _started(app):
    incoming = asyncio.Queue()
    incoming.put_nowait({"type": "lifespan.startup"})
    started = asyncio.Event()
    messages = []

    async def send(message):
        messages.append(message["type"])
        if message["type"] == "lifespan.startup.complete":
            started.set()

    lifetime = asyncio.create_task(app({"type": "lifespan"}, incoming.get, send))
    try:
        await asyncio.wait_for(started.wait(), 1)
        yield
    finally:
        incoming.put_nowait({"type": "lifespan.shutdown"})
        await asyncio.wait_for(lifetime, 1)
    assert messages == ["lifespan.startup.complete", "lifespan.shutdown.complete"]


async def _http(app, *, path="/blocked", method="GET", send=None):
    messages = []

    async def receive():
        raise AssertionError("Request must be rejected before reading a body")

    async def collect(message):
        messages.append(message)

    await app({"type": "http", "method": method, "path": path, "scheme": "https",
               "headers": [(b"host", b"app.internal")]}, receive, collect if send is None else send)
    return messages


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_stalled_guard_gets_safe_deadline_response_before_body(tmp_path, method):
    async def run():
        effects = []

        def routes(fallback):
            async def guarded(scope, receive, send):
                await asyncio.Event().wait()
                effects.append("effect")

            return guarded

        app, calls = _runtime(tmp_path, routes)
        async with _started(app):
            start, end = await asyncio.wait_for(_http(app, method=method), 1)
        assert start["status"] == 503
        headers = dict(start["headers"])
        assert headers[b"cache-control"] == b"no-store"
        assert headers[b"content-type"] == b"application/problem+json"
        assert headers[b"x-request-id"].startswith(b"req_")
        assert int(headers[b"content-length"]) > 0
        if method == "GET":
            problem = Problem.model_validate_json(end["body"])
            assert problem.code == "service_unavailable"
            assert problem.request_id.encode() == headers[b"x-request-id"]
        else:
            assert end["body"] == b""
        assert effects == [] and calls == ["open", "close"]

    asyncio.run(run())


def test_blocked_response_send_times_out_without_second_response(tmp_path):
    async def run():
        body_started = asyncio.Event()
        messages = []

        def routes(fallback):
            async def response(scope, receive, send):
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"synthetic"})

            return response

        async def slow_send(message):
            if message["type"] == "http.response.body":
                body_started.set()
                await asyncio.Event().wait()
            messages.append(message)

        app, calls = _runtime(tmp_path, routes)
        async with _started(app):
            request = asyncio.create_task(_http(app, send=slow_send))
            await asyncio.wait_for(body_started.wait(), 1)
            done, _ = await asyncio.wait((request,), timeout=1)
            assert request in done, "Outer deadline did not stop the stalled send"
            with pytest.raises(TimeoutError):
                request.result()
        assert [message["type"] for message in messages] == ["http.response.start"]
        assert messages[0]["status"] == 200
        assert calls == ["open", "close"]

    asyncio.run(run())


def test_stalled_request_does_not_block_concurrent_health(tmp_path):
    async def run():
        entered = asyncio.Event()

        def routes(fallback):
            async def route(scope, receive, send):
                if scope["path"] == "/blocked":
                    entered.set()
                    await asyncio.Event().wait()
                else:
                    await fallback(scope, receive, send)

            return route

        app, calls = _runtime(tmp_path, routes)
        async with _started(app):
            blocked = asyncio.create_task(_http(app))
            await asyncio.wait_for(entered.wait(), 1)
            health = await asyncio.wait_for(_http(app, path="/health/live"), 1)
            assert health[0]["status"] == 200
            assert (await asyncio.wait_for(blocked, 1))[0]["status"] == 503
        assert calls == ["open", "close"]

    asyncio.run(run())


def test_external_cancellation_propagates_without_fabricated_response(tmp_path):
    async def run():
        entered = asyncio.Event()
        messages = []

        def routes(fallback):
            async def route(scope, receive, send):
                entered.set()
                await asyncio.Event().wait()

            return route

        async def send(message):
            messages.append(message)

        app, calls = _runtime(tmp_path, routes, timeout=1.0)
        async with _started(app):
            request = asyncio.create_task(_http(app, send=send))
            await asyncio.wait_for(entered.wait(), 1)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        assert messages == [] and calls == ["open", "close"]

    asyncio.run(run())


def test_deadline_preserves_startup_and_shutdown_readiness(tmp_path):
    async def run():
        app, calls = _runtime(tmp_path)
        assert (await _http(app, path="/health/ready"))[0]["status"] == 503
        assert calls == []
        async with _started(app):
            assert (await _http(app, path="/health/ready"))[0]["status"] == 200
        assert (await _http(app, path="/health/ready"))[0]["status"] == 503
        assert calls == ["open", "close"]

    asyncio.run(run())
