"""Cheap current-principal probes and bounded renderer availability semantics."""
from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import aletheia_lab.hosted.report_renderer as renderer_module
from aletheia_lab.hosted.catalog_access import CatalogAccess
from aletheia_lab.hosted.report_renderer import ReportRenderer
from aletheia_lab.hosted.worker_supervisor import WorkerRejected


@pytest.mark.parametrize("failure", [None, PermissionError(), RuntimeError()])
def test_catalog_readiness_checks_required_projections_without_rows_and_handles_unavailable(failure):
    seen = []

    class Connection:
        async def execute(self, query):
            seen.append(query)
            if failure is not None:
                raise failure

    @asynccontextmanager
    async def connection():
        yield Connection()

    access = CatalogAccess(SimpleNamespace(pool=SimpleNamespace(connection=connection)), None, None)
    assert asyncio.run(access.is_ready()) is (failure is None)
    assert len(seen) == 1 and seen[0].endswith("LIMIT 0")
    for relation in ("k05_projects", "k05_records", "k05_objects", "k05_current_pointers",
                     "k05_record_parents", "k05_object_references", "k05_object_leases",
                     "j01_project_grants", "j01_annotations"):
        assert relation in seen[0]


def test_catalog_readiness_preserves_cancellation():
    @asynccontextmanager
    async def connection():
        raise asyncio.CancelledError()
        yield

    access = CatalogAccess(SimpleNamespace(pool=SimpleNamespace(connection=connection)), None, None)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(access.is_ready())


def _renderer(monkeypatch):
    # Exercise the real supervisor slot on one loop, without starting a subprocess.
    monkeypatch.setattr(renderer_module, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
    return ReportRenderer(slots=1)


def test_real_supervisor_busy_is_temporary_and_closed_renderer_stays_unavailable(monkeypatch):
    async def scenario():
        renderer = _renderer(monkeypatch)
        entered, release = asyncio.Event(), asyncio.Event()

        async def execute(report_format, payload):
            entered.set()
            await release.wait()
            return payload

        renderer._worker._execute = execute
        first = asyncio.create_task(renderer.render("json", b"synthetic"))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            with pytest.raises(TimeoutError, match="temporarily unavailable"):
                await renderer.render("json", b"another")
            release.set()
            assert await first == b"synthetic"
        finally:
            release.set()
            await renderer.aclose()
        with pytest.raises(TimeoutError, match="temporarily unavailable"):
            await renderer.render("json", b"synthetic")
    asyncio.run(scenario())


@pytest.mark.parametrize("reason,expected", [("worker_timeout", TimeoutError), ("worker_closed", TimeoutError),
    ("worker_input_too_large", WorkerRejected), ("worker_failed", WorkerRejected)])
def test_renderer_only_translates_availability_rejections(monkeypatch, reason, expected):
    async def scenario():
        renderer = _renderer(monkeypatch)

        async def execute(report_format, payload):
            raise WorkerRejected(reason)

        renderer._worker._execute = execute
        try:
            with pytest.raises(expected):
                await renderer.render("json", b"synthetic")
        finally:
            await renderer.aclose()
    asyncio.run(scenario())


def test_renderer_caller_cancellation_propagates_and_reaps_work(monkeypatch):
    async def scenario():
        renderer = _renderer(monkeypatch)
        entered = asyncio.Event()

        async def execute(report_format, payload):
            entered.set()
            await asyncio.Event().wait()

        renderer._worker._execute = execute
        pending = asyncio.create_task(renderer.render("json", b"synthetic"))
        await asyncio.wait_for(entered.wait(), 1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await renderer.aclose()
        await asyncio.sleep(0)
        assert not renderer._worker._tasks
    asyncio.run(scenario())
