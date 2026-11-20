"""Separate worker resource lifetime using synthetic dependencies."""

import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_k02_runtime_lifecycle import _settings

from aletheia_lab.hosted import managed_worker
from aletheia_lab.hosted_auth.settings import AuthPolicy


class _Supervisor:
    def __init__(self, case):
        self.case = case
        case.events.append(("worker_open",))

    async def aclose(self):
        self.case.events.append(("worker_close",))


class _Sources:
    def __init__(self, case):
        self.case = case

    async def is_upload_ready(self):
        self.case.readiness_calls.append(("source", self.case.source_ready))
        return self.case.source_ready


class _Leases:
    def __init__(self, case):
        self.case = case

    async def is_ready(self):
        self.case.readiness_calls.append(("lease", self.case.lease_ready))
        return self.case.lease_ready


class _Jobs:
    def __init__(self, case):
        self.case = case

    async def execution_ready(self):
        return self.case.schema_ready

    async def is_ready(self):
        return self.case.schema_ready

    async def follow_up_ready(self):
        self.case.readiness_calls.append(("follow_up", self.case.follow_up_ready))
        return self.case.follow_up_ready


def _wire_repositories(case, monkeypatch):
    def sources(pool, *, audit, catalog, **budgets):
        case.constructor_calls.append(("sources", pool, audit, catalog, budgets))
        case.sources = _Sources(case)
        return case.sources

    def jobs(pool, sources, *, audit, catalog, execution):
        assert execution is True
        assert sources is case.sources
        case.constructor_calls.append(("jobs", pool, audit, catalog))
        case.jobs = _Jobs(case)
        return case.jobs

    def leases(sources):
        assert sources is case.sources
        case.leases = _Leases(case)
        return case.leases

    monkeypatch.setattr(managed_worker, "PostgresSources", sources)
    monkeypatch.setattr(managed_worker, "PostgresJobs", jobs)
    monkeypatch.setattr(managed_worker, "PreviewLeases", leases)


@pytest.fixture
def worker_case(tmp_path, monkeypatch):
    case = SimpleNamespace(events=[], schema_ready=True, clock_ready=True, binding_ready=True,
        binding_calls=[], pool=object(), source_ready=False, lease_ready=False, follow_up_ready=True,
        audit_ready=True, catalog=None, readiness_calls=[], policy_calls=[], constructor_calls=[],
        catalog_calls=[], audit_calls=[], operations=None, scheduler_options=None)
    (tmp_path / "objects").mkdir()
    case.settings = _settings(tmp_path)
    monkeypatch.setattr(managed_worker, "load_runtime_settings", lambda path: case.settings)

    def policy(path):
        case.policy_calls.append(path)
        return AuthPolicy(default_return_to="/app", return_to_paths=("/app",))

    monkeypatch.setattr(managed_worker, "load_auth_policy", policy)
    monkeypatch.setattr(managed_worker, "sys", SimpleNamespace(platform="linux", executable="C:/python.exe"))

    @asynccontextmanager
    async def pool(settings, environment, *, role):
        case.events.append(("pool_open", role))
        try:
            yield case.pool
        finally:
            case.events.append(("pool_close",))

    async def binding(pool, storage, *, strict):
        case.binding_calls.append((pool, storage, strict))
        return case.binding_ready

    async def clock(*args, **kwargs):
        return case.clock_ready

    async def canonical(pool, settings):
        case.catalog_calls.append((pool, settings))
        return case.catalog

    async def verify_audit(pool, settings, enabled):
        case.audit_calls.append((pool, settings, enabled))

    async def audit_ready(pool):
        assert pool is case.pool
        return case.audit_ready

    _wire_repositories(case, monkeypatch)

    async def scheduler(jobs, operations, *, stop, slots, lease_seconds, idle_seconds, clock_check, failures=None):
        case.events.append(("scheduler", slots))
        case.operations, case.scheduler_options = operations, {"failures": failures, "clock": clock_check}
        stop.set()

    monkeypatch.setattr(managed_worker, "postgres_pool", pool)
    monkeypatch.setattr(managed_worker, "PayloadUploadWorkerSupervisor", lambda **kwargs: _Supervisor(case))
    monkeypatch.setattr(managed_worker, "probe_clock", clock)
    monkeypatch.setattr(managed_worker, "verify_runtime_binding", binding)
    monkeypatch.setattr(managed_worker, "_canonical_catalog", canonical)
    monkeypatch.setattr(managed_worker, "_verify_worker_audit", verify_audit)
    monkeypatch.setattr(managed_worker, "audit_is_ready", audit_ready)
    monkeypatch.setattr(managed_worker, "run_upload_scheduler", scheduler)
    case.environment = {"ALETHEIA_HOSTED_CONFIG": "unused", "ALETHEIA_HOSTED_AUTH_POLICY": "unused"}
    return case


