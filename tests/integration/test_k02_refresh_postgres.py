"""Refresh A→A, A→B→A keeps immutable first captures and current CAS fences."""
from __future__ import annotations

import hashlib
import io
import os
import zipfile
from copy import copy
from datetime import timedelta

import pytest
from pydantic import SecretStr
from test_k05_migrations_postgres import _run
from test_k05_native_results_postgres import _case, _initial

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.project_refresh import refresh_uploaded_project
from aletheia_lab.hosted.runtime_management import RuntimeManagement
from aletheia_lab.hosted.workspace_management import WorkspaceConflict, WorkspaceManagement
from aletheia_lab.hosted_contract.project_refresh import RefreshResult
from aletheia_lab.hosted_contract.runtime_management import ChangeProject

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K02_TEST_DATABASE_URL"),
    reason="Explicit K02 owned-schema PostgreSQL test URL required")]


@pytest.fixture(autouse=True)
def configured_database(monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K02_TEST_DATABASE_URL"])


def _changed_metrics(payload):
    target = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(payload)) as before, zipfile.ZipFile(target, "w") as after:
        for info in before.infolist():
            data = before.read(info.filename)
            if info.filename == "metrics.csv":
                assert b"candidate,loss,0.6" in data
                data = data.replace(b"candidate,loss,0.6", b"candidate,loss,0.8")
            after.writestr(copy(info), data)
    assert target.getvalue() != payload
    return target.getvalue()


async def _prepare(case, jobs, private, *, source, revision, key):
    receipt = await jobs.enqueue_refresh(credential_digest=case.a, workspace_id="a",
        project_id=case.snapshot.mapping.project_id, expected_revision=revision,
        request_key=SecretStr(key), source_id=source)
    lease = SecretStr(hashlib.sha256(key.encode()).hexdigest())
    claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=("refresh",))
    assert claim is not None and claim.job_id == receipt.job_id
    parent, origin = await jobs.refresh_parent(claim=claim)
    before = await case.sources.read(credential_digest=case.a, workspace_id="a", source_id=parent.source_id)
    new = await case.sources.read(credential_digest=case.a, workspace_id="a", source_id=source)
    prepared = refresh_uploaded_project(parent=parent, origin_source_sha256=origin,
        before_payload=before, new_payload=new, source_id=source, private_root=private,
        captured_at=parent.captured_at + timedelta(hours=1), maximum_upload_bytes=10000,
        maximum_expanded_bytes=20000, maximum_entries=10)
    return receipt, claim, lease, prepared


