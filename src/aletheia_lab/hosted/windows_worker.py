"""Run subprocess workers on a dedicated Windows Proactor loop."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from pathlib import Path
from typing import Any, TypeVar

from .payload_upload_worker import PayloadUploadWorkerSupervisor as UploadWorkerSupervisor
from .worker_limits import MAX_UPLOAD_OUTPUT_BYTES

_T = TypeVar("_T")


async def _wait_finished(future: asyncio.Future[_T], cancel: Callable[[], None] | None = None) -> _T:
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        if cancel is not None and not future.done():
            cancel()
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not future.cancelled():
            future.exception()
        raise


async def _call_on_loop(loop: asyncio.AbstractEventLoop, operation: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    completion: Future[_T] = Future()
    task: asyncio.Task[_T] | None = None

    def finished(inner: asyncio.Task[_T]) -> None:
        # Cancellation of a concurrent Future is not an acknowledgment that
        # the Proactor task has finished reaping its subprocess.
        if inner.cancelled():
            completion.cancel()
        elif (error := inner.exception()) is not None:
            completion.set_exception(error)
        else:
            completion.set_result(inner.result())

    def start() -> None:
        nonlocal task
        try:
            task = loop.create_task(operation())
        except Exception as error:
            completion.set_exception(error)
        else:
            task.add_done_callback(finished)

    def cancel() -> None:
        if task is not None and not task.done() and not task.cancelling():
            task.cancel()

    def request_cancel() -> None:
        loop.call_soon_threadsafe(cancel)

    loop.call_soon_threadsafe(start)
    return await _wait_finished(asyncio.wrap_future(completion), request_cancel)


class WindowsUploadWorker:
    maximum_payload_bytes = MAX_UPLOAD_OUTPUT_BYTES

    def __init__(self, *, python: Path, source_root: Path, private_root: Path,
                 slots: int, timeout_seconds: float) -> None:
        self._loop = asyncio.ProactorEventLoop()
        self._worker = UploadWorkerSupervisor(python=python, source_root=source_root, private_root=private_root,
                                               slots=slots, timeout_seconds=timeout_seconds)
        self._thread = threading.Thread(target=self._loop.run_forever, name="aletheia-upload-worker", daemon=True)
        self._thread.start()
        self._closed = False
        self._closing: asyncio.Task[None] | None = None

    async def run_upload(self, payload: bytes) -> bytes:
        if self._closed:
            raise RuntimeError("worker unavailable")
        return await _call_on_loop(self._loop, lambda: self._worker.run_upload(payload))

    async def aclose(self) -> None:
        if self._closing is None:
            self._closed = True
            self._closing = asyncio.create_task(self._close())
        await _wait_finished(self._closing)

    async def _close(self) -> None:
        try:
            await _call_on_loop(self._loop, self._worker.aclose)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            await asyncio.to_thread(self._thread.join, 5)
            if self._thread.is_alive():
                raise RuntimeError("worker shutdown did not complete")
            self._loop.close()
