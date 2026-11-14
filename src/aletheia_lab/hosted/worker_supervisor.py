"""Bounded internal report subprocesses, without a job queue or HTTP API."""

from __future__ import annotations

import asyncio
import json
import math
import os
from contextlib import suppress
from pathlib import Path
from typing import Literal

from .worker_limits import MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, MAX_UPLOAD_OUTPUT_BYTES

ReportFormat = Literal["json", "markdown", "pdf"]


class WorkerRejected(RuntimeError):
    """Stable internal error; never includes input, child stderr or credentials."""


def _environment(source_root: Path) -> dict[str, str]:
    # The worker must not inherit IdP, DB or provider credentials. These are OS
    # process necessities only; PYTHONPATH comes from trusted runtime wiring.
    allowed = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
    result = {key: os.environ[key] for key in allowed if key in os.environ}
    result["PYTHONPATH"] = str(source_root)
    return result


class ReportWorkerSupervisor:
    """One event-loop-owned bounded supervisor for the trusted report worker.

    Input must already be an authorized immutable view. This does not grant
    access, persist jobs, provide durable ACK or authorize output publication.
    """

    def _output_limit_for(self, payload: bytes) -> int:
        return MAX_OUTPUT_BYTES

    def __init__(self, *, python: Path, source_root: Path, slots: int, timeout_seconds: float) -> None:
        if not python.is_absolute() or not source_root.is_absolute():
            raise ValueError("trusted worker executable and source paths must be absolute")
        if type(slots) is not int or slots <= 0:
            raise ValueError("worker slots must be a positive integer")
        if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("worker timeout must be finite and positive")
        self._python = python
        self._source_root = source_root
        self._timeout = timeout_seconds
        self._slots = asyncio.BoundedSemaphore(slots)
        self._tasks: set[asyncio.Task[bytes]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False

    def _finished(self, task: asyncio.Task[bytes]) -> None:
        self._tasks.discard(task)
        self._slots.release()
        # Observe exceptions even if the caller was cancelled while the child
        # was being reaped. This does not log or expose child diagnostics.
        if not task.cancelled():
            task.exception()

    async def render(self, report_format: ReportFormat, payload: bytes) -> bytes:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise WorkerRejected("worker_wrong_event_loop")
        self._loop = loop
        if self._closed:
            raise WorkerRejected("worker_closed")
        if report_format not in ("json", "markdown", "pdf") or not isinstance(payload, bytes):
            raise WorkerRejected("worker_invalid_request")
        if len(payload) > MAX_INPUT_BYTES:
            raise WorkerRejected("worker_input_too_large")
        if self._slots.locked():
            raise WorkerRejected("worker_busy")
        await self._slots.acquire()
        task = loop.create_task(self._execute(report_format, payload))
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.done() and not task.cancelling():
                task.cancel()
            cleanup = asyncio.gather(task, return_exceptions=True)
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            raise

    async def _reap_spawn(self, spawn: asyncio.Task[asyncio.subprocess.Process],
                          process: asyncio.subprocess.Process | None) -> None:
        # Shield process creation so a cancellation during spawn cannot lose
        # the child handle. Keep the slot until that child has been reaped.
        if process is None:
            try:
                process = await spawn
            except Exception:
                process = None
        if process is not None:
            await self._terminate(process)

    async def _execute(self, report_format: ReportFormat, payload: bytes) -> bytes:
        module, argument = self._command(report_format)
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(
            str(self._python), "-B", "-m", module, argument,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(self._source_root.parent), env=_environment(self._source_root),
        ))
        process: asyncio.subprocess.Process | None = None
        try:
            async with asyncio.timeout(self._timeout):
                process = await asyncio.shield(spawn)
                output = await self._communicate(process, payload)
            if report_format == "pdf" and process.returncode == 3:
                raise WorkerRejected("worker_pdf_unsupported_text")
            if process.returncode != 0 or len(output) > self._output_limit_for(payload):
                raise WorkerRejected("worker_failed")
            return output
        except (asyncio.CancelledError, TimeoutError) as error:
            await self._reap_spawn(spawn, process)
            if isinstance(error, TimeoutError):
                raise WorkerRejected("worker_timeout") from None
            raise
        except WorkerRejected:
            if process is not None:
                await self._terminate(process)
            raise
        except Exception:
            if process is not None:
                await self._terminate(process)
            raise WorkerRejected("worker_failed") from None

    def _command(self, report_format: ReportFormat) -> tuple[str, str]:
        return "aletheia_lab.hosted.worker", report_format

    async def _communicate(self, process: asyncio.subprocess.Process, payload: bytes) -> bytes:
        async def read(stream: asyncio.StreamReader | None, limit: int) -> bytes:
            assert stream is not None
            result = bytearray()
            while block := await stream.read(64 * 1024):
                if len(result) + len(block) > limit:
                    raise WorkerRejected("worker_failed")
                result.extend(block)
            return bytes(result)

        async def write() -> None:
            assert process.stdin is not None
            try:
                process.stdin.write(payload)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        tasks = (asyncio.create_task(write()),
                 asyncio.create_task(read(process.stdout, self._output_limit_for(payload))),
                 asyncio.create_task(read(process.stderr, 64 * 1024)),
                 asyncio.create_task(process.wait()))
        try:
            await asyncio.gather(*tasks)
            return tasks[1].result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        try:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            # Drain pipes as well as waiting; a child can otherwise block with
            # full output pipes while the parent waits for its termination.
            await process.communicate()
        except Exception:
            # A child whose cleanup failed may still exist. Stop admitting work
            # instead of treating the released task slot as a safe capacity.
            self._closed = True
            raise WorkerRejected("worker_cleanup_failed") from None

    async def aclose(self) -> None:
        if self._loop is not None and self._loop is not asyncio.get_running_loop():
            raise WorkerRejected("worker_wrong_event_loop")
        self._closed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        if tasks:
            await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))


class UploadWorkerSupervisor(ReportWorkerSupervisor):
    """Bounded upload worker; root is trusted runtime configuration, not input."""

    def _output_limit_for(self, payload: bytes) -> int:
        try:
            request = json.loads(payload)
        except (ValueError, UnicodeError):
            return MAX_OUTPUT_BYTES
        return MAX_UPLOAD_OUTPUT_BYTES if isinstance(request, dict) and request.get("operation") == "confirm" else MAX_OUTPUT_BYTES

    def __init__(self, *, python: Path, source_root: Path, private_root: Path,
                 slots: int, timeout_seconds: float) -> None:
        super().__init__(python=python, source_root=source_root, slots=slots, timeout_seconds=timeout_seconds)
        if not private_root.is_absolute():
            raise ValueError("absolute private root required")
        self._private_root = private_root

    async def run_upload(self, payload: bytes) -> bytes:
        return await self.render("json", payload)

    def _command(self, report_format: ReportFormat) -> tuple[str, str]:
        if report_format != "json":
            raise WorkerRejected("worker_invalid_request")
        return "aletheia_lab.hosted.upload_worker", str(self._private_root)