@pytest.mark.parametrize("fail", [False, True])
def test_worker_uses_worker_pool_and_closes_resources(worker_case, monkeypatch, fail):
    case = worker_case
    if fail:
        async def fail_scheduler(*args, **kwargs):
            case.events.append(("scheduler", kwargs["slots"]))
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(managed_worker, "run_upload_scheduler", fail_scheduler)
        with pytest.raises(RuntimeError, match="synthetic failure"):
            asyncio.run(managed_worker.run_worker(case.environment, asyncio.Event()))
    else:
        asyncio.run(managed_worker.run_worker(case.environment, asyncio.Event()))
    assert case.events == [("pool_open", "worker"), ("worker_open",), ("scheduler", 1),
                           ("worker_close",), ("pool_close",)]
    assert len(case.binding_calls) == 1
    bound_pool, bound_storage, strict = case.binding_calls[0]
    assert bound_pool is case.pool and bound_storage is case.settings.storage
    assert strict is True


@pytest.mark.parametrize("dependency", ["binding_ready", "schema_ready", "clock_ready"])
def test_unready_worker_does_not_spawn_supervisor(worker_case, dependency):
    setattr(worker_case, dependency, False)
    with pytest.raises(RuntimeError, match="dependencies are unavailable"):
        asyncio.run(managed_worker.run_worker(worker_case.environment, asyncio.Event()))
    assert worker_case.events == [("pool_open", "worker"), ("pool_close",)]


def test_scheduler_return_without_stop_is_failure(worker_case, monkeypatch):
    async def scheduler(*args, **kwargs):
        pass

    monkeypatch.setattr(managed_worker, "run_upload_scheduler", scheduler)
    with pytest.raises(RuntimeError, match="scheduler stopped unexpectedly"):
        asyncio.run(managed_worker.run_worker(worker_case.environment, asyncio.Event()))
    assert worker_case.events[-2:] == [("worker_close",), ("pool_close",)]


def test_stop_deadline_cancels_scheduler_before_cleanup(worker_case, monkeypatch):
    settings = worker_case.settings
    worker_case.settings = settings.model_copy(update={
        "worker": settings.worker.model_copy(update={"shutdown_grace_seconds": 1})})

    async def scheduler(*args, stop, **kwargs):
        stop.set()
        try:
            await asyncio.Event().wait()
        finally:
            worker_case.events.append(("scheduler_cancelled",))

    monkeypatch.setattr(managed_worker, "run_upload_scheduler", scheduler)
    with pytest.raises(TimeoutError):
        asyncio.run(managed_worker.run_worker(worker_case.environment, asyncio.Event()))
    assert worker_case.events[-3:] == [("scheduler_cancelled",), ("worker_close",), ("pool_close",)]


