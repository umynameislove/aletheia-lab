"""Opt-in fail-closed clock gate for hosted session and capability reads.

Production wiring must also gate login, mutations and workers. This wrapper is
not a substitute for time synchronization or operational monitoring.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from aletheia_lab.hosted_contract.session_capabilities import Capabilities, Session

from .application import HostedBackend

ClockCheck = Callable[[], Awaitable[bool]]


class ClockUnavailable(TimeoutError):
    def __init__(self) -> None:
        super().__init__("Service clock is unavailable.")


class ClockGuardedBackend:
    """Recheck the supplied trusted clock predicate before every read."""

    def __init__(self, backend: HostedBackend, clock_check: ClockCheck) -> None:
        self._backend, self._clock_check = backend, clock_check

    async def _check(self) -> bool:
        try:
            return await self._clock_check() is True
        except Exception:
            return False

    async def is_ready(self) -> bool:
        try:
            return await self._check() and await self._backend.is_ready() is True
        except Exception:
            return False

    async def load_session(self, cookie: bytes | None) -> Session:
        if not await self._check():
            raise ClockUnavailable()
        session = await self._backend.load_session(cookie)
        if not await self._check():
            raise ClockUnavailable()
        return session

    async def capabilities(self, session: Session, workspace_id: str) -> Capabilities:
        if not await self._check():
            raise ClockUnavailable()
        capabilities = await self._backend.capabilities(session, workspace_id)
        if not await self._check():
            raise ClockUnavailable()
        return capabilities
