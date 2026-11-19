"""Await bounded blocking work through completion before releasing its caller."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from .observability import process_telemetry, timed_stage
from .resource_admission import ProcessSlots, ResourceBusy

_P = ParamSpec('_P')
_T = TypeVar('_T')

# API request admission and worker job slots are independent of the executor.
# Keep this gate before to_thread so its otherwise-unbounded queue stays empty.
BLOCKING_SLOTS_PER_PROCESS = 2
BLOCKING_WAITERS_PER_PROCESS = 32
_BLOCKING_SLOTS = ProcessSlots(BLOCKING_SLOTS_PER_PROCESS, BLOCKING_WAITERS_PER_PROCESS)


def blocking_slots() -> ProcessSlots:
    return _BLOCKING_SLOTS


async def _run_blocking(wait: bool, call: Callable[_P, _T], /, *args: _P.args, **kwargs: _P.kwargs) -> _T:
    slots = blocking_slots()
    telemetry = process_telemetry()
    try:
        await slots.acquire(wait=wait)
    except ResourceBusy:
        telemetry.increment('blocking_rejected')
        raise
    telemetry.set_gauge('blocking_active', slots.snapshot().active)
    telemetry.set_gauge('blocking_waiting', slots.snapshot().waiting)
    try:
        def measured() -> _T:
            with timed_stage('blocking_execution'):
                return call(*args, **kwargs)

        task = asyncio.create_task(asyncio.to_thread(measured))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cleanup = asyncio.gather(task, return_exceptions=True)
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            raise
    finally:
        slots.release()
        telemetry.set_gauge('blocking_active', slots.snapshot().active)
        telemetry.set_gauge('blocking_waiting', slots.snapshot().waiting)


async def run_blocking(call: Callable[_P, _T], /, *args: _P.args, **kwargs: _P.kwargs) -> _T:
    return await _run_blocking(True, call, *args, **kwargs)


async def run_blocking_nowait(call: Callable[_P, _T], /, *args: _P.args, **kwargs: _P.kwargs) -> _T:
    """For short filesystem effects under a DB fence: never wait holding the connection."""
    return await _run_blocking(False, call, *args, **kwargs)
