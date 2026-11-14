"""Offline pool lifecycle checks using injected synthetic objects only."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from aletheia_lab.hosted.composition import ProcessApplication
from aletheia_lab.hosted.postgres_pool import PoolUnavailable, postgres_pool, probe_postgres
from aletheia_lab.hosted.settings import RuntimeSettings

DSN = "postgresql://synthetic:private-test-value@db.internal/db?sslmode=verify-full"


def _settings(tmp_path: Path, *, client_kind="public", environment="staging"):
    identity = {"issuer": "https://identity.internal/realm", "client_id": "synthetic",
                "redirect_uri": "https://app.internal/callback", "client_kind": client_kind}
    if client_kind == "confidential":
        identity["client_secret_env"] = "ALETHEIA_HOSTED_OIDC_SECRET"
    return RuntimeSettings.model_validate({
        "environment": environment, "public_origin": "https://app.internal", "identity": identity,
        "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)},
    })


class FakePool:
    def __init__(self, *, fail_open=False, fail_close=False, hang_query=False, row=(1,)):
        self.fail_open, self.fail_close, self.hang_query = fail_open, fail_close, hang_query
        self.row = row
        self.events = []

    async def open(self, *, wait, timeout):
        self.events.append(("open", wait, timeout))
        if self.fail_open:
            raise RuntimeError("private-test-value")

    @asynccontextmanager
    async def connection(self, *, timeout):
        self.events.append(("acquire", timeout))
        try:
            yield self
        finally:
            self.events.append(("release",))

    async def execute(self, query):
        self.events.append(("execute", query))
        if self.hang_query:
            await asyncio.Event().wait()
        return self

    async def fetchone(self):
        return self.row

    async def close(self, *, timeout):
        self.events.append(("close", timeout))
        if self.fail_close:
            raise RuntimeError("private-test-value")


@pytest.mark.parametrize("role,maximum,queue", [("api", 8, 64), ("worker", 2, 1)])
def test_pool_limits_probe_and_close_are_scoped_to_process_role(tmp_path, role, maximum, queue):
    async def run():
        pool = FakePool()
        arguments = {}

        def factory(**kwargs):
            arguments.update(kwargs)
            return pool

        async with postgres_pool(_settings(tmp_path), {"ALETHEIA_HOSTED_DATABASE_URL": DSN},
                                 role=role, factory=factory) as opened:
            assert opened is pool
            assert arguments["max_size"] == maximum
            assert arguments["max_waiting"] == queue
            assert arguments["min_size"] == 0
            assert arguments["open"] is False
            assert arguments["num_workers"] == 1
            assert await probe_postgres(opened, timeout=1.0)
        assert ("execute", "SELECT 1") in pool.events
        assert ("release",) in pool.events
        assert pool.events[-1][0] == "close"

    asyncio.run(run())


def test_worker_reads_database_secret_without_idp_secret(tmp_path):
    async def run():
        pool = FakePool()
        async with postgres_pool(_settings(tmp_path, client_kind="confidential"),
                                 {"ALETHEIA_HOSTED_DATABASE_URL": DSN},
                                 role="worker", factory=lambda **kwargs: pool):
            pass
        assert pool.events[-1][0] == "close"

    asyncio.run(run())


@pytest.mark.parametrize("dsn", [
    "postgresql://synthetic:private-test-value@db.internal/db",
    "postgresql://synthetic:private-test-value@db.internal/db?sslmode=disable",
    "postgresql://synthetic:private-test-value@db.internal/db?sslmode=verify-full&sslmode=disable",
])
def test_staging_requires_explicit_verified_tls_before_creating_a_pool(tmp_path, dsn):
    async def run():
        called = []
        with pytest.raises(PoolUnavailable, match="pool_tls_required") as caught:
            async with postgres_pool(_settings(tmp_path), {"ALETHEIA_HOSTED_DATABASE_URL": dsn},
                                     role="api", factory=lambda **kwargs: called.append(kwargs)):
                pass
        assert called == []
        assert "private-test-value" not in str(caught.value)

    asyncio.run(run())


def test_open_failure_still_closes_pool_without_disclosing_diagnostics(tmp_path):
    async def run():
        pool = FakePool(fail_open=True)
        with pytest.raises(PoolUnavailable, match="pool_open_failed") as caught:
            async with postgres_pool(_settings(tmp_path), {"ALETHEIA_HOSTED_DATABASE_URL": DSN},
                                     role="api", factory=lambda **kwargs: pool):
                pass
        assert pool.events[-1][0] == "close"
        assert "private-test-value" not in str(caught.value)

    asyncio.run(run())


def test_query_timeout_releases_connection_and_pool(tmp_path):
    async def run():
        pool = FakePool(hang_query=True)
        async with postgres_pool(_settings(tmp_path), {"ALETHEIA_HOSTED_DATABASE_URL": DSN},
                                 role="api", factory=lambda **kwargs: pool):
            assert await probe_postgres(pool, timeout=0.01) is False
        assert ("release",) in pool.events
        assert pool.events[-1][0] == "close"

    asyncio.run(run())


def test_unexpected_probe_result_does_not_claim_connectivity(tmp_path):
    async def run():
        pool = FakePool(row=None)
        async with postgres_pool(_settings(tmp_path), {"ALETHEIA_HOSTED_DATABASE_URL": DSN},
                                 role="api", factory=lambda **kwargs: pool):
            assert await probe_postgres(pool, timeout=1.0) is False

    asyncio.run(run())


def test_cancellation_propagates_and_pool_closes(tmp_path):
    async def run():
        pool = FakePool()
        entered = asyncio.Event()

        async def consume():
            async with postgres_pool(_settings(tmp_path), {"ALETHEIA_HOSTED_DATABASE_URL": DSN},
                                     role="api", factory=lambda **kwargs: pool):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(consume())
        await asyncio.wait_for(entered.wait(), 1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pool.events[-1][0] == "close"

    asyncio.run(run())


def test_cancellation_during_open_closes_pool(tmp_path):
    async def run():
        started = asyncio.Event()

        class SlowOpenPool(FakePool):
            async def open(self, *, wait, timeout):
                started.set()
                await asyncio.Event().wait()

        pool = SlowOpenPool()

        async def consume():
            async with postgres_pool(_settings(tmp_path), {"ALETHEIA_HOSTED_DATABASE_URL": DSN},
                                     role="api", factory=lambda **kwargs: pool):
                pass

        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), 1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pool.events[-1][0] == "close"

    asyncio.run(run())


@pytest.mark.parametrize("timeout", [True, 0.0, -1.0, float("nan"), float("inf")])
def test_probe_rejects_unbounded_or_invalid_timeout(timeout):
    async def run():
        with pytest.raises(ValueError, match="finite and positive"):
            await probe_postgres(FakePool(), timeout=timeout)

    asyncio.run(run())


def test_close_failure_is_sanitized_and_caller_error_is_not_rewritten(tmp_path):
    async def run():
        pool = FakePool(fail_close=True)
        with pytest.raises(PoolUnavailable, match="pool_close_failed") as caught:
            async with postgres_pool(_settings(tmp_path), {"ALETHEIA_HOSTED_DATABASE_URL": DSN},
                                     role="api", factory=lambda **kwargs: pool):
                pass
        assert "private-test-value" not in str(caught.value)
        pool = FakePool()
        with pytest.raises(ValueError, match="caller-error"):
            async with postgres_pool(_settings(tmp_path), {"ALETHEIA_HOSTED_DATABASE_URL": DSN},
                                     role="api", factory=lambda **kwargs: pool):
                raise ValueError("caller-error")
        assert pool.events[-1][0] == "close"

    asyncio.run(run())


@pytest.mark.parametrize("row,expected", [((1,), "lifespan.startup.complete"), (None, "lifespan.startup.failed")])
def test_process_lifespan_probes_and_closes_injected_pool(tmp_path, row, expected):
    async def run():
        settings = _settings(tmp_path)
        pool = FakePool(row=row)

        class Backend:
            async def is_ready(self):
                return await probe_postgres(pool, timeout=1.0)

        @asynccontextmanager
        async def resources():
            async with postgres_pool(settings, {"ALETHEIA_HOSTED_DATABASE_URL": DSN},
                                     role="api", factory=lambda **kwargs: pool):
                yield Backend()

        app = ProcessApplication(settings, resources)
        incoming = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
        messages = []

        async def receive():
            return next(incoming)

        async def send(message):
            messages.append(message["type"])

        await app({"type": "lifespan"}, receive, send)
        assert messages[0] == expected
        if row is not None:
            assert messages[-1] == "lifespan.shutdown.complete"
        assert ("execute", "SELECT 1") in pool.events
        assert ("release",) in pool.events
        assert pool.events[-1][0] == "close"

    asyncio.run(run())
