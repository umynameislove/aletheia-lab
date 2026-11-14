"""Optional follow-up composition and current readiness use the same real queue port."""
from __future__ import annotations

import asyncio
import base64
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from test_k02_read_backend import _backend
from test_k02_runtime_lifecycle import _settings

from aletheia_lab.hosted import managed_runtime, upload_runtime
from aletheia_lab.hosted.application import AccessDenied
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_contract.session_capabilities import Membership


@pytest.mark.parametrize("role,allowed", [("owner", True), ("admin", True), ("analyst", True),
                                          ("reviewer", False), ("viewer", False)])
def test_followup_capability_uses_current_role_and_current_queue_readiness(role, allowed):
    async def scenario():
        base, sessions, workspaces = _backend()
        ready = AsyncMock(return_value=True)
        jobs = SimpleNamespace(is_ready=AsyncMock(return_value=True), controls_ready=AsyncMock(return_value=True),
                               follow_up_ready=ready)
        backend = upload_runtime.UploadReadBackend(sessions, workspaces, limits=base._limits, clock=base._clock,
            catalog_available=True, jobs=jobs, follow_up_available=True)
        session = await backend.load_session(b"session=synthetic")
        workspaces.membership = Membership(role=role, revision=7)
        assert (await backend.capabilities(session, "ws_synthetic_001")).features["projects.follow_up"] is allowed
        ready.return_value = False
        assert (await backend.capabilities(session, "ws_synthetic_001")).features["projects.follow_up"] is False
        workspaces.active = False
        with pytest.raises(AccessDenied):
            await backend.capabilities(session, "ws_synthetic_001")
        assert ready.await_count == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("catalog,flag", [(False, False), (True, False), (False, True)])
def test_default_or_incomplete_followup_profile_has_no_capability_hint_or_port_probe(catalog, flag):
    async def scenario():
        base, sessions, workspaces = _backend()
        ready = AsyncMock(return_value=True)
        jobs = SimpleNamespace(is_ready=AsyncMock(return_value=True), controls_ready=AsyncMock(return_value=True),
                               follow_up_ready=ready)
        backend = upload_runtime.UploadReadBackend(sessions, workspaces, limits=base._limits, clock=base._clock,
            catalog_available=catalog, jobs=jobs, follow_up_available=flag)
        session = await backend.load_session(b"session=synthetic")
        assert "projects.follow_up" not in (await backend.capabilities(session, "ws_synthetic_001")).features
        ready.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize("enabled,resumable", [(False, False), (False, True), (True, False), (True, True)])
def test_followup_mount_is_explicit_and_uses_same_raw_queue_in_both_upload_profiles(tmp_path, monkeypatch, enabled, resumable):
    calls = []

    def passthrough(fallback, *args, **kwargs):
        return fallback

    def followup(fallback, jobs, *args, **kwargs):
        calls.append(jobs)
        return fallback

    for name in ("CallbackApplication", "LoginStartApplication", "LogoutApplication", "SessionMutationApplication",
                 "SourceUploadApplication", "JobApplication", "CatalogApplication", "AuthAdmissionApplication",
                 "UploadStagingApplication", "ImportApplication", "RuntimeManagementApplication",
                 "ProjectRefreshApplication", "ProjectAnalysisApplication"):
        monkeypatch.setattr(upload_runtime, name, passthrough)
    monkeypatch.setattr(upload_runtime, "ProjectOperationsApplication", followup)
    monkeypatch.setattr(upload_runtime, "PreviewLeases", lambda sources: object())
    monkeypatch.setattr(upload_runtime, "ImportFailures", lambda pool: object())
    monkeypatch.setattr(upload_runtime, "LeasedJobs", lambda *args: object())
    raw_jobs = object()
    upload_runtime.compose_upload_runtime(settings=_settings(tmp_path), sessions=object(), workspaces=object(),
        sources=SimpleNamespace(maximum_upload_bytes=200 * 1024 * 1024, _pool=object()), jobs=raw_jobs,
        tokens=object(), coordinator=object(), auth_policy=AuthPolicy(default_return_to="/app", return_to_paths=("/app",)),
        limits=_backend()[0]._limits,
        clock_check=AsyncMock(return_value=True), admission=object(), admission_subject_key=SecretStr("s" * 43),
        catalog_access=object(), project_access=object(), reviews=object(), project_mutations=object(),
        resumable_uploads=resumable, follow_up_available=enabled, runtime_management=object())
    assert calls == ([raw_jobs] if enabled else [])


