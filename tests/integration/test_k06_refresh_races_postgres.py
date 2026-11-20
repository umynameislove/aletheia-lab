"""Competing refresh publications must preserve one CAS successor and old results."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import pytest
from pydantic import SecretStr
from test_k02_refresh_postgres import _changed_metrics, _prepare
from test_k05_native_results_postgres import _case, _initial, _run

from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.workspace_management import WorkspaceConflict

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K06_TEST_DATABASE_URL"), reason="Explicit K06 PostgreSQL test URL required")]


class _PublicationBarrier:
    def __init__(self, pool):
        self.pool = pool
        self.barrier = asyncio.Barrier(2)
        self.tasks = set()
        self.backend_pids = set()

    @asynccontextmanager
    async def connection(self):
        async with self.pool.connection() as conn:
            owner = self

            class Connection:
                autocommit = False

                async def execute(self, query, params=(), *, binary=False):
                    task = asyncio.current_task()
                    if query.startswith("INSERT INTO k05_projects") and task not in owner.tasks:
                        owner.tasks.add(task)
                        owner.backend_pids.add((await (await conn.execute("SELECT pg_backend_pid()")).fetchone())[0])
                        await owner.barrier.wait()
                    return await conn.execute(query, params, binary=binary)

            yield Connection()


def test_two_refresh_jobs_at_same_revision_publish_one_snapshot_and_keep_old_result(tmp_path, monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])

    async def scenario():
        async with _case(tmp_path) as case:
            _, initial = await _initial(case)
            jobs = PostgresJobs(case.pool, case.sources, catalog=case.catalog)
            source = await case.sources.put(credential_digest=case.a, workspace_id="a",
                request_key=SecretStr("k06-refresh-race-source-0001"), payload=_changed_metrics(case.payload))
            private = tmp_path / "refresh-worker"
            private.mkdir()
            prepared = [await _prepare(case, jobs, private, source=source.source_id,
                revision=2, key=f"k06-refresh-race-job-{index:04d}") for index in range(2)]
            assert prepared[0][3].snapshot.snapshot_id == prepared[1][3].snapshot.snapshot_id
            barrier = _PublicationBarrier(case.pool)
            competing = PostgresJobs(barrier, case.sources, catalog=case.catalog)
            outcomes = await asyncio.wait_for(asyncio.gather(*(
                competing.complete_refresh(claim=claim, lease_digest=lease, snapshot=result.snapshot)
                for _, claim, lease, result in prepared), return_exceptions=True), 15)
            assert sum(value is True for value in outcomes) == 1, outcomes
            assert sum(isinstance(value, WorkspaceConflict) for value in outcomes) == 1, outcomes
            assert len(barrier.backend_pids) == 2
            winner = outcomes.index(True)
            receipt, _, _, result = prepared[winner]
            output = await jobs.output(credential_digest=case.a, workspace_id="a", job_id=receipt.job_id)
            assert output["result"]["revision"] == 3
            assert output["result"]["snapshot_id"] == result.snapshot.snapshot_id
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id='a' "
                    "AND project_id=%s", (initial.project_id,))).fetchone() == (3,)
                assert await (await conn.execute("SELECT record_id FROM k05_current_pointers WHERE workspace_id='a' "
                    "AND project_id=%s AND pointer_name='snapshot'", (initial.project_id,))).fetchone() == (
                        result.snapshot.snapshot_id,)
                assert await (await conn.execute("SELECT state,count(*) FROM j01_jobs WHERE operation='refresh' "
                    "GROUP BY state ORDER BY state")).fetchall() == [("running", 1), ("succeeded", 1)]
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_snapshot'")).fetchone() == (2,)
            assert await case.catalog.read_record(credential_digest=case.a, workspace_id="a",
                project_id=initial.project_id, record_id=initial.record_id) == case.initial
            assert await case.catalog.read_record(credential_digest=case.a, workspace_id="a",
                project_id=initial.project_id, record_id=case.snapshot.snapshot_id) == case.snapshot
    _run(scenario())
