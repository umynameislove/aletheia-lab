"""A Selector API loop uses real bounded report children, then reaps its worker."""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path

import pytest

import aletheia_lab.product
from aletheia_lab.hosted.report_renderer import ReportRenderer
from aletheia_lab.hosted.worker_supervisor import WorkerRejected
from aletheia_lab.product.reports import render_product_report
from aletheia_lab.product.view import ProductView

pytestmark = pytest.mark.integration


def test_selector_loop_real_json_markdown_children_and_renderer_cleanup(monkeypatch):
    fixture = Path(aletheia_lab.product.__file__).with_name("synthetic_p6_view.json").read_bytes()
    view = ProductView.model_validate_json(fixture)
    create_process = asyncio.create_subprocess_exec
    processes, spawn_threads, spawn_loops = [], [], []
    monkeypatch.setenv("ALETHEIA_SYNTHETIC_PRIVATE_VALUE", "synthetic-not-for-child")

    async def observed_spawn(*args, **kwargs):
        assert "ALETHEIA_SYNTHETIC_PRIVATE_VALUE" not in kwargs["env"]
        assert set(kwargs["env"]) <= {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PYTHONPATH"}
        process = await create_process(*args, **kwargs)
        processes.append(process)
        spawn_threads.append(threading.get_ident())
        spawn_loops.append(type(asyncio.get_running_loop()).__name__)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observed_spawn)

    async def scenario():
        assert isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)
        main_thread = threading.get_ident()
        renderer = ReportRenderer(slots=1, timeout_seconds=20)
        try:
            for report_format in ("json", "markdown", "pdf"):
                assert await renderer.render(report_format, fixture) == render_product_report(view, report_format)
            with pytest.raises(WorkerRejected):
                await renderer.render("json", b"invalid-json")
        finally:
            await renderer.aclose()
        await renderer.aclose()
        assert len(processes) == 4 and all(process.pid != os.getpid() for process in processes)
        assert all(process.returncode is not None for process in processes)
        assert not renderer._worker._tasks
        if sys.platform == "win32":
            assert renderer._thread is not None and not renderer._thread.is_alive()
            assert all(thread != main_thread for thread in spawn_threads)
            assert all("Proactor" in name for name in spawn_loops)
        with pytest.raises(TimeoutError, match="unavailable"):
            await renderer.render("json", fixture)

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(scenario())
