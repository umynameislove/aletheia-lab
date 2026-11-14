"""Run one trusted upload job; scheduling and failure policy are separate."""
from __future__ import annotations

import asyncio
import math
import secrets
from collections.abc import Awaitable, Callable

from pydantic import SecretStr

from aletheia_lab.project.identity import content_hasher

from .import_errors import MappingInvalid
from .import_failures import ImportFailures
from .observability import timed_job
from .postgres_jobs import JobClaim, Operation, PostgresJobs
from .project_operations import CanonicalProjectOperations
from .upload_operations import UploadOperations
from .worker_supervisor import WorkerRejected
from .workspace_management import WorkspaceConflict


async def run_one_upload_job(jobs: PostgresJobs, operations: UploadOperations, *, lease_seconds: int,
                             clock_check: Callable[[], Awaitable[bool]] | None = None, failures: ImportFailures | None = None) -> bool:
    if clock_check is not None and await clock_check() is not True:
        raise TimeoutError("clock unavailable")
    lease = SecretStr(content_hasher(secrets.token_bytes(32)).hexdigest())
    supported: tuple[Operation, ...] = ("preview", "confirm", "analyze", "follow_up", "refresh") if isinstance(
        operations, CanonicalProjectOperations) else ("preview", "confirm", "analyze")
    claim = await jobs.claim(lease_digest=lease, lease_seconds=lease_seconds, operations=supported)
    if claim is None:
        return False
    try:
        if getattr(jobs, "execution", False):
            return await _guard_claim(jobs, operations, claim, lease, lease_seconds, clock_check)
        with timed_job(claim.job_id, claim.operation) as observation:
            observation.settled = await _run_claim(jobs, operations, claim, lease, clock_check)
            return observation.settled
    except WorkspaceConflict:
        return await _conflict_failure(jobs, claim, lease)
    except MappingInvalid as error:
        return await _mapping_failure(jobs, claim, lease, failures, error)
    except TimeoutError:
        if getattr(jobs, "execution", False):
            return await jobs.fail(claim=claim, lease_digest=lease, retryable=True)
        raise
    except WorkerRejected as error:
        return await _worker_failure(jobs, claim, lease, error)
    except Exception:
        # Cancellation propagates; expired leases remain available for recovery.
        return await jobs.fail(claim=claim, lease_digest=lease)


async def _stop_compute(task: asyncio.Task[bool]) -> None:
    """Do not acknowledge cancellation until the supervisor has reaped its child."""
    if not task.done():
        task.cancel()
    cleanup = asyncio.gather(task, return_exceptions=True)
    interrupted = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            interrupted = True
    if interrupted:
        raise asyncio.CancelledError


async def _guard_claim(jobs: PostgresJobs, operations: UploadOperations, claim: JobClaim,
                       lease: SecretStr, lease_seconds: int,
                       clock_check: Callable[[], Awaitable[bool]] | None) -> bool:
    with timed_job(claim.job_id, claim.operation) as observation:
        observation.settled = await _guard_claim_unobserved(jobs, operations, claim, lease, lease_seconds, clock_check)
        return observation.settled


async def _guard_claim_unobserved(jobs: PostgresJobs, operations: UploadOperations, claim: JobClaim,
                       lease: SecretStr, lease_seconds: int,
                       clock_check: Callable[[], Awaitable[bool]] | None) -> bool:
    task = asyncio.create_task(_run_claim(jobs, operations, claim, lease, clock_check))

    async def settled() -> bool:
        published = await task
        # Publication may lose to a cancellation after computation already ended.
        return published or await jobs.acknowledge_cancel(claim=claim, lease_digest=lease)

    try:
        while True:
            done, _ = await asyncio.wait((task,), timeout=min(5.0, lease_seconds / 3))
            if done:
                return await settled()
            try:
                if clock_check is not None and await clock_check() is not True:
                    raise TimeoutError("clock unavailable")
                live = await jobs.heartbeat(claim=claim, lease_digest=lease, lease_seconds=lease_seconds)
            except Exception:
                if task.done():
                    return await settled()
                raise
            if task.done():
                return await settled()
            if not live:
                await _stop_compute(task)
                if await jobs.acknowledge_cancel(claim=claim, lease_digest=lease):
                    return True
                # Revocation, deadline, or a newer lease fences any publication.
                return await jobs.fail(claim=claim, lease_digest=lease, failure_kind="authorization")
    finally:
        await _finish_compute(task)


