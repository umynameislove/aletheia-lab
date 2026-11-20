"""A clock failure cannot leak an otherwise valid private response."""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from aletheia_lab.hosted.clock_guarded_backend import ClockGuardedBackend, ClockUnavailable
from aletheia_lab.hosted_contract.session_capabilities import Actor, Session, SessionInfo


class Backend:
    def __init__(self):
        self.calls = 0

    async def is_ready(self):
        return True

    async def load_session(self, cookie):
        self.calls += 1
        return Session(schema_version="product-api/v1-draft", actor=Actor(user_id="u1", display_name="User",
            email="u@example.invalid", email_verified=True),
            session=SessionInfo(context_id="ctx1", expires_at=datetime.now(UTC) + timedelta(minutes=5)),
            csrf_token="synthetic", onboarding="needs_workspace", last_workspace_id=None)

    async def capabilities(self, session, workspace_id):
        raise AssertionError("not used")


def test_clock_denial_prevents_backend_and_readiness():
    async def run():
        backend = Backend()
        async def bad_clock():
            return False
        guarded = ClockGuardedBackend(backend, bad_clock)
        assert not await guarded.is_ready()
        with pytest.raises(ClockUnavailable):
            await guarded.load_session(b"cookie")
        assert backend.calls == 0
    asyncio.run(run())


def test_clock_failure_after_read_does_not_return_session():
    async def run():
        backend = Backend()
        checks = iter([True, False])
        async def changing_clock():
            return next(checks)
        guarded = ClockGuardedBackend(backend, changing_clock)
        with pytest.raises(ClockUnavailable):
            await guarded.load_session(b"cookie")
        assert backend.calls == 1
    asyncio.run(run())


def test_opt_in_runtime_does_not_start_when_clock_is_invalid(tmp_path):
    from test_k02_runtime_lifecycle import _lifespan, _settings

    from aletheia_lab.hosted.composition import ProcessApplication

    cleanup = []

    async def bad_clock():
        return False

    @asynccontextmanager
    async def resources():
        try:
            yield ClockGuardedBackend(Backend(), bad_clock)
        finally:
            cleanup.append(True)

    messages = _lifespan(ProcessApplication(_settings(tmp_path), resources), [{"type": "lifespan.startup"}])
    assert messages[0]["type"] == "lifespan.startup.failed"
    assert cleanup == [True]
