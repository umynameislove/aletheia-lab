"""Required shared-admission startup checks; no service/secret dependencies."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from test_k02_runtime_lifecycle import _settings

from aletheia_lab.hosted import managed_runtime
from aletheia_lab.hosted_auth.settings import AuthConfigurationRejected, AuthPolicy


async def _verified_binding(actual_pool, storage, *, strict=False):
    assert type(actual_pool) is object
    assert storage is not None and strict is True
    return True


def test_runtime_refuses_missing_shared_namespace_before_acquiring_resources(tmp_path, monkeypatch):
    monkeypatch.setattr(managed_runtime, "load_runtime_settings", lambda path: _settings(tmp_path))
    monkeypatch.setattr(managed_runtime, "load_auth_policy", lambda path: AuthPolicy(default_return_to="/app", return_to_paths=("/app",)))
    with pytest.raises(AuthConfigurationRejected):
        managed_runtime.ManagedRuntime({"ALETHEIA_HOSTED_CONFIG": "unused", "ALETHEIA_HOSTED_AUTH_POLICY": "unused"})


@pytest.mark.parametrize("reason", ["missing_key", "missing_table", "policy_drift", "lookup_timeout"])
def test_runtime_never_publishes_auth_when_shared_store_cannot_be_prepared(tmp_path, monkeypatch, reason):
    events = []
    runtime = _settings(tmp_path)
    runtime = runtime.model_copy(update={"api": runtime.api.model_copy(update={"backend_timeout_seconds": 0.03})})
    policy = AuthPolicy(default_return_to="/app", return_to_paths=("/app",),
        admission_namespace="test-runtime", idp_timeout_seconds=0.01)
    monkeypatch.setattr(managed_runtime, "load_runtime_settings", lambda path: runtime)
    monkeypatch.setattr(managed_runtime, "load_auth_policy", lambda path: policy)
    monkeypatch.setattr(managed_runtime, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(managed_runtime, "LoginProofCipher", lambda *args: object())

    @asynccontextmanager
    async def pool(*args, **kwargs):
        events.append("pool-open")
        try:
            yield object()
        finally:
            events.append("pool-close")

    class Store:
        def __init__(self, *args, **kwargs):
            pass

        async def prepare(self, policy):
            events.append("prepare")
            if reason == "lookup_timeout":
                await asyncio.Event().wait()
            raise RuntimeError("synthetic-private-admission-error")

    monkeypatch.setattr(managed_runtime, "verify_runtime_binding", _verified_binding)
    monkeypatch.setattr(managed_runtime, "postgres_pool", pool)
    monkeypatch.setattr(managed_runtime, "PostgresAuthAdmission", Store)
    env = {"ALETHEIA_HOSTED_CONFIG": "unused", "ALETHEIA_HOSTED_AUTH_POLICY": "unused",
           "ALETHEIA_HOSTED_DATABASE_URL": "postgresql://u:p@localhost/db",
           "ALETHEIA_HOSTED_LOGIN_PROOF_KEY": "aw==", "ALETHEIA_HOSTED_CSRF_SECRET": "s" * 43}
    if reason != "missing_key":
        env["ALETHEIA_HOSTED_ADMISSION_SECRET"] = "r" * 43
    (tmp_path / "objects").mkdir()

    async def scenario():
        incoming = iter(({"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}))
        messages = []

        async def receive():
            return next(incoming)

        async def send(event):
            messages.append(event)

        await managed_runtime.ManagedRuntime(env)({"type": "lifespan"}, receive, send)
        assert [message["type"] for message in messages] == ["lifespan.startup.failed"]
        assert "synthetic-private" not in repr(messages)
        assert events == ([] if reason == "missing_key" else ["pool-open", "prepare", "pool-close"])
    asyncio.run(scenario())