async def _run_claim(jobs: PostgresJobs, operations: UploadOperations, claim: object, lease: SecretStr,
                     clock_check: Callable[[], Awaitable[bool]] | None = None) -> bool:
    from .postgres_jobs import JobClaim

    if not isinstance(claim, JobClaim):
        raise ValueError("invalid job claim")
    if claim.operation == "refresh":
        if not isinstance(operations, CanonicalProjectOperations):
            raise ValueError("canonical refresh is unavailable")
        refreshed = await operations.prepare_refresh(claim)
        if clock_check is not None and await clock_check() is not True:
            raise TimeoutError("clock unavailable")
        return await jobs.complete_refresh(claim=claim, lease_digest=lease, snapshot=refreshed.snapshot)
    if claim.operation == "follow_up":
        if not isinstance(operations, CanonicalProjectOperations):
            raise ValueError("canonical follow-up is unavailable")
        record = await operations.prepare_follow_up(claim)
        if clock_check is not None and await clock_check() is not True:
            raise TimeoutError("clock unavailable")
        return await jobs.complete_result(claim=claim, lease_digest=lease, record=record)
    if claim.operation == "analyze" and isinstance(operations, CanonicalProjectOperations):
        record = await operations.prepare_initial_analysis(claim)
        if clock_check is not None and await clock_check() is not True:
            raise TimeoutError("clock unavailable")
        return await jobs.complete_result(claim=claim, lease_digest=lease, record=record)
    return await _run_upload_claim(jobs, operations, claim, lease, clock_check)


async def _run_upload_claim(jobs: PostgresJobs, operations: UploadOperations, claim: object, lease: SecretStr,
                            clock_check: Callable[[], Awaitable[bool]] | None) -> bool:
    from .postgres_jobs import JobClaim

    if not isinstance(claim, JobClaim):
        raise ValueError("invalid job claim")
    if claim.operation == "analyze":
        snapshot_id, question = claim.request.get("snapshot_id"), claim.request.get("question")
        if (set(claim.request) not in ({"snapshot_id", "question"}, {"snapshot_id", "question", "analysis_model"})
                or claim.request.get("analysis_model", "upload-snapshot-observation/v1") not in
                    ("upload-snapshot-observation/v1", "upload-snapshot-metric-observation/v2")
                or not isinstance(snapshot_id, str) or not isinstance(question, str)):
            raise ValueError("invalid analysis job")
        from typing import cast

        from .postgres_jobs import AnalysisModel

        options = {} if "analysis_model" not in claim.request else {
            "analysis_model": cast(AnalysisModel, claim.request["analysis_model"])}
        result = await operations.analyze(credential_digest=claim.credential_digest,
            workspace_id=claim.workspace_id, snapshot_id=snapshot_id, question=question, **options)
        if clock_check is not None and await clock_check() is not True:
            raise TimeoutError("clock unavailable")
        return await jobs.complete(claim=claim, lease_digest=lease, result=result.model_dump(mode="json"))
    source_id = claim.request.get("source_id")
    if not isinstance(source_id, str):
        raise ValueError("invalid source job")
    if claim.operation == "preview":
        if set(claim.request) != {"source_id"}:
            raise ValueError("invalid preview job")
        preview = await operations.preview(credential_digest=claim.credential_digest,
                                          workspace_id=claim.workspace_id, source_id=source_id)
        if clock_check is not None and await clock_check() is not True:
            raise TimeoutError("clock unavailable")
        return await jobs.complete(claim=claim, lease_digest=lease, result=preview.model_dump(mode="json"))
    if claim.operation == "confirm":
        return await _confirm_claim(jobs, operations, claim, lease, source_id, clock_check)
    raise ValueError("unsupported job operation")


