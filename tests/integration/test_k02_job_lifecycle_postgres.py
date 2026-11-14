"""P20 actual persisted metadata, capped claims and canonical publication deadlines."""
from __future__ import annotations

import os

import pytest
from pydantic import SecretStr
from test_k05_migrations_postgres import _run
from test_k05_native_results_postgres import _case

from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K02_TEST_DATABASE_URL"),
    reason="Explicit private K02 PostgreSQL database URL required")]


async def _enqueue(jobs, case, operation="preview", key="synthetic-lifecycle-key-01"):
    return await jobs.enqueue(credential_digest=case.a, workspace_id="a", operation=operation,
        request_key=SecretStr(key), request={"source_id": case.receipt.source_id,
                                            **({"mapping": {}} if operation == "confirm" else {})})


async def _past_deadline(pool, job_id):
    async with pool.connection() as conn:
        await conn.execute("UPDATE j01_jobs SET created_at=clock_timestamp()-interval '1 hour', "
            "deadline_at=clock_timestamp()-interval '1 second',lease_until=clock_timestamp()+interval '1 minute' "
            "WHERE job_id=%s", (job_id,))


def test_actual_claim_budget_counts_same_digest_reclaims_and_terminates_exhaustion(tmp_path, monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K02_TEST_DATABASE_URL"])

    async def scenario():
        async with _case(tmp_path) as case:
            jobs = PostgresJobs(case.pool, case.sources)
            receipt = await _enqueue(jobs, case)
            queued = await jobs.status(credential_digest=case.a, workspace_id="a", job_id=receipt.job_id)
            assert queued.operation == "preview" and queued.created_at and queued.deadline_at
            assert queued.attempt is None and queued.max_attempts == 3 and queued.terminal_at is None
            lease = SecretStr("a" * 64)
            for attempt in range(1, 4):
                claim = await jobs.claim(lease_digest=lease, lease_seconds=120)
                assert claim is not None and claim.job_id == receipt.job_id
                status = await jobs.status(credential_digest=case.a, workspace_id="a", job_id=receipt.job_id)
                assert status.status == "running" and status.attempt == attempt and status.terminal_at is None
                async with case.pool.connection() as conn:
                    await conn.execute("UPDATE j01_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                                       (receipt.job_id,))
            assert await jobs.claim(lease_digest=lease, lease_seconds=120) is None
            failed = await jobs.status(credential_digest=case.a, workspace_id="a", job_id=receipt.job_id)
            assert failed.status == "failed" and failed.attempt == failed.max_attempts == 3
            assert failed.terminal_at is not None and failed.retryable is True and failed.output_url is None
            retry = await jobs.retry(credential_digest=case.a, workspace_id="a", job_id=receipt.job_id,
                                     request_key=SecretStr("synthetic-lifecycle-manual-retry"))
            assert retry.job_id != receipt.job_id
            restarted = await jobs.status(credential_digest=case.a, workspace_id="a", job_id=retry.job_id)
            assert restarted.status == "queued" and restarted.attempt is None and restarted.max_attempts == 3
    _run(scenario())


def test_actual_cancel_metadata_and_absolute_deadline_cap_are_not_view_only(tmp_path, monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K02_TEST_DATABASE_URL"])

    async def scenario():
        async with _case(tmp_path) as case:
            jobs = PostgresJobs(case.pool, case.sources)
            receipt = await _enqueue(jobs, case)
            cancelled = await jobs.cancel(credential_digest=case.a, workspace_id="a", job_id=receipt.job_id)
            assert cancelled.status == "cancelled" and cancelled.operation == "preview"
            assert cancelled.created_at and cancelled.deadline_at and cancelled.terminal_at and cancelled.retryable is True
            assert cancelled.attempt is None and cancelled.max_attempts == 3
            next_job = await _enqueue(jobs, case, key="synthetic-lifecycle-cap-key-02")
            async with case.pool.connection() as conn:
                await conn.execute("UPDATE j01_jobs SET deadline_at=clock_timestamp()+interval '30 seconds' WHERE job_id=%s",
                                   (next_job.job_id,))
            lease = SecretStr("b" * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=120)
            assert claim and claim.job_id == next_job.job_id
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT lease_until=deadline_at FROM j01_jobs WHERE job_id=%s",
                                                (next_job.job_id,))).fetchone() == (True,)
            await _past_deadline(case.pool, next_job.job_id)
            assert await jobs.complete(claim=claim, lease_digest=lease, result={"source_sha256": case.receipt.sha256}) is False
            failed = await jobs.status(credential_digest=case.a, workspace_id="a", job_id=next_job.job_id)
            assert failed.status == "failed" and failed.terminal_at is not None
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT state,result,lease_digest,lease_until FROM j01_jobs WHERE job_id=%s",
                                                (next_job.job_id,))).fetchone() == ("failed", None, None, None)
            assert await jobs.claim(lease_digest=lease, lease_seconds=120) is None
    _run(scenario())


@pytest.mark.parametrize("operation", ["confirm", "analyze"])
def test_actual_deadline_blocks_canonical_snapshot_or_result_publication_even_with_fresh_lease(tmp_path, monkeypatch, operation):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K02_TEST_DATABASE_URL"])

    async def scenario():
        async with _case(tmp_path) as case:
            sources = PostgresSources(case.pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
                                      maximum_entries=10, catalog=case.catalog)
            jobs = PostgresJobs(case.pool, sources, catalog=case.catalog)
            receipt = await _enqueue(jobs, case, operation="confirm")
            lease = SecretStr("c" * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=120)
            assert claim and claim.job_id == receipt.job_id
            if operation == "analyze":
                assert await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot)
                receipt = await jobs.enqueue_analysis(credential_digest=case.a, workspace_id="a",
                    project_id=case.snapshot.mapping.project_id, snapshot_id=case.snapshot.snapshot_id,
                    question=case.view.conversation.turns[0].question, request_key=SecretStr("synthetic-deadline-analysis"))
                claim = await jobs.claim(lease_digest=lease, lease_seconds=120)
                assert claim and claim.job_id == receipt.job_id
            await _past_deadline(case.pool, receipt.job_id)
            completed = (await jobs.complete_result(claim=claim, lease_digest=lease, record=case.initial)
                         if operation == "analyze" else
                         await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot))
            assert completed is False
            status = await jobs.status(credential_digest=case.a, workspace_id="a", job_id=receipt.job_id)
            assert status.status == "failed" and status.terminal_at is not None
            async with case.pool.connection() as conn:
                kinds = await (await conn.execute("SELECT record_type,count(*) FROM k05_records GROUP BY record_type")).fetchall()
                assert kinds == ([("upload_snapshot", 1)] if operation == "analyze" else [])
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_result'")).fetchone() == (0,)
    _run(scenario())
