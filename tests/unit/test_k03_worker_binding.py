"""Worker startup delegates database binding checks to the canonical K-04 guard."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from test_k02_runtime_lifecycle import _settings

from aletheia_lab.hosted import managed_worker, runtime_binding
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_auth.worker_sessions import WorkerPostgresSources


def _wire_current_profile(case, monkeypatch):
    class Jobs:
        def __init__(self, pool, sources, *, audit, catalog, execution):
            assert execution is True
            assert case.verified and pool is case.pool and sources is case.sources
            assert audit is False and catalog is case.catalog
            case.events.append("jobs_constructed")

        async def follow_up_ready(self):
            assert case.verified
            case.events.append("follow_up")
            return False

        async def execution_ready(self):
            return True

        async def is_ready(self):
            assert case.verified
            case.events.append("jobs_schema")
            return True

    async def catalog(pool, settings):
        assert case.verified and pool is case.pool and settings is case.settings
        case.events.append("catalog_schema")
        return case.catalog

    async def profile(sources, leases):
        assert case.verified and sources is case.sources
        assert leases._sources is case.sources and leases._pool is case.pool
        case.events.append("upload_profile")
        # This binding fixture uses the supported non-resumable/follow-up profile.
        return False

    monkeypatch.setattr(managed_worker, "PostgresJobs", Jobs)
    monkeypatch.setattr(managed_worker, "_canonical_catalog", catalog)
    monkeypatch.setattr(managed_worker, "upload_profile", profile)


def _case(tmp_path, monkeypatch, *, environment="staging", binding=True, startup_seconds=1.0):
    (tmp_path / "objects").mkdir()
    settings = _settings(tmp_path, startup_timeout_seconds=startup_seconds)
    values = settings.model_dump()
    values["environment"] = environment
    values["storage"].update(expected_schema="k03_worker_fixture", expected_database_role="k03_worker_role")
    pool_value = object()
    case = SimpleNamespace(settings=RuntimeSettings.model_validate(values), events=[], pool=pool_value,
                           binding=binding, verified=False, sources=SimpleNamespace(_pool=pool_value),
                           catalog=object(), supervisor=SimpleNamespace(),
                           environment={"ALETHEIA_HOSTED_CONFIG": "unused", "ALETHEIA_HOSTED_AUTH_POLICY": "unused"})
    monkeypatch.setattr(managed_worker, "load_runtime_settings", lambda path: case.settings)
    monkeypatch.setattr(managed_worker, "load_auth_policy", lambda path: AuthPolicy(
        default_return_to="/app", return_to_paths=("/app",)))
    monkeypatch.setattr(managed_worker, "sys", SimpleNamespace(platform="linux", executable="C:/python.exe"))

    @asynccontextmanager
    async def pool(settings, environment, *, role):
        assert settings is case.settings and environment is case.environment and role == "worker"
        case.events.append("pool_open")
        try:
            yield case.pool
        finally:
            case.events.append("pool_close")

    def sources(pool, *, audit, catalog, **budgets):
        assert case.verified and pool is case.pool and audit is False and catalog is case.catalog
        assert budgets == {"maximum_upload_bytes": 200 * 1024 * 1024,
                           "maximum_expanded_bytes": 512 * 1024 * 1024, "maximum_entries": 100000}
        case.events.append("sources_constructed")
        return case.sources

    async def verify(pool, storage, *, strict=False):
        assert pool is case.pool and storage is case.settings.storage
        assert strict is (case.settings.environment == "staging")
        case.events.append("binding")
        case.verified = case.binding is True
        return case.binding

    async def clock(pool, **kwargs):
        assert case.verified and pool is case.pool
        case.events.append("clock")
        return True

    def supervisor(**kwargs):
        case.events.append("supervisor_open")

        async def close():
            case.events.append("supervisor_close")

        case.supervisor.aclose = close
        return case.supervisor

    async def scheduler(jobs, operations, *, stop, **kwargs):
        assert case.verified
        case.events.append("scheduler")
        stop.set()

    monkeypatch.setattr(managed_worker, "postgres_pool", pool)
    monkeypatch.setattr(managed_worker, "PostgresSources", sources)
    monkeypatch.setattr(managed_worker, "verify_runtime_binding", verify)
    monkeypatch.setattr(managed_worker, "probe_clock", clock)
    monkeypatch.setattr(managed_worker, "PayloadUploadWorkerSupervisor", supervisor)
    monkeypatch.setattr(managed_worker, "run_upload_scheduler", scheduler)
    _wire_current_profile(case, monkeypatch)
    return case


@pytest.mark.parametrize("environment", ["local", "staging"])
def test_worker_binding_precedes_catalog_and_uses_current_repository_arguments(tmp_path, monkeypatch, environment):
    case = _case(tmp_path, monkeypatch, environment=environment)
    asyncio.run(managed_worker.run_worker(case.environment, asyncio.Event()))
    assert case.events == ["pool_open", "binding", "catalog_schema", "sources_constructed", "jobs_constructed",
                           "upload_profile", "follow_up", "jobs_schema", "upload_profile", "clock",
                           "supervisor_open", "scheduler", "supervisor_close", "pool_close"]


@pytest.mark.parametrize("binding", [False, None, 1])
def test_unverified_binding_never_reads_schema_or_starts_worker(tmp_path, monkeypatch, binding):
    case = _case(tmp_path, monkeypatch, binding=binding)
    with pytest.raises(RuntimeError, match="Worker dependencies are unavailable."):
        asyncio.run(managed_worker.run_worker(case.environment, asyncio.Event()))
    assert case.events == ["pool_open", "binding", "pool_close"]


def _blocked_checker(case, entered):
    async def verify(pool, storage, *, strict=False):
        assert pool is case.pool and storage is case.settings.storage and strict is True
        case.events.append("binding")
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            case.events.append("binding_cancelled")
    return verify


def test_binding_check_is_inside_existing_startup_deadline_and_pool_closes(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, startup_seconds=0.02)

    async def scenario():
        monkeypatch.setattr(managed_worker, "verify_runtime_binding", _blocked_checker(case, asyncio.Event()))
        async with asyncio.timeout(1):
            with pytest.raises(TimeoutError):
                await managed_worker.run_worker(case.environment, asyncio.Event())

    asyncio.run(scenario())
    assert case.events == ["pool_open", "binding", "binding_cancelled", "pool_close"]


def test_external_cancellation_during_binding_propagates_and_closes_pool(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch)

    async def scenario():
        entered = asyncio.Event()
        monkeypatch.setattr(managed_worker, "verify_runtime_binding", _blocked_checker(case, entered))
        task = asyncio.create_task(managed_worker.run_worker(case.environment, asyncio.Event()))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert case.events == ["pool_open", "binding", "binding_cancelled", "pool_close"]


def test_worker_imports_canonical_checker_and_retains_rotated_job_adapter():
    assert managed_worker.verify_runtime_binding is runtime_binding.verify_runtime_binding
    assert managed_worker.PostgresSources is WorkerPostgresSources
