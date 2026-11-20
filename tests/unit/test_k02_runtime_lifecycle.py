"""Process lifetime tests with synthetic resources and no service connections."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from aletheia_lab.hosted.composition import ProcessApplication
from aletheia_lab.hosted.settings import RuntimeSettings


class ProbeBackend:
    def __init__(self, *, ready=True):
        self.ready = ready

    async def is_ready(self):
        return self.ready

    async def load_session(self, cookie):
        raise AssertionError("no identity implementation in a lifecycle test")

    async def capabilities(self, session, workspace_id):
        raise AssertionError("no membership implementation in a lifecycle test")


def _settings(tmp_path, **api):
    return RuntimeSettings.model_validate({
        "environment": "staging", "public_origin": "https://app.internal",
        "identity": {
            "issuer": "https://identity.internal/realms/synthetic", "client_id": "synthetic-client",
            "redirect_uri": "https://app.internal/v1/auth/callback", "client_kind": "public",
        },
        "storage": {
            "database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path / "objects"),
        },
        "api": api,
    })


async def _http(app, route="/health/ready"):
    messages = []

    async def receive():
        raise AssertionError("health route should not read a body")

    async def send(message):
        messages.append(message)

    await app({"type": "http", "method": "GET", "path": route, "headers": [(b"host", b"app.internal")]}, receive, send)
    return messages[0]["status"]


def _lifespan(app, events):
    async def run():
        pending = iter(events)
        messages = []

        async def receive():
            return next(pending)

        async def send(message):
            messages.append(message)

        await app({"type": "lifespan"}, receive, send)
        assert await _http(app) == 503
        return messages

    return asyncio.run(run())


def test_constructor_and_http_before_startup_do_not_create_resources(tmp_path):
    calls = []

    @asynccontextmanager
    async def resources():
        calls.append("opened")
        yield ProbeBackend()

    app = ProcessApplication(_settings(tmp_path), resources)
    assert calls == []
    assert asyncio.run(_http(app)) == 503
    assert calls == []
    assert app.body_limits.maximum_bytes == app.settings.api.maximum_json_bytes


def test_ready_only_after_startup_and_cleanup_exactly_once(tmp_path):
    calls = []

    @asynccontextmanager
    async def resources():
        calls.append("opened")
        try:
            yield ProbeBackend()
        finally:
            calls.append("closed")

    app = ProcessApplication(_settings(tmp_path), resources)

    async def run():
        events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
        messages = []

        async def receive():
            return next(events)

        async def send(message):
            messages.append(message)
            if message["type"] == "lifespan.startup.complete":
                assert calls == ["opened"]
                assert await _http(app) == 200

        await app({"type": "lifespan"}, receive, send)
        assert await _http(app) == 503
        return messages

    assert asyncio.run(run()) == [
        {"type": "lifespan.startup.complete"}, {"type": "lifespan.shutdown.complete"},
    ]
    assert calls == ["opened", "closed"]


def test_unready_backend_is_closed_and_does_not_complete_startup(tmp_path):
    calls = []

    @asynccontextmanager
    async def resources():
        try:
            yield ProbeBackend(ready=False)
        finally:
            calls.append("closed")

    app = ProcessApplication(_settings(tmp_path), resources)
    messages = _lifespan(app, [{"type": "lifespan.startup"}])
    assert [x["type"] for x in messages] == ["lifespan.startup.failed"]
    assert calls == ["closed"]


def test_partial_startup_exception_has_cleanup_and_fixed_failure_text(tmp_path):
    calls = []

    @asynccontextmanager
    async def resources():
        try:
            raise RuntimeError("postgresql://private:synthetic-secret@host/db")
            yield ProbeBackend()
        finally:
            calls.append("closed")

    messages = _lifespan(ProcessApplication(_settings(tmp_path), resources), [{"type": "lifespan.startup"}])
    assert messages == [{"type": "lifespan.startup.failed", "message": "Runtime dependencies are unavailable."}]
    assert "synthetic-secret" not in repr(messages)
    assert calls == ["closed"]


def test_startup_deadline_cleans_partially_acquired_resources(tmp_path):
    calls = []

    @asynccontextmanager
    async def resources():
        try:
            await asyncio.Event().wait()
            yield ProbeBackend()
        finally:
            calls.append("closed")

    app = ProcessApplication(_settings(tmp_path, startup_timeout_seconds=0.01), resources)
    assert _lifespan(app, [{"type": "lifespan.startup"}])[0]["type"] == "lifespan.startup.failed"
    assert calls == ["closed"]


def test_readiness_probe_timeout_closes_an_entered_context(tmp_path):
    calls = []

    class SlowProbe(ProbeBackend):
        async def is_ready(self):
            await asyncio.Event().wait()

    @asynccontextmanager
    async def resources():
        try:
            yield SlowProbe()
        finally:
            calls.append("closed")

    app = ProcessApplication(_settings(tmp_path, startup_timeout_seconds=0.01), resources)
    assert _lifespan(app, [{"type": "lifespan.startup"}])[0]["type"] == "lifespan.startup.failed"
    assert calls == ["closed"]


def test_cancellation_closes_resources_and_propagates(tmp_path):
    calls = []

    @asynccontextmanager
    async def resources():
        try:
            yield ProbeBackend()
        finally:
            calls.append("closed")

    app = ProcessApplication(_settings(tmp_path), resources)

    async def run():
        started = asyncio.Event()
        queue = asyncio.Queue()
        queue.put_nowait({"type": "lifespan.startup"})

        async def send(message):
            if message["type"] == "lifespan.startup.complete":
                started.set()

        task = asyncio.create_task(app({"type": "lifespan"}, queue.get, send))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await _http(app) == 503

    asyncio.run(run())
    assert calls == ["closed"]


def test_shutdown_error_does_not_report_success_or_disclose_exception(tmp_path):
    @asynccontextmanager
    async def resources():
        yield ProbeBackend()
        raise RuntimeError("private-dsn-synthetic-secret")

    app = ProcessApplication(_settings(tmp_path), resources)
    messages = _lifespan(app, [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    assert messages[-1] == {"type": "lifespan.shutdown.failed", "message": "Runtime shutdown did not complete."}
    assert "synthetic-secret" not in repr(messages)


def test_resource_close_timeout_is_a_failed_shutdown(tmp_path):
    @asynccontextmanager
    async def resources():
        try:
            yield ProbeBackend()
        finally:
            await asyncio.Event().wait()

    app = ProcessApplication(_settings(tmp_path, resource_close_timeout_seconds=0.01), resources)
    messages = _lifespan(app, [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    assert messages[-1]["type"] == "lifespan.shutdown.failed"


def test_active_backend_cannot_be_used_on_another_event_loop(tmp_path):
    @asynccontextmanager
    async def resources():
        yield ProbeBackend()

    app = ProcessApplication(_settings(tmp_path), resources)

    async def run():
        events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

        async def receive():
            return next(events)

        async def send(message):
            if message["type"] == "lifespan.startup.complete":
                with pytest.raises(RuntimeError, match="owning event loop"):
                    await asyncio.to_thread(lambda: asyncio.run(_http(app)))

        await app({"type": "lifespan"}, receive, send)

    asyncio.run(run())


def test_send_failure_propagates_but_still_closes_backend(tmp_path):
    calls = []

    @asynccontextmanager
    async def resources():
        try:
            yield ProbeBackend()
        finally:
            calls.append("closed")

    app = ProcessApplication(_settings(tmp_path), resources)

    async def run():
        async def receive():
            return {"type": "lifespan.startup"}

        async def send(message):
            raise OSError("synthetic transport failure")

        with pytest.raises(OSError):
            await app({"type": "lifespan"}, receive, send)

    asyncio.run(run())
    assert calls == ["closed"]


def test_one_application_instance_cannot_reopen_process_resources(tmp_path):
    calls = []

    @asynccontextmanager
    async def resources():
        calls.append("opened")
        yield ProbeBackend()

    app = ProcessApplication(_settings(tmp_path), resources)
    _lifespan(app, [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    second = _lifespan(app, [{"type": "lifespan.startup"}])
    assert second[0]["type"] == "lifespan.startup.failed"
    assert calls == ["opened"]


def test_failed_scheduler_disables_routes_and_closes_resources(tmp_path):
    async def run():
        fail = asyncio.Event()
        stopped = asyncio.Event()
        closed = []

        @asynccontextmanager
        async def resources():
            try:
                yield ProbeBackend()
            finally:
                closed.append(True)

        async def scheduler(stop):
            await fail.wait()
            stopped.set()
            raise RuntimeError("private scheduler detail")

        def routes(fallback):
            async def route(scope, receive, send):
                await send({"type": "http.response.start", "status": 204, "headers": []})
                await send({"type": "http.response.body", "body": b""})

            return route

        app = ProcessApplication(_settings(tmp_path), resources, routes=routes, scheduler=scheduler)
        incoming = asyncio.Queue()
        incoming.put_nowait({"type": "lifespan.startup"})
        messages = []

        async def send(message):
            messages.append(message)
            if message["type"] == "lifespan.startup.complete":
                assert await _http(app, "/experimental") == 204
                fail.set()
                await asyncio.wait_for(stopped.wait(), 1)
                assert await _http(app) == 503
                assert await _http(app, "/experimental") == 404
                incoming.put_nowait({"type": "lifespan.shutdown"})

        await app({"type": "lifespan"}, incoming.get, send)
        assert messages[-1]["type"] == "lifespan.shutdown.failed"
        assert "private scheduler detail" not in repr(messages)
        assert closed == [True]

    asyncio.run(run())


def test_scheduler_return_before_shutdown_is_reported_as_failure(tmp_path):
    async def run():
        finish = asyncio.Event()
        finished = asyncio.Event()
        closed = []

        @asynccontextmanager
        async def resources():
            try:
                yield ProbeBackend()
            finally:
                closed.append(True)

        async def scheduler(stop):
            await finish.wait()
            finished.set()

        app = ProcessApplication(_settings(tmp_path), resources, scheduler=scheduler)
        events = asyncio.Queue()
        events.put_nowait({"type": "lifespan.startup"})
        messages = []

        async def send(message):
            messages.append(message)
            if message["type"] == "lifespan.startup.complete":
                finish.set()
                await asyncio.wait_for(finished.wait(), 1)
                assert await _http(app) == 503
                events.put_nowait({"type": "lifespan.shutdown"})

        await app({"type": "lifespan"}, events.get, send)
        assert messages[-1]["type"] == "lifespan.shutdown.failed"
        assert closed == [True]

    asyncio.run(run())
