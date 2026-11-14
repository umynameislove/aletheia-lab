"""Offline tests start only local worker children using synthetic data."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from aletheia_lab.hosted.worker_supervisor import (
    ReportWorkerSupervisor,
    WorkerRejected,
    _environment,
)
from aletheia_lab.product.reports import render_product_report
from aletheia_lab.product.view import ProductView

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "src/aletheia_lab/product/synthetic_p6_view.json"


def _supervisor(*, slots=1, timeout=30.0):
    return ReportWorkerSupervisor(
        python=Path(sys.executable).absolute(), source_root=(ROOT / "src").resolve(),
        slots=slots, timeout_seconds=timeout,
    )


def test_real_child_result_matches_domain_renderer_and_closed_supervisor_denies():
    async def run():
        supervisor = _supervisor()
        raw = FIXTURE.read_bytes()
        result = await supervisor.render("markdown", raw)
        assert result == render_product_report(ProductView.model_validate_json(raw), "markdown")
        await supervisor.aclose()
        with pytest.raises(WorkerRejected, match="worker_closed"):
            await supervisor.render("markdown", raw)

    asyncio.run(run())


def test_busy_admission_does_not_start_an_extra_worker():
    async def run():
        supervisor = _supervisor()
        first = asyncio.create_task(supervisor.render("json", FIXTURE.read_bytes()))
        await asyncio.sleep(0)
        with pytest.raises(WorkerRejected, match="worker_busy"):
            await supervisor.render("json", FIXTURE.read_bytes())
        assert await first
        await supervisor.aclose()

    asyncio.run(run())


def test_failed_child_does_not_expose_input_and_slot_recovers():
    async def run():
        supervisor = _supervisor()
        with pytest.raises(WorkerRejected, match="worker_failed") as caught:
            await supervisor.render("json", b'{"private":"synthetic-secret"}')
        assert "synthetic-secret" not in str(caught.value)
        assert await supervisor.render("json", FIXTURE.read_bytes())
        await supervisor.aclose()

    asyncio.run(run())


def test_cancel_reaps_child_and_capacity_recovers(monkeypatch):
    async def run():
        started = asyncio.Event()
        children = []
        original_spawn = asyncio.create_subprocess_exec

        async def tracked_spawn(*args, **kwargs):
            child = await original_spawn(*args, **kwargs)
            children.append(child)
            started.set()
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", tracked_spawn)
        supervisor = _supervisor()
        task = asyncio.create_task(supervisor.render("json", b" " * (5 * 1024 * 1024)))
        await asyncio.wait_for(started.wait(), 30)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async def retry_after_reap():
            while True:
                try:
                    return await supervisor.render("json", FIXTURE.read_bytes())
                except WorkerRejected as error:
                    if str(error) != "worker_busy":
                        raise
                    await asyncio.sleep(0.01)

        assert await asyncio.wait_for(retry_after_reap(), 30)
        assert children[0].returncode is not None
        await asyncio.wait_for(supervisor.aclose(), 30)
        with pytest.raises(WorkerRejected, match="worker_closed"):
            await supervisor.render("json", FIXTURE.read_bytes())

    asyncio.run(run())


def test_timeout_fails_closed_and_supervisor_can_shutdown():
    async def run():
        supervisor = _supervisor(timeout=0.001)
        with pytest.raises(WorkerRejected, match="worker_timeout"):
            await supervisor.render("json", b" " * (5 * 1024 * 1024))
        await asyncio.wait_for(supervisor.aclose(), 30)

    asyncio.run(run())


def test_worker_environment_does_not_inherit_application_secrets(monkeypatch):
    monkeypatch.setenv("ALETHEIA_HOSTED_DATABASE_URL", "synthetic-database-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-provider-secret")
    environment = _environment((ROOT / "src").resolve())
    assert "ALETHEIA_HOSTED_DATABASE_URL" not in environment
    assert "OPENAI_API_KEY" not in environment
    assert environment["PYTHONPATH"] == str((ROOT / "src").resolve())


def test_event_loop_remains_schedulable_while_child_process_renders():
    async def run():
        supervisor = _supervisor()
        # Whitespace is valid JSON and makes the worker scan a larger bounded
        # synthetic input; it does not change the validated ProductView.
        payload = b" " * (8 * 1024 * 1024) + FIXTURE.read_bytes()
        try:
            task = asyncio.create_task(supervisor.render("json", payload))
            ticks = 0
            while not task.done():
                await asyncio.sleep(0.01)
                ticks += 1
            output = await task
            assert ticks >= 2
            assert output == render_product_report(ProductView.model_validate_json(FIXTURE.read_bytes()), "json")
        finally:
            await supervisor.aclose()

    asyncio.run(run())
