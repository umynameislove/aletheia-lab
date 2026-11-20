"""Use the existing bounded report worker on API loops, including Windows pools."""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

from aletheia_lab.product.pdf import pdf_available

from .observability import timed_stage
from .request_boundary import RequestRejected
from .windows_worker import _call_on_loop, _wait_finished
from .worker_supervisor import ReportFormat, ReportWorkerSupervisor, WorkerRejected


class ReportRenderer:
    def __init__(self, *, slots: int = 1, timeout_seconds: float = 15) -> None:
        self._worker = ReportWorkerSupervisor(python=Path(sys.executable).absolute(),
            source_root=Path(__file__).resolve().parents[2], slots=slots, timeout_seconds=timeout_seconds)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self._closing: asyncio.Task[None] | None = None
        if sys.platform == "win32":
            self._loop = asyncio.ProactorEventLoop()
            self._thread = threading.Thread(target=self._loop.run_forever, name="aletheia-report-worker", daemon=True)
            self._thread.start()

    async def render(self, report_format: ReportFormat, payload: bytes) -> bytes:
        with timed_stage("report_render"):
            return await self._render(report_format, payload)

    async def _render(self, report_format: ReportFormat, payload: bytes) -> bytes:
        if self._closed:
            raise TimeoutError("Report worker is temporarily unavailable.")
        if report_format == "pdf" and not pdf_available():
            raise RequestRejected(503, "service_unavailable", "PDF rendering is currently unavailable.")
        try:
            if self._loop is None:
                return await self._worker.render(report_format, payload)
            return await _call_on_loop(self._loop, lambda: self._worker.render(report_format, payload))
        except WorkerRejected as error:
            if str(error) == "worker_pdf_unsupported_text":
                raise RequestRejected(400, "invalid_request", "Some report characters are outside the PDF font. Use JSON or Markdown.") from None
            if str(error) in ("worker_busy", "worker_timeout", "worker_closed"):
                raise TimeoutError("Report worker is temporarily unavailable.") from None
            raise

    async def aclose(self) -> None:
        if self._closing is None:
            self._closed = True
            self._closing = asyncio.create_task(self._close())
        await _wait_finished(self._closing)

    async def _close(self) -> None:
        if self._loop is None:
            await self._worker.aclose()
            return
        try:
            await _call_on_loop(self._loop, self._worker.aclose)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            assert self._thread is not None
            await asyncio.to_thread(self._thread.join, 5)
            if self._thread.is_alive():
                raise RuntimeError("Report worker shutdown did not complete.")
            self._loop.close()
