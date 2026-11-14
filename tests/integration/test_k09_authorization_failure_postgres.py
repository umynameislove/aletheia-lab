"""Real revocation after a persisted claim, before preparation or publication."""
from __future__ import annotations

import asyncio
import os

import pytest
from pydantic import SecretStr
from test_k05_native_results_postgres import _case, _run
from test_k06_canonical_snapshot_postgres import _setup

from aletheia_lab.hosted.authorization import AuthorizationDenied, PostgresAuthorization
from aletheia_lab.hosted.postgres_jobs import JobStateConflict, PostgresJobs
from aletheia_lab.hosted.project_operations import CanonicalProjectOperations
from aletheia_lab.hosted.upload_job_worker import run_one_upload_job

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K09_TEST_DATABASE_URL"), reason="Explicit private K09 PostgreSQL URL required",
)]


@pytest.fixture(autouse=True)
def configured_database(monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K09_TEST_DATABASE_URL"])


@pytest.mark.parametrize("stage", ["prepare", "publication"])
@pytest.mark.parametrize("revocation", ["membership", "session"])
def test_revocation_after_worker_claim_records_authorization_without_publication(
    tmp_path, record_property, stage, revocation,
):
    async def scenario():
        async with _case(tmp_path) as case:
            entered, release = asyncio.Event(), asyncio.Event()

            class ControlledJobs(PostgresJobs):
                async def complete_snapshot(self, **kwargs):
                    if stage == "publication":
                        entered.set()
                        await release.wait()
                    return await super().complete_snapshot(**kwargs)

            jobs = await _setup(case, lambda *args, **kwargs: ControlledJobs(
                *args, **kwargs, execution=True,
            ))

            class PreparedOperations(CanonicalProjectOperations):
                async def prepare_confirm(self, **kwargs):
                    if stage == "prepare":
                        entered.set()
                        await release.wait()
                    async with case.pool.connection() as conn:
                        await PostgresAuthorization().authorize(
                            conn, case.a.get_secret_value(), "a", "confirm",
                        )
                    return case.snapshot

            receipt = await jobs.enqueue(credential_digest=case.a, workspace_id="a", operation="confirm",
                request_key=SecretStr("k09-auth-after-claim"), request={
                    "source_id": case.receipt.source_id,
                    "mapping": case.snapshot.mapping.model_dump(mode="json"),
                })
            operations = PreparedOperations(case.sources, None, jobs)
            task = asyncio.create_task(run_one_upload_job(jobs, operations, lease_seconds=30))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                before = await jobs.status(credential_digest=case.a, workspace_id="a", job_id=receipt.job_id)
                history = await jobs.attempts(credential_digest=case.b, workspace_id="a", job_id=receipt.job_id)
                attempt, = history.items
                assert before.status == "running" and before.attempt == 1
                assert attempt.outcome == "running" and attempt.started_at and attempt.heartbeat_at
                record_property("job_id", receipt.job_id)
                record_property("claimed_status", before.status)
                record_property("started_at", attempt.started_at.isoformat())
                record_property("heartbeat_at", attempt.heartbeat_at.isoformat())
                async with case.pool.connection() as conn:
                    if revocation == "membership":
                        await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 "
                                           "WHERE workspace_id='a' AND user_id='owner_a'")
                    else:
                        await conn.execute("UPDATE j01_sessions SET state='revoked' WHERE context_id='ctx_a'")
                    revoked_at = (await (await conn.execute("SELECT clock_timestamp()")).fetchone())[0]
                record_property("revocation_db_at", revoked_at.isoformat())
                release.set()
                assert await asyncio.wait_for(task, 5)
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

            status = await jobs.status(credential_digest=case.b, workspace_id="a", job_id=receipt.job_id)
            history = await jobs.attempts(credential_digest=case.b, workspace_id="a", job_id=receipt.job_id)
            attempt, = history.items
            assert status.status == "failed" and status.retryable is False and status.output_url is None
            assert attempt.epoch == 1 and attempt.outcome == "failed"
            assert attempt.failure_kind == "authorization" and attempt.finished_at
            assert attempt.started_at and attempt.heartbeat_at
            assert history.model_dump(mode="json")["items"][0]["failure_kind"] == "authorization"
            with pytest.raises(AuthorizationDenied):
                await jobs.status(credential_digest=case.a, workspace_id="a", job_id=receipt.job_id)
            with pytest.raises(AuthorizationDenied):
                await jobs.attempts(credential_digest=case.a, workspace_id="a", job_id=receipt.job_id)
            assert await jobs.claim(lease_digest=SecretStr("b" * 64), lease_seconds=30) is None
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT state,failure_kind,result,attempt_count,lease_digest,lease_until "
                    "FROM j01_jobs WHERE job_id=%s", (receipt.job_id,))).fetchone() == (
                        "failed", "authorization", None, 1, None, None,
                    )
                assert await (await conn.execute("SELECT count(*) FROM k05_records")).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit "
                    "WHERE action='job.publish'")).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit "
                    "WHERE action='job.fail'")).fetchone() == (1,)
                if revocation == "membership":
                    await conn.execute("UPDATE j01_memberships SET active=true,revision=revision+1 "
                                       "WHERE workspace_id='a' AND user_id='owner_a'")
                else:
                    await conn.execute("UPDATE j01_sessions SET state='active' WHERE context_id='ctx_a'")
            with pytest.raises(JobStateConflict):
                await jobs.retry(credential_digest=case.a, workspace_id="a", job_id=receipt.job_id,
                                 request_key=SecretStr("k09-auth-no-retry-after-restore"))
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (1,)
            for name, value in {
                "stage": stage, "revocation": revocation, "status": status.status,
                "attempt": attempt.epoch, "failure_kind": attempt.failure_kind,
                "finished_at": attempt.finished_at.isoformat(), "retryable": status.retryable,
                "output_present": status.output_url is not None, "published_records": 0,
                "publish_audit": 0, "fail_audit": 1, "job_count": 1,
                "retry_after_authority_restored": "denied",
            }.items():
                record_property(name, value)
    _run(scenario())
