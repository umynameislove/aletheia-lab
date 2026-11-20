"""Audit startup failures release resources before any API/worker effects start."""
import asyncio
import base64
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_k02_runtime_lifecycle import _settings

from aletheia_lab.hosted import managed_runtime, managed_worker
from aletheia_lab.hosted_auth.settings import AuthPolicy


@pytest.mark.parametrize("surface", ["api", "worker"])
@pytest.mark.parametrize("failure", ["binding", "audit", "timeout", "cancelled"])
def test_binding_precedes_audit_and_failed_startup_closes_pool_without_running_jobs(tmp_path, monkeypatch, surface, failure):
    events, messages = [], []
    settings = _settings(tmp_path)
    settings = settings.model_copy(update={"storage": settings.storage.model_copy(update={
        "expected_schema": "k06_synthetic", "expected_database_role": "k06_synthetic_api"})})
    Path(settings.storage.object_root).mkdir(parents=True, exist_ok=True)
    policy = AuthPolicy(default_return_to="/app", return_to_paths=("/app",), admission_namespace="k06-runtime")
    module = managed_runtime if surface == "api" else managed_worker
    monkeypatch.setattr(module, "load_runtime_settings", lambda path: settings)
    monkeypatch.setattr(module, "load_auth_policy", lambda path: policy)

    @asynccontextmanager
    async def connection():
        yield object()

    @asynccontextmanager
    async def pool(*args, **kwargs):
        events.append("pool-open")
        try:
            yield SimpleNamespace(connection=connection)
        finally:
            events.append("pool-close")

    async def binding(*args, **kwargs):
        events.append("binding")
        return failure != "binding"

    async def canonical(*args):
        events.append("canonical")
        return True

    async def projects(*args):
        events.append("projects")
        return True

    async def reviews(*args):
        events.append("reviews")
        return True

    async def audit(*args):
        events.append("audit")
        if failure == "timeout":
            raise TimeoutError("synthetic diagnostic must not reach response")
        if failure == "cancelled":
            raise asyncio.CancelledError()
        return False

    def forbidden(*args, **kwargs):
        raise AssertionError("Failed preflight must not create effect repositories or workers")

    monkeypatch.setattr(module, "postgres_pool", pool)
    monkeypatch.setattr(module, "verify_runtime_binding", binding)
    monkeypatch.setattr(module, "verify_action_audit", audit)
    monkeypatch.setattr(module, "PostgresSources", forbidden)
    environment = {"ALETHEIA_HOSTED_CONFIG": "synthetic", "ALETHEIA_HOSTED_AUTH_POLICY": "synthetic",
        "ALETHEIA_HOSTED_ACTION_AUDIT": "enabled", "ALETHEIA_HOSTED_DATABASE_URL": "postgresql://synthetic@localhost/synthetic",
        "ALETHEIA_HOSTED_LOGIN_PROOF_KEY": base64.b64encode(b"k" * 32).decode(),
        "ALETHEIA_HOSTED_CSRF_SECRET": "s" * 43, "ALETHEIA_HOSTED_ADMISSION_SECRET": "r" * 43}
    if surface == "api":
        monkeypatch.setattr(module, "verify_catalog", canonical)
        monkeypatch.setattr(module, "verify_project_policy", projects)
        monkeypatch.setattr(module, "verify_reviews", reviews)
        monkeypatch.setattr(module, "RefreshingOidc", forbidden)
        monkeypatch.setattr(module, "PostgresWorkspaceManagement", forbidden)
    else:
        monkeypatch.setattr(module, "PayloadUploadWorkerSupervisor", forbidden)
        monkeypatch.setattr(module, "WindowsUploadWorker", forbidden)

    async def scenario():
        if surface == "api":
            runtime = managed_runtime.ManagedRuntime(environment)

            async def receive():
                return {"type": "lifespan.startup"}

            async def send(message):
                messages.append(message)

            await runtime({"type": "lifespan"}, receive, send)
        else:
            await managed_worker.run_worker(environment, asyncio.Event())

    if failure == "cancelled":
        expected = asyncio.CancelledError
    elif surface == "worker":
        expected = TimeoutError if failure == "timeout" else RuntimeError
    else:
        expected = None
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        if expected is None:
            runner.run(scenario())
            assert messages == [{"type": "lifespan.startup.failed", "message": "Runtime dependencies are unavailable."}]
        else:
            with pytest.raises(expected):
                runner.run(scenario())
            assert not messages
    assert events == ["pool-open", "binding", *(["canonical", "projects", "reviews"] if surface == "api" and failure != "binding" else []),
                      *(["audit"] if failure != "binding" else []), "pool-close"]