async def _confirm_claim(jobs: PostgresJobs, operations: UploadOperations, claim: object, lease: SecretStr,
                         source_id: str, clock_check: Callable[[], Awaitable[bool]] | None) -> bool:
    from .postgres_jobs import JobClaim

    if not isinstance(claim, JobClaim):
        raise ValueError("invalid job claim")
    mapping = claim.request.get("mapping")
    if set(claim.request) != {"source_id", "mapping"} or not isinstance(mapping, dict):
        raise ValueError("invalid confirm job")
    mapping = dict(mapping)
    preview_id = mapping.pop('_preview_id', None)
    if preview_id is not None and not isinstance(preview_id, str):
        raise ValueError('Invalid preview binding.')
    options: dict[str, str] = {} if preview_id is None else {'preview_id': preview_id}
    snapshot = await operations.prepare_confirm(credential_digest=claim.credential_digest,
        workspace_id=claim.workspace_id, source_id=source_id, mapping=mapping, **options)
    if clock_check is not None and await clock_check() is not True:
        raise TimeoutError("clock unavailable")
    return await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=snapshot)


def _schedule_limits(slots: int, lease_seconds: int, idle_seconds: float) -> None:
    if type(slots) is not int or not 1 <= slots <= 32:
        raise ValueError("invalid worker slots")
    if type(lease_seconds) is not int or not 10 <= lease_seconds <= 300:
        raise ValueError("invalid lease")
    if isinstance(idle_seconds, bool) or not math.isfinite(idle_seconds) or not 0.05 <= idle_seconds <= 60:
        raise ValueError("invalid polling interval")


async def run_upload_scheduler(jobs: PostgresJobs, operations: UploadOperations, *, stop: asyncio.Event,
                               slots: int, lease_seconds: int, idle_seconds: float,
                               clock_check: Callable[[], Awaitable[bool]] | None = None, failures: ImportFailures | None = None) -> None:
    """Run bounded pollers until stopped; leave interrupted jobs for lease recovery."""
    _schedule_limits(slots, lease_seconds, idle_seconds)

    async def poll() -> None:
        while not stop.is_set():
            try:
                if getattr(jobs, "execution", False):
                    # Each slot sweeps a bounded batch; all timestamps remain database-owned.
                    await jobs.reconcile(limit=64)
                    worked = await run_one_upload_job(jobs, operations, lease_seconds=lease_seconds,
                                                      clock_check=clock_check, failures=failures)
                else:
                    # Explicit legacy profile has no renewable heartbeat.
                    async with asyncio.timeout(lease_seconds - 2):
                        if clock_check is None and failures is None:
                            worked = await run_one_upload_job(jobs, operations, lease_seconds=lease_seconds)
                        else:
                            worked = await run_one_upload_job(jobs, operations, lease_seconds=lease_seconds,
                                                              clock_check=clock_check, failures=failures)
            except TimeoutError:
                worked = False
            if not worked and not stop.is_set():
                try:
                    async with asyncio.timeout(idle_seconds):
                        await stop.wait()
                except TimeoutError:
                    pass

    async with asyncio.TaskGroup() as group:
        for _ in range(slots):
            group.create_task(poll())
async def _mapping_failure(jobs: PostgresJobs, claim: JobClaim, lease: SecretStr,
                           failures: ImportFailures | None, error: MappingInvalid) -> bool:
    if failures is not None:
        try:
            await failures.record(claim=claim, lease_digest=lease, failure=error)
        finally:
            await jobs.fail(claim=claim, lease_digest=lease)
        return True
    return await jobs.fail(claim=claim, lease_digest=lease)

async def _worker_failure(jobs: PostgresJobs, claim: JobClaim, lease: SecretStr,
                          error: WorkerRejected) -> bool:
    if getattr(jobs, "execution", False):
        return await jobs.fail(claim=claim, lease_digest=lease,
                               retryable=str(error) in ("worker_busy", "worker_timeout"))
    return await jobs.fail(claim=claim, lease_digest=lease)

async def _finish_compute(task: asyncio.Task[bool]) -> None:
    if not task.done():
        await _stop_compute(task)
    elif not task.cancelled():
        task.exception()

async def _conflict_failure(jobs: PostgresJobs, claim: JobClaim, lease: SecretStr) -> bool:
    if getattr(jobs, "execution", False):
        return await jobs.fail(claim=claim, lease_digest=lease, failure_kind="conflict")
    return await jobs.fail(claim=claim, lease_digest=lease)

