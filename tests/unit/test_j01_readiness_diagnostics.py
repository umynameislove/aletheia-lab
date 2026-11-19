"""Readiness diagnostics use fixed categories without provider or DB details."""

import asyncio
import base64
import logging
import types
from contextlib import AsyncExitStack, asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from test_k02_runtime_lifecycle import _settings

from aletheia_lab.hosted import managed_runtime
from aletheia_lab.hosted_auth.settings import AuthPolicy

LOGGER = 'aletheia_lab.hosted.managed_runtime'
PRIVATE_DETAIL = 'postgresql://private:synthetic-secret@host/db'


@pytest.fixture
def readiness_clock(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    settings = settings.model_copy(update={'storage': settings.storage.model_copy(update={
        'expected_schema': 'j01_test_schema', 'expected_database_role': 'j01_test_api',
    })})
    policy = AuthPolicy(default_return_to='/app', return_to_paths=('/app',),
                        admission_namespace='test-readiness')
    jobs_ready = AsyncMock(return_value=True)
    admission_ready = AsyncMock(return_value=True)
    probe = AsyncMock(return_value=True)
    connection_resource = object()
    @asynccontextmanager
    async def connection():
        yield connection_resource
    pool_resource = types.SimpleNamespace(connection=connection)
    binding = AsyncMock(return_value=True)
    catalog_ready = AsyncMock(return_value=True)
    project_policy_ready = AsyncMock(return_value=True)
    reviews_ready = AsyncMock(return_value=True)
    catalog_health = AsyncMock(return_value=True)
    runtime_operations_ready = AsyncMock(return_value=True)
    runtime_operations = types.SimpleNamespace(is_ready=runtime_operations_ready)
    polling_ready = AsyncMock(return_value=True)
    retention_ready = AsyncMock(return_value=True)
    runtime_parameters = []
    renderer = types.SimpleNamespace(aclose=AsyncMock())
    def report_renderer(*, timeout_seconds):
        assert timeout_seconds == min(15, settings.api.backend_timeout_seconds)
        return renderer
    captured = {}

    @asynccontextmanager
    async def pool(runtime_settings, current_environment, *, role):
        assert runtime_settings is settings
        assert current_environment == environment
        assert role == 'api'
        yield pool_resource

    class Oidc:
        def __init__(self, *args, **kwargs):
            pass

        async def _current(self):
            pass

    def compose(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(managed_runtime, 'load_runtime_settings', lambda path: settings)
    monkeypatch.setattr(managed_runtime, 'load_auth_policy', lambda path: policy)
    monkeypatch.setattr(managed_runtime, 'sys', types.SimpleNamespace(platform='linux'))
    monkeypatch.setattr(managed_runtime, 'postgres_pool', pool)
    monkeypatch.setattr(managed_runtime, 'verify_runtime_binding', binding)
    monkeypatch.setattr(managed_runtime, 'verify_catalog', catalog_ready)
    monkeypatch.setattr(managed_runtime, 'verify_project_policy', project_policy_ready)
    monkeypatch.setattr(managed_runtime, 'verify_reviews', reviews_ready)
    monkeypatch.setattr(managed_runtime, 'ReportRenderer', report_renderer)
    monkeypatch.setattr(managed_runtime, 'RefreshingOidc', Oidc)
    monkeypatch.setattr(managed_runtime, 'PostgresAuthAdmission', lambda *args, **kwargs:
                        types.SimpleNamespace(prepare=AsyncMock(), is_ready=admission_ready))
    def job_repository(pool, sources, *, audit, catalog=None, execution, analysis_model):
        assert execution is True and analysis_model == "upload-snapshot-metric-observation/v2"
        assert pool is pool_resource and audit is False
        assert catalog is not None and catalog.pool is pool_resource
        captured['sources'] = sources
        return types.SimpleNamespace(execution=True, execution_ready=AsyncMock(return_value=True),
            is_ready=jobs_ready, follow_up_ready=AsyncMock(return_value=False))

    def catalog_access(catalog, cursors, current_renderer):
        assert catalog.pool is pool_resource and current_renderer is renderer
        return types.SimpleNamespace(is_ready=catalog_health)

    monkeypatch.setattr(managed_runtime, 'PostgresJobs', job_repository)
    monkeypatch.setattr(managed_runtime, 'CatalogAccess', catalog_access)
    def runtime_management(current_pool, *, audit, platform_limits):
        assert current_pool is pool_resource and audit is False
        assert platform_limits.maximum_upload_bytes == 10 * 1024 * 1024
        assert platform_limits.maximum_workspace_bytes == captured['sources'].maximum_workspace_bytes
        runtime_parameters.append(platform_limits)
        return runtime_operations
    monkeypatch.setattr(managed_runtime, 'RuntimeManagement', runtime_management)
    monkeypatch.setattr(managed_runtime, 'PostgresJobUpdates', lambda jobs, cursors:
                        types.SimpleNamespace(ready=polling_ready))
    monkeypatch.setattr(managed_runtime, 'PostgresRetention', lambda pool, catalog:
                        types.SimpleNamespace(is_ready=retention_ready))
    monkeypatch.setattr(managed_runtime, 'PostgresWorkspaceManagement', lambda *args, **kwargs:
                        types.SimpleNamespace(is_ready=AsyncMock(return_value=True)))
    monkeypatch.setattr(managed_runtime, 'probe_clock', probe)
    monkeypatch.setattr(managed_runtime, 'compose_upload_runtime', compose)
    (tmp_path / 'objects').mkdir()
    environment = {
        'ALETHEIA_HOSTED_CONFIG': 'unused',
        'ALETHEIA_HOSTED_AUTH_POLICY': 'unused',
        'ALETHEIA_HOSTED_DATABASE_URL': 'postgresql://u:p@localhost/db',
        'ALETHEIA_HOSTED_LOGIN_PROOF_KEY': base64.b64encode(b'k' * 32).decode(),
        'ALETHEIA_HOSTED_CSRF_SECRET': 's' * 43,
        'ALETHEIA_HOSTED_ADMISSION_SECRET': 'r' * 43,
    }

    @asynccontextmanager
    async def composed():
        runtime = managed_runtime.ManagedRuntime(environment)
        async with AsyncExitStack() as stack:
            await runtime._compose(stack)
            binding.assert_awaited_once_with(pool_resource, settings.storage, strict=True)
            catalog_ready.assert_awaited_once_with(connection_resource, settings.storage.expected_schema)
            project_policy_ready.assert_awaited_once_with(connection_resource, settings.storage.expected_schema)
            reviews_ready.assert_awaited_once_with(connection_resource, settings.storage.expected_schema)
            assert captured['runtime_management'] is runtime_operations
            assert len(runtime_parameters) == 1
            runtime_operations_ready.assert_awaited_once_with()
            yield captured['clock_check']
        renderer.aclose.assert_awaited_once_with()

    return composed, jobs_ready, admission_ready, probe, pool_resource, policy, settings, catalog_health, runtime_operations_ready


@pytest.mark.parametrize(
    'jobs_value,admission_value,clock_value,error_type,reason,counts',
    [
        (False, True, True, None, 'jobs_schema', (1, 0, 0)),
        (True, False, True, None, 'admission', (1, 1, 0)),
        (True, True, False, None, 'clock_bound', (1, 1, 1)),
        (True, True, True, TimeoutError, 'timeout', (1, 0, 0)),
        (True, True, True, RuntimeError, 'jobs_schema', (1, 0, 0)),
        (True, True, True, None, None, (1, 1, 1)),
    ],
    ids=['jobs-schema', 'admission', 'clock-bound', 'timeout', 'private-error', 'ready'],
)
def test_readiness_reports_only_fixed_failure_categories(
    readiness_clock, caplog, jobs_value, admission_value, clock_value, error_type, reason, counts,
):
    composed, jobs, admission, probe, pool, policy, settings, catalog_health, runtime_operations = readiness_clock
    jobs.return_value = jobs_value
    admission.return_value = admission_value
    probe.return_value = clock_value
    if error_type is not None:
        jobs.side_effect = error_type(PRIVATE_DETAIL)

    async def scenario():
        async with composed() as clock:
            assert await clock() is (reason is None)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        asyncio.run(scenario())
    assert (jobs.await_count, admission.await_count, probe.await_count) == counts
    assert runtime_operations.await_count == 1 + bool(counts[1])
    records = [record for record in caplog.records if record.name == LOGGER]
    expected = [] if reason is None else ['hosted_readiness_unavailable reason=' + reason]
    assert [record.getMessage() for record in records] == expected
    assert all(record.exc_info is None for record in records)
    assert PRIVATE_DETAIL not in caplog.text
    assert 'synthetic-secret' not in caplog.text
    if counts[1]:
        catalog_health.assert_awaited_once_with()
    else:
        catalog_health.assert_not_awaited()
    if counts[2]:
        probe.assert_awaited_once_with(
            pool, max_skew_seconds=policy.clock_skew_seconds,
            timeout_seconds=min(2, settings.api.backend_timeout_seconds),
        )


def test_readiness_external_cancellation_propagates_without_logging(readiness_clock, caplog):
    composed, jobs, admission, probe, *_ = readiness_clock

    async def scenario():
        entered = asyncio.Event()

        async def wait_for_cancellation():
            entered.set()
            await asyncio.Event().wait()

        jobs.side_effect = wait_for_cancellation
        async with composed() as clock:
            task = asyncio.create_task(clock())
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        asyncio.run(scenario())
    assert jobs.await_count == 1
    admission.assert_not_awaited()
    probe.assert_not_awaited()
    readiness_clock[-2].assert_not_awaited()
    readiness_clock[-1].assert_awaited_once_with()
    assert not [record for record in caplog.records if record.name == LOGGER]