def test_refresh_same_then_changed_then_original_reuses_first_capture_and_advances_pointer(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            _, initial = await _initial(case)
            assert initial.revision == 2
            jobs = PostgresJobs(case.pool, case.sources, catalog=case.catalog)
            private = tmp_path / "refresh-worker"
            private.mkdir()
            first_capture = case.snapshot.model_dump_json()
            same = await _prepare(case, jobs, private, source=case.receipt.source_id,
                revision=2, key="refresh-pg-same-source-001")
            receipt, claim, lease, prepared = same
            assert prepared.status == "unchanged" and prepared.snapshot.model_dump_json() == first_capture
            assert await jobs.complete_refresh(claim=claim, lease_digest=lease, snapshot=prepared.snapshot)
            unchanged = RefreshResult.model_validate((await jobs.output(credential_digest=case.a,
                workspace_id="a", job_id=receipt.job_id))["result"])
            assert unchanged.status == "unchanged" and unchanged.revision == 2
            source_b = await case.sources.put(credential_digest=case.a, workspace_id="a",
                request_key=SecretStr("refresh-pg-source-b-002"), payload=_changed_metrics(case.payload))
            receipt_b, claim_b, lease_b, prepared_b = await _prepare(case, jobs, private, source=source_b.source_id,
                revision=2, key="refresh-pg-change-to-b-003")
            assert prepared_b.status == "new_snapshot" and prepared_b.snapshot.snapshot_id != case.snapshot.snapshot_id
            assert await jobs.complete_refresh(claim=claim_b, lease_digest=lease_b, snapshot=prepared_b.snapshot)
            changed = RefreshResult.model_validate((await jobs.output(credential_digest=case.a,
                workspace_id="a", job_id=receipt_b.job_id))["result"])
            assert changed.revision == 3 and changed.snapshot_id == prepared_b.snapshot.snapshot_id
            receipt_a, claim_a, lease_a, prepared_a = await _prepare(case, jobs, private, source=case.receipt.source_id,
                revision=3, key="refresh-pg-back-to-a-004")
            assert prepared_a.status == "new_snapshot" and prepared_a.snapshot.snapshot_id == case.snapshot.snapshot_id
            assert prepared_a.snapshot.captured_at != case.snapshot.captured_at
            # Regression: an existing immutable A is legal when current pointer is B and CAS is exact.
            assert await jobs.complete_refresh(claim=claim_a, lease_digest=lease_a, snapshot=prepared_a.snapshot)
            returned = RefreshResult.model_validate((await jobs.output(credential_digest=case.a,
                workspace_id="a", job_id=receipt_a.job_id))["result"])
            assert returned.status == "new_snapshot" and returned.revision == 4
            assert returned.previous_snapshot_id == prepared_b.snapshot.snapshot_id
            assert returned.snapshot_id == case.snapshot.snapshot_id
            restored = await case.catalog.read_record(credential_digest=case.a, workspace_id="a",
                project_id=case.snapshot.mapping.project_id, record_id=case.snapshot.snapshot_id)
            assert restored.model_dump_json() == first_capture
            assert await case.catalog.read_record(credential_digest=case.a, workspace_id="a",
                project_id=case.snapshot.mapping.project_id, record_id=prepared_b.snapshot.snapshot_id) == prepared_b.snapshot
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id='a' "
                    "AND project_id=%s", (case.snapshot.mapping.project_id,))).fetchone() == (4,)
                assert await (await conn.execute("SELECT record_id FROM k05_current_pointers WHERE workspace_id='a' "
                    "AND project_id=%s AND pointer_name='snapshot'", (case.snapshot.mapping.project_id,))).fetchone() == (case.snapshot.snapshot_id,)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE workspace_id='a' "
                    "AND record_type='upload_snapshot'")).fetchone() == (2,)
                assert await (await conn.execute("SELECT payload=%s::jsonb FROM j01_upload_snapshots "
                    "WHERE workspace_id='a' AND snapshot_id=%s", (first_capture, case.snapshot.snapshot_id))).fetchone() == (True,)
    _run(scenario())


@pytest.mark.parametrize("interruption,error", [("stale", WorkspaceConflict), ("revoke", AuthorizationDenied)])
def test_refresh_rechecks_stale_revision_and_current_membership_before_publication(tmp_path, interruption, error):
    async def scenario():
        async with _case(tmp_path) as case:
            await _initial(case)
            jobs = PostgresJobs(case.pool, case.sources, catalog=case.catalog)
            private = tmp_path / "refresh-worker"
            private.mkdir()
            source = await case.sources.put(credential_digest=case.a, workspace_id="a",
                request_key=SecretStr("refresh-pg-interrupted-source"), payload=_changed_metrics(case.payload))
            receipt, claim, lease, prepared = await _prepare(case, jobs, private, source=source.source_id,
                revision=2, key="refresh-pg-interrupted-job-001")
            if interruption == "stale":
                await RuntimeManagement(case.pool).update_project(credential_digest=case.a, workspace_id="a",
                    project_id=case.snapshot.mapping.project_id, expected_revision=2,
                    request=ChangeProject(schema_version="product-api/v1-draft", display_name="Concurrent metadata"))
            else:
                await WorkspaceManagement(case.pool).mutate_member(credential_digest=case.b, workspace_id="a",
                    user_id="owner_a", expected_revision=1, active=False)
            with pytest.raises(error):
                await jobs.complete_refresh(claim=claim, lease_digest=lease, snapshot=prepared.snapshot)
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT record_id FROM k05_current_pointers WHERE workspace_id='a' "
                    "AND project_id=%s AND pointer_name='snapshot'", (case.snapshot.mapping.project_id,))).fetchone() == (case.snapshot.snapshot_id,)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE workspace_id='a' "
                    "AND record_id=%s", (prepared.snapshot.snapshot_id,))).fetchone() == (0,)
                assert await (await conn.execute("SELECT state FROM j01_jobs WHERE job_id=%s", (receipt.job_id,))).fetchone() == ("running",)
    _run(scenario())