def test_followup_mount_rejects_missing_catalog_profile(tmp_path):
    with pytest.raises(ValueError):
        upload_runtime.compose_upload_runtime(settings=_settings(tmp_path), sessions=object(), workspaces=object(),
            sources=SimpleNamespace(maximum_upload_bytes=1000), jobs=object(), tokens=object(), coordinator=object(),
            auth_policy=AuthPolicy(default_return_to="/app", return_to_paths=("/app",)), limits=_backend()[0]._limits,
            clock_check=AsyncMock(return_value=True),
            admission=object(), admission_subject_key=SecretStr("s" * 43), follow_up_available=True)


def _managed(tmp_path, monkeypatch, *, full=True, staging=True, leases=True, followup=True, management=True,
             polling=True, retention=True):
    settings = _settings(tmp_path)
    settings = settings.model_copy(update={"api": settings.api.model_copy(update={
        "startup_timeout_seconds": 0.05, "backend_timeout_seconds": 0.05}), "storage": settings.storage.model_copy(update={
            "expected_schema": "synthetic_schema" if full else None,
            "expected_database_role": "synthetic_api" if full else None})})
    policy = AuthPolicy(default_return_to="/app", return_to_paths=("/app",), admission_namespace="test-followup")
    probes = SimpleNamespace(staging=AsyncMock(return_value=staging), leases=AsyncMock(return_value=leases),
        followup=AsyncMock(return_value=followup), jobs=AsyncMock(return_value=True),
        execution=AsyncMock(return_value=True),
        polling=AsyncMock(return_value=polling), retention=AsyncMock(return_value=retention),
        management=AsyncMock(return_value=management),
        workspace=AsyncMock(return_value=True),
        catalog=AsyncMock(return_value=True), admission=AsyncMock(return_value=True),
        clock=AsyncMock(return_value=True), close=AsyncMock())
    captured = {}

    @asynccontextmanager
    async def connection():
        yield object()

    pool_resource = SimpleNamespace(connection=connection)

    @asynccontextmanager
    async def pool(*args, **kwargs):
        yield pool_resource

    class Sources:
        def __init__(self, pool, *, maximum_upload_bytes, maximum_expanded_bytes, maximum_entries, audit=False, catalog=None):
            self._pool = pool
            self.maximum_upload_bytes = maximum_upload_bytes
            self.maximum_workspace_bytes = 8 * 200 * 1024 * 1024
            self.maximum_expanded_bytes = maximum_expanded_bytes
            self.maximum_entries = maximum_entries
            self.is_upload_ready = probes.staging
            captured["sources"] = self
            captured["source_catalog"] = catalog

    class Jobs:
        def __init__(self, pool, sources, *, audit=False, catalog=None, execution, analysis_model):
            assert execution is True and analysis_model == "upload-snapshot-metric-observation/v2"
            self.execution = True
            self.execution_ready = probes.execution
            self.is_ready, self.follow_up_ready = probes.jobs, probes.followup
            captured.update(queue=self, queue_catalog=catalog)

    class Oidc:
        async def _current(self):
            pass

    def compose(**kwargs):
        captured.update(kwargs)
        return object()

    def runtime_management(pool, *, audit=False, platform_limits):
        value = SimpleNamespace(is_ready=probes.management)
        captured.update(management_port=value, management_limits=platform_limits)
        return value

    monkeypatch.setattr(managed_runtime, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(managed_runtime, "resolve_runtime_secrets", lambda *args: SimpleNamespace(oidc_client_secret=None))
    monkeypatch.setattr(managed_runtime, "postgres_pool", pool)
    for name in ("verify_runtime_binding", "verify_catalog", "verify_project_policy", "verify_reviews"):
        monkeypatch.setattr(managed_runtime, name, AsyncMock(return_value=True))
    monkeypatch.setattr(managed_runtime, "FilesystemObjectStore", lambda *args: object())
    monkeypatch.setattr(managed_runtime, "ReportRenderer", lambda **kwargs: SimpleNamespace(aclose=probes.close))
    monkeypatch.setattr(managed_runtime, "CatalogAccess", lambda *args: SimpleNamespace(is_ready=probes.catalog))
    monkeypatch.setattr(managed_runtime, "PostgresSources", Sources)
    monkeypatch.setattr(managed_runtime, "PostgresJobs", Jobs)
    monkeypatch.setattr(managed_runtime, "PostgresJobUpdates", lambda jobs, cursors:
                        SimpleNamespace(ready=probes.polling))
    monkeypatch.setattr(managed_runtime, "PostgresRetention", lambda pool, catalog:
                        SimpleNamespace(is_ready=probes.retention))
    monkeypatch.setattr(managed_runtime, "RuntimeManagement", runtime_management)
    monkeypatch.setattr(managed_runtime, "PostgresWorkspaceManagement", lambda *args, **kwargs:
        SimpleNamespace(is_ready=probes.workspace))
    monkeypatch.setattr(managed_runtime, "PreviewLeases", lambda sources: SimpleNamespace(is_ready=probes.leases))
    monkeypatch.setattr(managed_runtime, "PostgresAuthAdmission", lambda *args, **kwargs:
        SimpleNamespace(prepare=AsyncMock(), is_ready=probes.admission))
    monkeypatch.setattr(managed_runtime, "RefreshingOidc", lambda *args, **kwargs: Oidc())
    monkeypatch.setattr(managed_runtime, "LoginCoordinator", lambda *args, **kwargs: object())
    monkeypatch.setattr(managed_runtime, "probe_clock", probes.clock)
    monkeypatch.setattr(managed_runtime, "compose_upload_runtime", compose)
    runtime = object.__new__(managed_runtime.ManagedRuntime)
    runtime._env = {"ALETHEIA_HOSTED_LOGIN_PROOF_KEY": base64.b64encode(b"k" * 32).decode(),
        "ALETHEIA_HOSTED_CSRF_SECRET": "s" * 43, "ALETHEIA_HOSTED_ADMISSION_SECRET": "r" * 43}
    runtime._settings, runtime._policy, runtime._admission_namespace = settings, policy, "test-followup"
    return runtime, captured, probes


def test_managed_full_profile_injects_catalog_and_preserves_k07_budgets(tmp_path, monkeypatch):
    runtime, captured, probes = _managed(tmp_path, monkeypatch)

    async def scenario():
        async with AsyncExitStack() as stack:
            await runtime._compose(stack)
            assert captured["queue_catalog"] is not None
            assert captured["source_catalog"] is captured["queue_catalog"]
            assert captured["jobs"] is captured["queue"]
            assert captured["runtime_management"] is captured["management_port"]
            assert captured["management_limits"].maximum_upload_bytes == 200 * 1024 * 1024
            assert captured["management_limits"].maximum_workspace_bytes == 8 * 200 * 1024 * 1024
            assert captured["follow_up_available"] is True and captured["resumable_uploads"] is True
            assert (captured["sources"].maximum_upload_bytes, captured["sources"].maximum_expanded_bytes,
                    captured["sources"].maximum_entries) == (200 * 1024 * 1024, 512 * 1024 * 1024, 100000)
            assert await captured["clock_check"]() is True
        probes.close.assert_awaited_once()
    asyncio.run(scenario())


def test_legacy_catalog_none_never_enables_or_probes_followup(tmp_path, monkeypatch):
    runtime, captured, probes = _managed(tmp_path, monkeypatch, full=False, staging=False, leases=False)

    async def scenario():
        async with AsyncExitStack() as stack:
            await runtime._compose(stack)
            assert captured["queue_catalog"] is None and captured["follow_up_available"] is False
            assert captured["source_catalog"] is None
            assert captured["runtime_management"] is None
            assert await captured["clock_check"]() is True
        probes.followup.assert_not_awaited()
        probes.management.assert_not_awaited()
    asyncio.run(scenario())


def test_disabled_upload_profile_rejects_hot_upgrade_until_recomposed(tmp_path, monkeypatch):
    runtime, captured, probes = _managed(tmp_path, monkeypatch, staging=False, leases=False, followup=False)

    async def scenario():
        async with AsyncExitStack() as stack:
            await runtime._compose(stack)
            assert captured["resumable_uploads"] is False and captured["follow_up_available"] is False
            assert captured["management_limits"].maximum_upload_bytes == 10 * 1024 * 1024
            probes.staging.return_value = probes.leases.return_value = probes.followup.return_value = True
            assert await captured["clock_check"]() is False
        assert probes.staging.await_count == probes.leases.await_count == 2
        assert probes.followup.await_count == 1
    asyncio.run(scenario())


def test_false_followup_profile_stays_disabled_while_complete_upload_profile_is_rechecked(tmp_path, monkeypatch):
    runtime, captured, probes = _managed(tmp_path, monkeypatch, followup=False)

    async def scenario():
        async with AsyncExitStack() as stack:
            await runtime._compose(stack)
            assert captured["follow_up_available"] is False and captured["resumable_uploads"] is True
            probes.followup.return_value = True
            assert await captured["clock_check"]() is True
        assert probes.followup.await_count == 1
        assert probes.staging.await_count == probes.leases.await_count == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("staging,leases", [(True, False), (False, True)])
def test_half_upgraded_upload_preview_schema_is_rejected_at_startup(tmp_path, monkeypatch, staging, leases):
    runtime, _, probes = _managed(tmp_path, monkeypatch, staging=staging, leases=leases)

    async def scenario():
        async with AsyncExitStack() as stack:
            with pytest.raises(RuntimeError, match="must be ready together"):
                await runtime._compose(stack)
        assert probes.staging.await_count == probes.leases.await_count == 1
        probes.followup.assert_not_awaited()
        probes.management.assert_not_awaited()
        probes.close.assert_awaited_once()
    asyncio.run(scenario())


def test_required_runtime_management_schema_failure_rejects_startup(tmp_path, monkeypatch):
    runtime, _, probes = _managed(tmp_path, monkeypatch, management=False)

    async def scenario():
        async with AsyncExitStack() as stack:
            with pytest.raises(RuntimeError, match="Runtime operation schema is unavailable"):
                await runtime._compose(stack)
        probes.management.assert_awaited_once()
        probes.close.assert_awaited_once()
    asyncio.run(scenario())


@pytest.mark.parametrize("port", ["staging", "leases", "followup", "management", "polling", "retention"])
def test_active_optional_port_loss_fails_readiness_closed(tmp_path, monkeypatch, port):
    runtime, captured, probes = _managed(tmp_path, monkeypatch)

    async def scenario():
        async with AsyncExitStack() as stack:
            await runtime._compose(stack)
            assert await captured["clock_check"]() is True
            getattr(probes, port).return_value = False
            assert await captured["clock_check"]() is False
    asyncio.run(scenario())


@pytest.mark.parametrize("port", ["staging", "leases", "followup", "management", "polling", "retention"])
@pytest.mark.parametrize("phase", ["startup", "readiness"])
def test_optional_port_hang_is_bounded_at_startup_and_readiness(tmp_path, monkeypatch, port, phase):
    runtime, captured, probes = _managed(tmp_path, monkeypatch)

    async def blocked():
        await asyncio.Event().wait()

    async def scenario():
        async with AsyncExitStack() as stack:
            if phase == "startup":
                getattr(probes, port).side_effect = blocked
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(runtime._compose(stack), 0.5)
            else:
                await runtime._compose(stack)
                getattr(probes, port).side_effect = blocked
                assert await asyncio.wait_for(captured["clock_check"](), 0.5) is False
        probes.close.assert_awaited_once()
    asyncio.run(scenario())


@pytest.mark.parametrize('port', ['polling', 'retention'])
def test_new_required_polling_and_retention_ports_deny_startup_when_false(tmp_path, monkeypatch, port):
    runtime, captured, probes = _managed(tmp_path, monkeypatch, **{port: False})

    async def scenario():
        async with AsyncExitStack() as stack:
            with pytest.raises(RuntimeError, match='Current polling or retention schema is unavailable'):
                await runtime._compose(stack)
        getattr(probes, port).assert_awaited_once_with()
        probes.staging.assert_not_awaited()
        probes.leases.assert_not_awaited()
        probes.management.assert_not_awaited()
        probes.close.assert_awaited_once_with()
        assert 'clock_check' not in captured
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['false', 'hang', 'cancelled'])
def test_current_job_execution_profile_startup_denial_is_bounded_and_cleans_resources(tmp_path, monkeypatch, failure):
    runtime, captured, probes = _managed(tmp_path, monkeypatch)
    if failure == 'false':
        probes.execution.return_value = False
        expected = RuntimeError
    elif failure == 'hang':
        async def hung():
            await asyncio.Event().wait()
        probes.execution.side_effect = hung
        expected = TimeoutError
    else:
        probes.execution.side_effect = asyncio.CancelledError()
        expected = asyncio.CancelledError

    async def scenario():
        with pytest.raises(expected):
            async with asyncio.timeout(1) as guard:
                try:
                    async with AsyncExitStack() as stack:
                        await runtime._compose(stack)
                finally:
                    assert not guard.expired(), 'The configured startup deadline, not the outer test guard, must stop startup.'
        probes.execution.assert_awaited_once_with()
        probes.staging.assert_not_awaited()
        probes.leases.assert_not_awaited()
        probes.followup.assert_not_awaited()
        probes.management.assert_not_awaited()
        probes.close.assert_awaited_once_with()
        assert 'clock_check' not in captured
    asyncio.run(scenario())
