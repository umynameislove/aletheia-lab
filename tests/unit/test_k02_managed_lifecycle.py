"""Resource cleanup when managed startup is cancelled."""

import asyncio
import base64
import types
from contextlib import asynccontextmanager

import pytest
from test_k02_runtime_lifecycle import _settings

from aletheia_lab.hosted import managed_runtime
from aletheia_lab.hosted_auth.settings import AuthPolicy


@pytest.mark.parametrize("binding_ready", [True, False])
def test_binding_denial_or_startup_cancellation_closes_pool(tmp_path, monkeypatch, binding_ready):
    async def run():
        opened = asyncio.Event()
        closed = []
        settings = _settings(tmp_path)
        connection_pool = object()
        binding_calls = []

        @asynccontextmanager
        async def pool(*args, **kwargs):
            try:
                yield connection_pool
            finally:
                closed.append("pool")

        class Admission:
            def __init__(self, *args, **kwargs):
                pass

            async def prepare(self, policy):
                opened.set()
                await asyncio.Event().wait()

        async def binding(pool, storage, *, strict):
            assert pool is connection_pool and storage is settings.storage and strict is True
            binding_calls.append(True)
            return binding_ready

        monkeypatch.setattr(managed_runtime, "verify_runtime_binding", binding)
        monkeypatch.setattr(managed_runtime, "load_runtime_settings", lambda path: settings)
        monkeypatch.setattr(managed_runtime, "load_auth_policy", lambda path: AuthPolicy(
            default_return_to="/app", return_to_paths=("/app",), admission_namespace="test-runtime"))
        monkeypatch.setattr(managed_runtime, "sys", types.SimpleNamespace(platform="linux"))
        monkeypatch.setattr(managed_runtime, "postgres_pool", pool)
        monkeypatch.setattr(managed_runtime, "PostgresAuthAdmission", Admission)
        (tmp_path / "objects").mkdir()
        env = {"ALETHEIA_HOSTED_CONFIG": "unused", "ALETHEIA_HOSTED_AUTH_POLICY": "unused",
               "ALETHEIA_HOSTED_DATABASE_URL": "postgresql://u:p@localhost/db",
               "ALETHEIA_HOSTED_LOGIN_PROOF_KEY": base64.b64encode(b"k" * 32).decode(),
               "ALETHEIA_HOSTED_CSRF_SECRET": "s" * 43,
               "ALETHEIA_HOSTED_ADMISSION_SECRET": "r" * 43}
        runtime = managed_runtime.ManagedRuntime(env)
        messages = []

        async def receive():
            return {"type": "lifespan.startup"}

        async def send(message):
            messages.append(message)

        task = asyncio.create_task(runtime({"type": "lifespan"}, receive, send))
        if not binding_ready:
            await asyncio.wait_for(task, 1)
            assert binding_calls == [True] and not opened.is_set()
            assert closed == ["pool"]
            assert len(messages) == 1 and messages[0]["type"] == "lifespan.startup.failed"
            return
        await asyncio.wait_for(opened.wait(), 1)
        assert binding_calls == [True]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed == ["pool"]
        assert messages == []

    asyncio.run(run())
