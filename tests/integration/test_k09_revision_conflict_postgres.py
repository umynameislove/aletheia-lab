"""A post-ACK project revision conflict stays fenced, typed and nonretryable."""
from __future__ import annotations

import os

import pytest
from pydantic import SecretStr
from test_k05_native_results_postgres import _case, _initial, _run
from test_k09_execution_postgres import _jobs

from aletheia_lab.hosted import persistence_migration as core
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.native_upload_records import build_upload_follow_up_result, result_view
from aletheia_lab.hosted.postgres_jobs import JobStateConflict
from aletheia_lab.hosted.project_operations import CanonicalProjectOperations
from aletheia_lab.hosted.upload_job_worker import run_one_upload_job
from aletheia_lab.product.follow_up import (
    build_product_follow_up_view,
    resolve_product_follow_up_selection,
)

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K09_TEST_DATABASE_URL'),
    reason='Explicit private K09 PostgreSQL URL required')]


@pytest.fixture(autouse=True)
def database(monkeypatch):
    monkeypatch.setenv('K05_TEST_DATABASE_URL', os.environ['K09_TEST_DATABASE_URL'])


def test_follow_up_changed_after_ack_has_typed_conflict_and_no_stale_publication(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            await _initial(case)
            jobs = await _jobs(case)
            assert await jobs.conflicts_ready()
            parent = result_view(case.initial)
            records, receipts = [], []
            for index in range(2):
                question = f'Which evidence remains uncertain for question {index}?'
                selection = resolve_product_follow_up_selection(parent, selection_kind='claim',
                    selection_id=parent.claims[0].id, question=question)
                records.append(build_upload_follow_up_result('a', parent, build_product_follow_up_view(parent, selection)))
                receipts.append(await jobs.enqueue_follow_up(credential_digest=case.a, workspace_id='a',
                    parent_result_id=case.initial.record_id, expected_revision=2,
                    request_key=SecretStr(f'revision-conflict-key-{index:02}'), selection_kind='claim',
                    selection_id=parent.claims[0].id, question=question))
            lease = SecretStr('a' * 64)
            first = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=('follow_up',))
            assert first is not None and first.job_id == receipts[0].job_id
            assert await jobs.complete_result(claim=first, lease_digest=lease, record=records[0])

            class StagedOperations(CanonicalProjectOperations):
                async def prepare_follow_up(self, claim):
                    assert claim.job_id == receipts[1].job_id
                    return records[1]

            operations = StagedOperations(case.sources, None, jobs)
            assert await run_one_upload_job(jobs, operations, lease_seconds=120)
            status = await jobs.status(credential_digest=case.a, workspace_id='a', job_id=receipts[1].job_id)
            assert status.status == 'failed' and status.retryable is False and status.output_url is None
            history = await jobs.attempts(credential_digest=case.a, workspace_id='a', job_id=status.job_id)
            assert history.items[-1].failure_kind == 'conflict'
            assert history.conflict.project_id == case.initial.project_id and history.conflict.current_revision == 3
            with pytest.raises(JobStateConflict):
                await jobs.retry(credential_digest=case.a, workspace_id='a', job_id=status.job_id,
                    request_key=SecretStr('conflict-cannot-retry-old-job'))
            async with case.pool.connection() as conn:
                assert await (await conn.execute('SELECT count(*) FROM k05_records WHERE record_id=%s',
                    (records[1].record_id,))).fetchone() == (0,)
                assert await (await conn.execute('SELECT revision FROM k05_projects WHERE project_id=%s',
                    (case.initial.project_id,))).fetchone() == (3,)
                await conn.execute("UPDATE j01_sessions SET state='revoked' WHERE context_id='ctx_a'")
            with pytest.raises(AuthorizationDenied):
                await jobs.attempts(credential_digest=case.a, workspace_id='a', job_id=status.job_id)
    _run(scenario())


def test_v16_to_v17_preserves_old_migration_checksums_and_canonical_bytes(tmp_path, monkeypatch):
    migrations = core.MIGRATIONS

    async def scenario():
        with monkeypatch.context() as old:
            old.setattr(core, 'MIGRATIONS', migrations[:16])
            manager = _case(tmp_path)
            case = await manager.__aenter__()
        try:
            await _initial(case)
            before = (await case.catalog.read_record(credential_digest=case.a, workspace_id='a',
                project_id=case.initial.project_id, record_id=case.initial.record_id)).model_dump_json()
            async with case.pool.connection() as conn:
                schema = (await (await conn.execute('SELECT current_schema()')).fetchone())[0]
                ledger = await (await conn.execute('SELECT version,checksum,signature FROM k05_persistence_migrations ORDER BY version')).fetchall()
                assert len(ledger) == 16
                assert await core.migrate(conn, schema) == 'upgraded'
                assert await core.verify(conn, schema)
                after = await (await conn.execute('SELECT version,checksum,signature FROM k05_persistence_migrations ORDER BY version')).fetchall()
                assert after[:16] == ledger and len(after) == 17
                assert await core.migrate(conn, schema) == 'unchanged'
            assert (await case.catalog.read_record(credential_digest=case.a, workspace_id='a',
                project_id=case.initial.project_id, record_id=case.initial.record_id)).model_dump_json() == before
        finally:
            await manager.__aexit__(None, None, None)
    _run(scenario())
