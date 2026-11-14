"""Nonwaiting process-local permits; overload never creates a hidden task queue."""
from __future__ import annotations

import asyncio
import threading
from collections import deque
from dataclasses import dataclass

SOURCE_COMPLETION_BYTES_PER_PROCESS = 400 * 1024 * 1024


class ResourceBusy(TimeoutError):
    """No work was started; callers may retain their intent and retry later."""


@dataclass(frozen=True)
class AdmissionSnapshot:
    active: int
    capacity: int
    rejected: int
    peak_active: int
    waiting: int = 0


class ImmediateSlots:
    """One event-loop owner, bounded active work and zero queued waiters."""

    def __init__(self, capacity: int) -> None:
        if type(capacity) is not int or not 1 <= capacity <= 10000:
            raise ValueError("A bounded positive slot count is required.")
        self.capacity = capacity
        self._active = self._rejected = self._peak = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None or self._loop.is_closed() and self._active == 0:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("Permits must stay on their owning event loop.")
        if self._active >= self.capacity:
            self._rejected += 1
            raise ResourceBusy("Process resource capacity is temporarily unavailable.")
        self._active += 1
        self._peak = max(self._peak, self._active)

    def release(self) -> None:
        if self._loop is not asyncio.get_running_loop() or self._active < 1:
            raise RuntimeError("Only an acquired permit can be released by its owner.")
        self._active -= 1

    def snapshot(self) -> AdmissionSnapshot:
        return AdmissionSnapshot(self._active, self.capacity, self._rejected, self._peak)


@dataclass(eq=False)
class _Waiter:
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[None]
    granted: bool = False


class ProcessSlots:
    """Bounded FIFO admission shared by API and Windows bridge event loops."""

    def __init__(self, capacity: int, maximum_waiting: int = 32) -> None:
        if (type(capacity) is not int or not 1 <= capacity <= 10000
                or type(maximum_waiting) is not int or not 0 <= maximum_waiting <= 10000):
            raise ValueError("A bounded positive slot count is required.")
        self.capacity, self.maximum_waiting = capacity, maximum_waiting
        self._active = self._rejected = self._peak = 0
        self._lock = threading.Lock()
        self._waiting: deque[_Waiter] = deque()

    async def acquire(self, *, wait: bool = True) -> None:
        if type(wait) is not bool:
            raise ValueError('An explicit queue decision is required.')
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._active < self.capacity:
                self._active += 1
                self._peak = max(self._peak, self._active)
                return
            if not wait or len(self._waiting) >= self.maximum_waiting:
                self._rejected += 1
                raise ResourceBusy("Process resource capacity is temporarily unavailable.")
            waiter = _Waiter(loop, loop.create_future())
            self._waiting.append(waiter)
        try:
            await asyncio.shield(waiter.future)
        except asyncio.CancelledError:
            with self._lock:
                if waiter.granted:
                    self._release_locked()
                else:
                    self._waiting.remove(waiter)
            waiter.future.cancel()
            raise

    @staticmethod
    def _deliver(waiter: _Waiter) -> None:
        if not waiter.future.done():
            waiter.future.set_result(None)

    def _release_locked(self) -> None:
        if self._active < 1:
            raise RuntimeError("Only an acquired permit can be released.")
        self._active -= 1
        while self._waiting:
            waiter = self._waiting.popleft()
            if waiter.loop.is_closed():
                continue
            waiter.granted = True
            self._active += 1
            try:
                waiter.loop.call_soon_threadsafe(self._deliver, waiter)
            except RuntimeError:
                waiter.granted = False
                self._active -= 1
                continue
            break

    def release(self) -> None:
        with self._lock:
            self._release_locked()

    def snapshot(self) -> AdmissionSnapshot:
        with self._lock:
            return AdmissionSnapshot(self._active, self.capacity, self._rejected, self._peak, len(self._waiting))


class ByteAdmission:
    """Reserve known payload bytes before materialization; no byte wait queue."""

    def __init__(self, capacity: int) -> None:
        if type(capacity) is not int or capacity < 1:
            raise ValueError('A positive byte budget is required.')
        self.capacity = capacity
        self._active = self._peak = self._rejected = 0
        self._lock = threading.Lock()

    def acquire(self, byte_size: int) -> None:
        if type(byte_size) is not int or byte_size < 1:
            raise ValueError('A positive byte reservation is required.')
        with self._lock:
            if self._active + byte_size > self.capacity:
                self._rejected += 1
                raise ResourceBusy('Source materialization capacity is temporarily unavailable.')
            self._active += byte_size
            self._peak = max(self._peak, self._active)

    def release(self, byte_size: int) -> None:
        with self._lock:
            if type(byte_size) is not int or not 1 <= byte_size <= self._active:
                raise RuntimeError('Only reserved bytes can be released.')
            self._active -= byte_size

    def snapshot(self) -> AdmissionSnapshot:
        with self._lock:
            return AdmissionSnapshot(self._active, self.capacity, self._rejected, self._peak)


class RequestAdmission:
    """Reserve one of the configured request slots for bounded health probes."""

    def __init__(self, total: int) -> None:
        if type(total) is not int or not 1 <= total <= 10000:
            raise ValueError("A bounded request concurrency limit is required.")
        self.health_reserved = int(total > 1)
        self.regular = ImmediateSlots(total - self.health_reserved)
        self.health = ImmediateSlots(1) if self.health_reserved else self.regular

    def slots(self, path: str) -> ImmediateSlots:
        return self.health if path in ("/health/live", "/health/ready") else self.regular
