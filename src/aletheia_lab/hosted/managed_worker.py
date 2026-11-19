"""Resource lifetime for an explicitly started upload worker process."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from collections.abc import Mapping
from contextlib import AsyncExitStack
from functools import partial
from pathlib import Path

from aletheia_lab.hosted_auth.clock_guard import probe_clock
from aletheia_lab.hosted_auth.postgres_sessions import Pool
from aletheia_lab.hosted_auth.settings import AuthPolicy, load_auth_policy
from aletheia_lab.hosted_auth.worker_sessions import WorkerPostgresSources as PostgresSources

from .action_audit import is_ready as audit_is_ready
from .action_audit_migration import verify as verify_action_audit
from .import_failures import ImportFailures
from .object_store import FilesystemObjectStore
from .observability import configure_process_observability, process_telemetry
from .payload_upload_worker import PayloadUploadWorkerSupervisor
from .persistence_migration import verify as verify_catalog
from .postgres_catalog import PostgresCatalog
from .postgres_jobs import PostgresJobs
from .postgres_objects import PostgresObjects
from .postgres_pool import postgres_pool
from .preview_leases import PreviewLeases
from .project_operations import CanonicalProjectOperations
from .project_policy_migration import verify as verify_project_policy
from .runtime_binding import verify_runtime_binding
from .settings import RuntimeSettings, load_runtime_settings
from .upload_job_worker import run_upload_scheduler
from .upload_operations import UploadOperations
from .upload_profile import upload_profile
from .windows_worker import WindowsUploadWorker


def _audit_enabled(environment: Mapping[str, str], settings: RuntimeSettings) -> bool:
    value = environment.get("ALETHEIA_HOSTED_ACTION_AUDIT", "disabled")
    if value not in ("enabled", "disabled") or (value == "enabled" and settings.storage.expected_schema is None):
        raise ValueError("Action audit configuration is unavailable.")
    return value == "enabled"


async def _verify_worker_audit(pool: Pool, settings: RuntimeSettings, enabled: bool) -> None:
    if not enabled:
        return
    schema = settings.storage.expected_schema
    if schema is None:
        raise RuntimeError("Worker dependencies are unavailable.")
    async with pool.connection() as conn:
        if not await verify_action_audit(conn, schema):
            raise RuntimeError("Worker dependencies are unavailable.")


async def _canonical_catalog(pool: Pool, settings: RuntimeSettings) -> PostgresCatalog | None:
    schema = settings.storage.expected_schema
    if schema is None:
        return None
    async with pool.connection() as conn:
        if not (await verify_catalog(conn, schema) and await verify_project_policy(conn, schema)):
            raise RuntimeError("Canonical catalog is unavailable.")
    objects = PostgresObjects(pool, FilesystemObjectStore(Path(settings.storage.object_root) / "catalog"))
    return PostgresCatalog(pool, objects)


def _supervisor_type() -> type[PayloadUploadWorkerSupervisor] | type[WindowsUploadWorker]:
    if sys.platform == "win32":
        if isinstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop):
            raise RuntimeError("Windows runtime requires a selector loop")
        return WindowsUploadWorker
    return PayloadUploadWorkerSupervisor


async def run_worker(environment: Mapping[str, str], stop: asyncio.Event) -> None:
    """Run one worker until stopped, with a process-local database pool."""
    settings = load_runtime_settings(Path(environment["ALETHEIA_HOSTED_CONFIG"]))
    audit_enabled = _audit_enabled(environment, settings)
    policy = load_auth_policy(Path(environment["ALETHEIA_HOSTED_AUTH_POLICY"]))
    policy.require_runtime(settings)
    root = Path(settings.storage.object_root)
    if not root.is_dir():
        raise ValueError("private root must already exist")
    worker_type = _supervisor_type()
    async with AsyncExitStack() as stack:
        pool = await stack.enter_async_context(postgres_pool(settings, environment, role="worker"))
        process_telemetry().bind_pool(pool)
        process_telemetry().watch_loop(asyncio.get_running_loop())
        async with asyncio.timeout(settings.api.startup_timeout_seconds):
            if await verify_runtime_binding(pool, settings.storage, strict=settings.environment == "staging") is not True:
                raise RuntimeError("Worker dependencies are unavailable.")
            await _verify_worker_audit(pool, settings, audit_enabled)
            catalog = await _canonical_catalog(pool, settings)
            sources = PostgresSources(pool, maximum_upload_bytes=200 * 1024 * 1024,
                maximum_expanded_bytes=512 * 1024 * 1024, maximum_entries=100000,
                audit=audit_enabled, catalog=catalog)
            jobs = PostgresJobs(pool, sources, audit=audit_enabled, catalog=catalog, execution=True)
            leases = PreviewLeases(sources)
            leased_imports = await upload_profile(sources, leases)
            canonical_ready = catalog is not None and await jobs.follow_up_ready()

            clock = partial(_worker_clock, settings, policy, pool, sources, leases, leased_imports,
                            audit_enabled, canonical_ready, jobs)
            if not await jobs.is_ready() or not await clock():
                raise RuntimeError("Worker dependencies are unavailable.")
        supervisor = worker_type(python=Path(sys.executable).absolute(),
                                 source_root=Path(__file__).resolve().parents[2],
                                 private_root=root, slots=settings.worker.job_slots_per_process,
                                 timeout_seconds=90)
        stack.push_async_callback(supervisor.aclose)
        operations = (CanonicalProjectOperations(sources, supervisor, jobs,
            leases=leases if leased_imports else None, private_root=root) if canonical_ready else
            UploadOperations(sources, supervisor, leases=leases if leased_imports else None, private_root=root))
        failures = ImportFailures(pool) if leased_imports else None
        task = asyncio.create_task(run_upload_scheduler(
            jobs, operations, stop=stop, slots=settings.worker.job_slots_per_process, lease_seconds=120,
            idle_seconds=0.25, clock_check=clock, failures=failures))
        stopping = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait((task, stopping), return_when=asyncio.FIRST_COMPLETED)
            if not stop.is_set():
                await task
                raise RuntimeError("Worker scheduler stopped unexpectedly.")
            # Allow in-flight work to finish, then cancel for lease recovery.
            await asyncio.wait_for(asyncio.shield(task), settings.worker.shutdown_grace_seconds)
        finally:
            task.cancel()
            stopping.cancel()
            await asyncio.gather(task, stopping, return_exceptions=True)


async def _worker_clock(settings: RuntimeSettings, policy: AuthPolicy, pool: Pool,
                        sources: PostgresSources, leases: PreviewLeases, leased_imports: bool,
                        audit_enabled: bool, canonical_ready: bool, jobs: PostgresJobs) -> bool:
    try:
        async with asyncio.timeout(min(2, settings.api.backend_timeout_seconds)):
            if await upload_profile(sources, leases) != leased_imports:
                return False
            if audit_enabled and not await audit_is_ready(pool):
                return False
            if canonical_ready and not await jobs.follow_up_ready():
                return False
            if not await jobs.execution_ready():
                return False
            return await probe_clock(pool, max_skew_seconds=policy.clock_skew_seconds,
                                     timeout_seconds=min(2, settings.api.backend_timeout_seconds))
    except Exception:
        return False


def main() -> int:
    async def start() -> None:
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        signals = [signal.SIGTERM]
        if hasattr(signal, "SIGBREAK"):
            signals.append(signal.SIGBREAK)
        previous = {sig: signal.getsignal(sig) for sig in signals}
        try:
            for sig in signals:
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
            await run_worker(os.environ, stop)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    observation = None
    try:
        observation = configure_process_observability(role="worker", release_id=os.environ.get("ALETHEIA_RELEASE_ID"))
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                runner.run(start())
        else:
            asyncio.run(start())
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("Worker stopped because a dependency failed.", file=sys.stderr)
        return 1
    finally:
        if observation is not None:
            observation.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
