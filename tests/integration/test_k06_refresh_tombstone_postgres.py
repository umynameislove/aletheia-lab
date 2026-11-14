"""Both real refresh/delete commit orders retain one current truth without resurrection."""
from __future__ import annotations

import os

import pytest
from pydantic import SecretStr
from test_k02_refresh_postgres import _changed_metrics, _prepare
from test_k05_native_results_postgres import _case, _initial, _run
from test_k06_canonical_snapshot_postgres import _setup

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.postgres_sources import SourceNotFound
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
from aletheia_lab.hosted.workspace_management import WorkspaceConflict

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K06_TEST_DATABASE_URL"), reason="Explicit K06 PostgreSQL test URL required")]


@pytest.fixture(autouse=True)
def configured_database(monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])


@pytest.mark.parametrize("delete_first", [True, False])
def test_refresh_computed_before_delete_cannot_resurrect_project_in_either_commit_order(tmp_path, delete_first):
    async def scenario():
        async with _case(tmp_path) as case:
            _, initial = await _initial(case)
            jobs = await _setup(case)
            source = await case.sources.put(credential_digest=case.a, workspace_id="a",
                request_key=SecretStr("k06-delete-refresh-source"), payload=_changed_metrics(case.payload))
            private = tmp_path / "refresh-worker"
            private.mkdir()
            receipt, claim, lease, prepared = await _prepare(case, jobs, private,
                source=source.source_id, revision=2, key="k06-delete-refresh-job")
            mutations = PostgresProjectMutations(case.pool, case.catalog, audit=True)
            if delete_first:
                deleted = await mutations.delete(credential_digest=case.a, workspace_id="a",
                    project_id=initial.project_id, expected_revision=2)
                assert deleted["revision"] == 3
                with pytest.raises((AuthorizationDenied, SourceNotFound)):
                    await jobs.complete_refresh(claim=claim, lease_digest=lease, snapshot=prepared.snapshot)
                assert await jobs.fail(claim=claim, lease_digest=lease)
                current_snapshot, count, state = case.snapshot.snapshot_id, 1, "failed"
            else:
                assert await jobs.complete_refresh(claim=claim, lease_digest=lease, snapshot=prepared.snapshot)
                with pytest.raises(WorkspaceConflict):
                    await mutations.delete(credential_digest=case.a, workspace_id="a",
                        project_id=initial.project_id, expected_revision=2)
                deleted = await mutations.delete(credential_digest=case.a, workspace_id="a",
                    project_id=initial.project_id, expected_revision=3)
                assert deleted["revision"] == 4
                current_snapshot, count, state = prepared.snapshot.snapshot_id, 2, "succeeded"
            with pytest.raises((AuthorizationDenied, SourceNotFound)):
                await case.catalog.read_record(credential_digest=case.a, workspace_id="a",
                    project_id=initial.project_id, record_id=initial.record_id)
            with pytest.raises((AuthorizationDenied, SourceNotFound)):
                await jobs.output(credential_digest=case.a, workspace_id="a", job_id=receipt.job_id)
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT record_id FROM k05_current_pointers WHERE workspace_id='a' AND project_id=%s AND pointer_name='snapshot'", (initial.project_id,))).fetchone() == (current_snapshot,)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_snapshot'")).fetchone() == (count,)
                assert await (await conn.execute("SELECT state FROM j01_jobs WHERE job_id=%s", (receipt.job_id,))).fetchone() == (state,)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='deletion'")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.publish' AND job_id=%s", (receipt.job_id,))).fetchone() == (0 if delete_first else 1,)
                assert await (await conn.execute("SELECT count(*) FROM j01_sources")).fetchone() == (2,)
    _run(scenario())
