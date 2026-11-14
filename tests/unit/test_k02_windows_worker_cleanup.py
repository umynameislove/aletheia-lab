"""Cancellation must acknowledge cleanup across the Windows worker event loops."""

import asyncio
import sys
import threading
from pathlib import Path

import pytest

from aletheia_lab.hosted.report_renderer import ReportRenderer
from aletheia_lab.hosted.windows_worker import WindowsUploadWorker
from aletheia_lab.hosted.worker_supervisor import WorkerRejected
from aletheia_lab.product.reports import render_product_report
from aletheia_lab.product.view import ProductView

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows event-loop integration")


@pytest.mark.parametrize("kind", ["upload", "report"])
@pytest.mark.parametrize("repeat_cancel", [False, True])
@pytest.mark.parametrize("closing", [False, True])
def test_selector_cancellation_waits_for_proactor_cleanup(tmp_path, kind, repeat_cancel, closing):
    async def scenario():
        worker = (WindowsUploadWorker(python=Path(sys.executable),
            source_root=Path(__file__).resolve().parents[2] / "src", private_root=tmp_path,
            slots=1, timeout_seconds=10) if kind == "upload" else ReportRenderer(slots=1, timeout_seconds=10))

        async def run(payload):
            return await (worker.run_upload(payload) if kind == "upload" else worker.render("json", payload))
        started, cleanup_started, cleanup_finished = (threading.Event() for _ in range(3))
        state = {}

        async def execute(report_format, payload):
            release = state["release"] = asyncio.Event()
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                await release.wait()
                cleanup_finished.set()

        worker._worker._execute = execute
        upload = asyncio.create_task(run(b"synthetic"))
        caller = upload
        try:
            assert await asyncio.to_thread(started.wait, 2)
            if closing:
                caller = asyncio.create_task(worker.aclose())
                assert await asyncio.to_thread(cleanup_started.wait, 2)
            caller.cancel()
            assert await asyncio.to_thread(cleanup_started.wait, 2)
            if repeat_cancel:
                caller.cancel()
            await asyncio.sleep(0)
            assert not caller.done(), "caller returned before the worker finished cleanup"
            if not closing:
                expected = WorkerRejected if kind == "upload" else TimeoutError
                with pytest.raises(expected):
                    await run(b"next")
            assert not cleanup_finished.is_set()
            worker._loop.call_soon_threadsafe(state["release"].set)
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(caller), 2)
            assert cleanup_finished.is_set()
        finally:
            if not worker._loop.is_closed() and "release" in state:
                worker._loop.call_soon_threadsafe(state["release"].set)
            await asyncio.gather(upload, caller, return_exceptions=True)
            await worker.aclose()
        assert not worker._thread.is_alive()
        assert worker._loop.is_closed()

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(scenario())


@pytest.mark.parametrize("kind", ["upload", "report"])
def test_cancellation_before_proactor_task_starts_does_not_lose_completion(tmp_path, kind):
    async def scenario():
        worker = (WindowsUploadWorker(python=Path(sys.executable),
            source_root=Path(__file__).resolve().parents[2] / "src", private_root=tmp_path,
            slots=1, timeout_seconds=10) if kind == "upload" else ReportRenderer(slots=1, timeout_seconds=10))

        async def run(payload):
            return await (worker.run_upload(payload) if kind == "upload" else worker.render("json", payload))
        blocked, release = threading.Event(), threading.Event()
        executed = threading.Event()

        def block_loop():
            blocked.set()
            release.wait(2)

        async def execute(report_format, payload):
            executed.set()
            return b"synthetic-result"

        worker._worker._execute = execute
        worker._loop.call_soon_threadsafe(block_loop)
        upload = None
        try:
            assert await asyncio.to_thread(blocked.wait, 2)
            upload = asyncio.create_task(run(b"cancel-before-start"))
            await asyncio.sleep(0)
            upload.cancel()
            await asyncio.sleep(0)
            assert not upload.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(upload), 2)
            assert not executed.is_set()
            assert await run(b"next") == b"synthetic-result"
        finally:
            release.set()
            if upload is not None:
                await asyncio.gather(upload, return_exceptions=True)
            await worker.aclose()
        assert not worker._thread.is_alive()

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(scenario())


@pytest.mark.parametrize("report_format", ["json", "markdown"])
def test_selector_report_renderer_uses_real_subprocess_and_closes(report_format):
    raw = (Path(__file__).resolve().parents[2] / "src/aletheia_lab/product/synthetic_p6_view.json").read_bytes()
    view = ProductView.model_validate_json(raw)

    async def scenario():
        renderer = ReportRenderer(slots=1, timeout_seconds=20)
        try:
            assert await renderer.render(report_format, raw) == render_product_report(view, report_format)
        finally:
            await renderer.aclose()
        assert not renderer._thread.is_alive() and renderer._loop.is_closed()

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(scenario())
