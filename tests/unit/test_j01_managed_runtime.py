import asyncio
import base64
import types
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from test_k02_runtime_lifecycle import _settings

from aletheia_lab.hosted import managed_runtime
from aletheia_lab.hosted_auth.settings import AuthPolicy


def _forbid_worker(**kwargs):
    raise AssertionError('API must not construct a worker')


class _AdmissionStore:
    def __init__(self, *args, **kwargs):
        pass

    async def prepare(self, policy):
        pass


@pytest.mark.parametrize('failed', [False, True])
def test_api_lifespan_closes_database_without_starting_worker(tmp_path, monkeypatch, failed):
    events = []
    settings = _settings(tmp_path)
    settings = settings.model_copy(update={'storage': settings.storage.model_copy(update={
        'expected_schema': 'j01_test_schema', 'expected_database_role': 'j01_test_api',
    })})
    connection_resource = object()
    @asynccontextmanager
    async def connection():
        yield connection_resource
    pool_resource = types.SimpleNamespace(connection=connection)
    binding = AsyncMock(return_value=True)
    catalog_ready = AsyncMock(return_value=True)
    project_policy_ready = AsyncMock(return_value=True)
    reviews_ready = AsyncMock(return_value=True)
    runtime_operations_ready = AsyncMock(return_value=True)
    runtime_operations = types.SimpleNamespace(is_ready=runtime_operations_ready)
    polling_ready = AsyncMock(return_value=True)
    retention_ready = AsyncMock(return_value=True)
    runtime_parameters = []
    renderer = types.SimpleNamespace(aclose=AsyncMock(side_effect=lambda: events.append('renderer-close')))
    def report_renderer(*, timeout_seconds):
        assert timeout_seconds == min(15, settings.api.backend_timeout_seconds)
        return renderer
    monkeypatch.setattr(managed_runtime, 'load_runtime_settings', lambda path:settings)
    monkeypatch.setattr(managed_runtime, 'verify_runtime_binding', binding)
    monkeypatch.setattr(managed_runtime, 'verify_catalog', catalog_ready)
    monkeypatch.setattr(managed_runtime, 'verify_project_policy', project_policy_ready)
    monkeypatch.setattr(managed_runtime, 'verify_reviews', reviews_ready)
    monkeypatch.setattr(managed_runtime, 'ReportRenderer', report_renderer)
    def runtime_management(current_pool, *, audit, platform_limits):
        assert current_pool is pool_resource and audit is False
        assert platform_limits.maximum_upload_bytes == 10 * 1024 * 1024
        runtime_parameters.append(platform_limits)
        return runtime_operations
    monkeypatch.setattr(managed_runtime, 'RuntimeManagement', runtime_management)
    monkeypatch.setattr(managed_runtime, 'PostgresJobUpdates', lambda jobs, cursors:
                        types.SimpleNamespace(ready=polling_ready))
    monkeypatch.setattr(managed_runtime, 'PostgresRetention', lambda pool, catalog:
                        types.SimpleNamespace(is_ready=retention_ready))
    monkeypatch.setattr(managed_runtime, 'load_auth_policy', lambda path:AuthPolicy(default_return_to='/app', return_to_paths=('/app',), admission_namespace='test-runtime'))
    monkeypatch.setattr(managed_runtime, 'sys', types.SimpleNamespace(platform='linux', executable='C:/python.exe'))
    @asynccontextmanager
    async def pool(runtime_settings, environment, *, role):
        assert runtime_settings is settings
        assert environment == env
        assert role == 'api'
        events.append('pool-open')
        try:
            yield pool_resource
        finally:
            events.append('pool-close')
    class Oidc:
        def __init__(self, *args, **kwargs):
            pass
        async def _current(self):
            events.append('oidc-ready')
            if failed:
                raise RuntimeError('sensitive provider detail')
    async def application(scope, receive, send):
        assert (await receive())['type']=='lifespan.startup'
        await send({'type':'lifespan.startup.complete'})
        assert (await receive())['type']=='lifespan.shutdown'
        await send({'type':'lifespan.shutdown.complete'})
    monkeypatch.setattr(managed_runtime, 'postgres_pool', pool)
    monkeypatch.setattr(managed_runtime, 'UploadWorkerSupervisor', _forbid_worker, raising=False)
    monkeypatch.setattr(managed_runtime, 'RefreshingOidc', Oidc)
    monkeypatch.setattr(managed_runtime, 'PostgresAuthAdmission', _AdmissionStore)
    def jobs(pool, sources, *, audit, catalog, execution, analysis_model):
        assert pool is pool_resource and audit is False and catalog is not None
        assert execution is True and analysis_model == 'upload-snapshot-metric-observation/v2'
        return types.SimpleNamespace(execution=True, execution_ready=AsyncMock(return_value=True),
            is_ready=AsyncMock(return_value=True), follow_up_ready=AsyncMock(return_value=False))
    monkeypatch.setattr(managed_runtime, 'PostgresJobs', jobs)
    def compose(**kwargs):
        assert kwargs['runtime_management'] is runtime_operations
        assert runtime_parameters[0].maximum_workspace_bytes == kwargs['sources'].maximum_workspace_bytes
        assert runtime_parameters[0].maximum_upload_bytes == kwargs['limits'].upload_bundle_bytes
        return application
    monkeypatch.setattr(managed_runtime, 'compose_upload_runtime', compose)
    root = tmp_path / 'objects'
    root.mkdir()
    env = {'ALETHEIA_HOSTED_CONFIG':'unused','ALETHEIA_HOSTED_AUTH_POLICY':'unused',
           'ALETHEIA_HOSTED_DATABASE_URL':'postgresql://u:p@localhost/db',
           'ALETHEIA_HOSTED_LOGIN_PROOF_KEY':base64.b64encode(b'k'*32).decode(),
           'ALETHEIA_HOSTED_CSRF_SECRET':'s'*43,
           'ALETHEIA_HOSTED_ADMISSION_SECRET':'r'*43}
    async def scenario():
        runtime=managed_runtime.ManagedRuntime(env)
        incoming=iter([{'type':'lifespan.startup'},{'type':'lifespan.shutdown'}])
        output=[]
        async def receive(): return next(incoming)
        async def send(message):
            if message['type']=='lifespan.shutdown.complete':
                assert events[-1]=='pool-close'
            output.append(message['type'])
        await runtime({'type':'lifespan'},receive,send)
        expected = ['lifespan.startup.failed'] if failed else ['lifespan.startup.complete','lifespan.shutdown.complete']
        assert output==expected
    asyncio.run(scenario())
    assert events==['pool-open','oidc-ready','renderer-close','pool-close']
    binding.assert_awaited_once_with(pool_resource, settings.storage, strict=True)
    catalog_ready.assert_awaited_once_with(connection_resource, settings.storage.expected_schema)
    project_policy_ready.assert_awaited_once_with(connection_resource, settings.storage.expected_schema)
    reviews_ready.assert_awaited_once_with(connection_resource, settings.storage.expected_schema)
    renderer.aclose.assert_awaited_once_with()
    if failed:
        assert runtime_parameters == []
        runtime_operations_ready.assert_not_awaited()
        polling_ready.assert_not_awaited()
        retention_ready.assert_not_awaited()
    else:
        assert len(runtime_parameters) == 1
        runtime_operations_ready.assert_awaited_once_with()
        polling_ready.assert_awaited_once_with()
        retention_ready.assert_awaited_once_with()
