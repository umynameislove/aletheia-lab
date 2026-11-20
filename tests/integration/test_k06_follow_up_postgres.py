"""Real queue commits native snapshot and initial analysis into one canonical catalog."""
from __future__ import annotations

import os

import pytest
from pydantic import SecretStr
from test_k05_native_results_postgres import _case, _run

from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.native_upload_records import UploadResultRecord
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K06_TEST_DATABASE_URL"),
    reason="Explicit K-06 PostgreSQL test URL required")]


def test_real_queue_snapshot_and_initial_analysis_publish_canonical_ids_receipts_and_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])

    async def run():
        async with _case(tmp_path) as case:
            async with case.pool.connection() as conn:
                schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
                await migrate_audit(conn, schema)
            case.sources = PostgresSources(case.pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
                                          maximum_entries=10, catalog=case.catalog)
            jobs = PostgresJobs(case.pool, case.sources, audit=True, catalog=case.catalog)
            assert await jobs.follow_up_ready()
            confirm = await jobs.enqueue(credential_digest=case.a, workspace_id="a", operation="confirm",
                request_key=SecretStr("initial-native-confirm-key"), request={"source_id": case.receipt.source_id,
                    "mapping": case.snapshot.mapping.model_dump(mode="json")})
            confirm_lease = SecretStr("a" * 64)
            confirming = await jobs.claim(lease_digest=confirm_lease, lease_seconds=120, operations=("confirm",))
            assert confirming is not None and confirming.job_id == confirm.job_id
            assert await jobs.complete_snapshot(claim=confirming, lease_digest=confirm_lease, snapshot=case.snapshot)
            analysis_request = {"snapshot_id": case.snapshot.snapshot_id,
                                "question": case.view.conversation.turns[0].question}
            analyzing = await jobs.enqueue(credential_digest=case.a, workspace_id="a", operation="analyze",
                request_key=SecretStr("initial-native-analyze-key"), request=analysis_request)
            analysis_lease = SecretStr("b" * 64)
            claim = await jobs.claim(lease_digest=analysis_lease, lease_seconds=120, operations=("analyze",))
            assert claim is not None and claim.job_id == analyzing.job_id
            assert await jobs.complete(claim=claim, lease_digest=analysis_lease, result=case.view.model_dump(mode="json"))
            stored = await case.catalog.read_record(credential_digest=case.a, workspace_id="a",
                project_id=case.initial.project_id, record_id=case.initial.record_id)
            assert isinstance(stored, UploadResultRecord) and stored == case.initial
            assert await jobs.output(credential_digest=case.a, workspace_id="a", job_id=analyzing.job_id) == {
                "schema_version": "product-api/v1-draft", "workspace_id": "a", "job_id": analyzing.job_id,
                "operation": "analyze", "result": case.view.model_dump(mode="json")}
            replay = await PostgresJobs(case.pool, case.sources, audit=True, catalog=case.catalog).enqueue(
                credential_digest=case.a, workspace_id="a", operation="analyze",
                request_key=SecretStr("initial-native-analyze-key"), request=analysis_request)
            assert replay == analyzing
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT provenance_kind,revision FROM k05_projects")).fetchone() == ("upload", 2)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_snapshot'")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_result'")).fetchone() == (1,)
                assert await (await conn.execute("SELECT state,canonical_result_id FROM j01_jobs WHERE job_id=%s",
                    (analyzing.job_id,))).fetchone() == ("succeeded", case.initial.record_id)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.admit'")).fetchone() == (2,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.publish'")).fetchone() == (2,)
                assert await (await conn.execute("SELECT parent_id,effect_id FROM k06_action_audit WHERE action='job.publish' AND job_id=%s",
                    (analyzing.job_id,))).fetchone() == (case.initial.project_id, case.initial.record_id)
    _run(run())