def test_external_cancellation_cleans_up_scheduler_and_resources(worker_case, monkeypatch):
    async def run():
        started = asyncio.Event()

        async def scheduler(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                worker_case.events.append(("scheduler_cancelled",))

        monkeypatch.setattr(managed_worker, "run_upload_scheduler", scheduler)
        task = asyncio.create_task(managed_worker.run_worker(worker_case.environment, asyncio.Event()))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert worker_case.events[-3:] == [("scheduler_cancelled",), ("worker_close",), ("pool_close",)]

    asyncio.run(run())


def test_worker_cli_handles_stop_signal_and_restores_handler(monkeypatch):
    import signal

    before = signal.getsignal(signal.SIGTERM)

    async def worker(environment, stop):
        handler = signal.getsignal(signal.SIGTERM)
        handler(signal.SIGTERM, None)
        await asyncio.wait_for(stop.wait(), 1)

    monkeypatch.setattr(managed_worker, "run_worker", worker)
    assert managed_worker.main() == 0
    assert signal.getsignal(signal.SIGTERM) == before


def test_worker_cli_hides_private_driver_errors(monkeypatch, capsys):
    async def worker(environment, stop):
        raise RuntimeError("private-dsn-secret")

    monkeypatch.setattr(managed_worker, "run_worker", worker)
    assert managed_worker.main() == 1
    assert "private-dsn-secret" not in capsys.readouterr().err


@pytest.mark.parametrize("source_ready,lease_ready", [(False, False), (False, True), (True, False), (True, True)])
def test_upload_profile_requires_matching_upload_and_preview_readiness(worker_case, source_ready, lease_ready):
    case = worker_case
    case.source_ready, case.lease_ready = source_ready, lease_ready
    if source_ready != lease_ready:
        with pytest.raises(RuntimeError, match="migrations must be ready together"):
            asyncio.run(managed_worker.run_worker(case.environment, asyncio.Event()))
        assert case.events == [("pool_open", "worker"), ("pool_close",)]
        assert case.readiness_calls == [("source", source_ready), ("lease", lease_ready)]
        return
    asyncio.run(managed_worker.run_worker(case.environment, asyncio.Event()))
    enabled = source_ready
    assert case.operations._leases is (case.leases if enabled else None)
    assert (case.scheduler_options["failures"] is not None) is enabled
    assert case.operations._sources is case.sources
    assert case.operations._private_root == Path(case.settings.storage.object_root)


@pytest.mark.parametrize("source_ready,lease_ready", [(False, True), (True, False), (True, True)])
def test_legacy_worker_stops_admission_when_upload_profile_changes(worker_case, monkeypatch, source_ready, lease_ready):
    case = worker_case

    async def scheduler(*args, stop, clock_check, **kwargs):
        assert await clock_check() is True
        case.readiness_calls.clear()
        case.source_ready, case.lease_ready = source_ready, lease_ready
        assert await clock_check() is False
        assert case.readiness_calls == [("source", source_ready), ("lease", lease_ready)]
        stop.set()

    monkeypatch.setattr(managed_worker, "run_upload_scheduler", scheduler)
    asyncio.run(managed_worker.run_worker(case.environment, asyncio.Event()))
    assert case.events[-2:] == [("worker_close",), ("pool_close",)]


def _enable_audit(case):
    case.environment["ALETHEIA_HOSTED_ACTION_AUDIT"] = "enabled"
    case.settings = case.settings.model_copy(update={"storage": case.settings.storage.model_copy(update={
        "expected_schema": "worker_synthetic", "expected_database_role": "worker_synthetic_role"})})


@pytest.mark.parametrize("dependency", ["source_ready", "lease_ready", "follow_up_ready", "audit_ready"])
def test_enabled_dependencies_are_rechecked_during_scheduler(worker_case, monkeypatch, dependency):
    case = worker_case
    case.source_ready = case.lease_ready = True
    case.catalog = object()
    _enable_audit(case)

    async def scheduler(*args, stop, clock_check, **kwargs):
        assert await clock_check() is True
        setattr(case, dependency, False)
        assert await clock_check() is False
        stop.set()

    monkeypatch.setattr(managed_worker, "run_upload_scheduler", scheduler)
    asyncio.run(managed_worker.run_worker(case.environment, asyncio.Event()))
    assert case.events[-2:] == [("worker_close",), ("pool_close",)]


@pytest.mark.parametrize("value", ["private-invalid-mode", "enabled"])
def test_invalid_audit_config_rejects_before_auth_policy_or_pool(worker_case, value):
    case = worker_case
    case.environment["ALETHEIA_HOSTED_ACTION_AUDIT"] = value
    with pytest.raises(ValueError, match="Action audit configuration is unavailable"):
        asyncio.run(managed_worker.run_worker(case.environment, asyncio.Event()))
    assert case.policy_calls == [] and case.events == []


@pytest.mark.parametrize("follow_up_ready", [False, True])
def test_catalog_and_audit_forward_to_one_canonical_worker_bridge(worker_case, follow_up_ready):
    case = worker_case
    case.catalog, case.follow_up_ready = object(), follow_up_ready
    case.source_ready = case.lease_ready = True
    _enable_audit(case)
    asyncio.run(managed_worker.run_worker(case.environment, asyncio.Event()))
    source_call, job_call = case.constructor_calls
    assert source_call[:4] == ("sources", case.pool, True, case.catalog)
    assert source_call[3] is job_call[3] is case.catalog
    assert job_call[:3] == ("jobs", case.pool, True)
    assert source_call[4] == {"maximum_upload_bytes": 200 * 1024 * 1024,
                              "maximum_expanded_bytes": 512 * 1024 * 1024, "maximum_entries": 100000}
    assert case.catalog_calls == [(case.pool, case.settings)]
    assert case.audit_calls == [(case.pool, case.settings, True)]
    assert isinstance(case.operations, managed_worker.CanonicalProjectOperations) is follow_up_ready
    if follow_up_ready:
        assert case.operations._project_jobs is case.jobs
    assert case.operations._leases is case.leases


def test_enabled_audit_outage_prevents_supervisor_creation(worker_case):
    case = worker_case
    _enable_audit(case)
    case.audit_ready = False
    with pytest.raises(RuntimeError, match="dependencies are unavailable"):
        asyncio.run(managed_worker.run_worker(case.environment, asyncio.Event()))
    assert case.events == [("pool_open", "worker"), ("pool_close",)]


def test_module_entrypoint_has_worker_clock_before_starting_runtime(tmp_path):
    command = """
import runpy
import sys

def before_main(frame, event, argument):
    if (event == "call" and frame.f_code.co_name == "main"
            and frame.f_globals.get("__name__") == "__main__"):
        assert callable(frame.f_globals.get("_worker_clock")), "Worker readiness must be defined at CLI entry."
        raise SystemExit(0)

sys.setprofile(before_main)
runpy.run_module("aletheia_lab.hosted.managed_worker", run_name="__main__")
raise AssertionError("Worker module did not invoke its normal entrypoint.")
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", command],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(Path(managed_worker.__file__).resolve().parents[2])),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
