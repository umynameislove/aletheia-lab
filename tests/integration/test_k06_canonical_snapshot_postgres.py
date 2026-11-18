"""Job-fenced native snapshot publication on real PostgreSQL and filesystem bytes."""
from __future__ import annotations

import os
from datetime import timedelta

import pytest
from pydantic import SecretStr
from test_k05_native_results_postgres import _case, _run

from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.object_store import ObjectIntegrityError
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources, SourceNotFound
from aletheia_lab.hosted.upload_snapshot import UploadSnapshot, build_upload_snapshot
from aletheia_lab.hosted.workspace_management import WorkspaceManagement

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K06_TEST_DATABASE_URL"),
    reason="Explicit K-06 PostgreSQL test URL required")]


async def _setup(case, jobs_type=PostgresJobs):
    async with case.pool.connection() as conn:
        schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
        await migrate_audit(conn, schema)
    case.sources = PostgresSources(case.pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
                                  maximum_entries=10, catalog=case.catalog)
    return jobs_type(case.pool, case.sources, audit=True, catalog=case.catalog)


async def _admit(jobs, case, key):
    receipt = await jobs.enqueue(credential_digest=case.a, workspace_id="a", operation="confirm",
        request_key=SecretStr(key), request={"source_id": case.receipt.source_id,
                                           "mapping": case.snapshot.mapping.model_dump(mode="json")})
    lease = SecretStr("d" * 64)
    claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=("confirm",))
    assert claim is not None and claim.job_id == receipt.job_id
    return claim, lease


async def _assert_unpublished(case, job_id):
    async with case.pool.connection() as conn:
        assert await (await conn.execute("SELECT state,result FROM j01_jobs WHERE job_id=%s", (job_id,))).fetchone() == (
            "running", None)
        for table in ("k05_projects", "k05_records", "k05_current_pointers", "j01_upload_snapshots"):
            assert await (await conn.execute("SELECT count(*) FROM " + table)).fetchone() == (0,)
        assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.publish'")).fetchone() == (0,)


def test_canonical_snapshot_projection_terminal_and_audit_commit_once_and_keep_first_capture(tmp_path, monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])

    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _setup(case)
            claim, lease = await _admit(jobs, case, "canonical-snapshot-first-key")
            assert await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot)
            # A worker retry after an ACK was lost cannot append another effect/audit.
            assert not await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot)
            later = build_upload_snapshot(workspace_id="a", source_id=case.snapshot.source_id,
                source_sha256=case.snapshot.source_sha256, mapping=case.snapshot.mapping,
                mapping_result=case.snapshot.mapping_result, captured_at=case.snapshot.captured_at + timedelta(seconds=30))
            assert later.snapshot_id == case.snapshot.snapshot_id and later.record_sha256 != case.snapshot.record_sha256
            second, second_lease = await _admit(jobs, case, "canonical-snapshot-second-key")
            assert await jobs.complete_snapshot(claim=second, lease_digest=second_lease, snapshot=later)
            stored = await case.catalog.read_record(credential_digest=case.a, workspace_id="a",
                project_id=case.snapshot.mapping.project_id, record_id=case.snapshot.snapshot_id)
            assert stored == case.snapshot
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT provenance_kind,revision FROM k05_projects")).fetchone() == ("upload", 1)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_snapshot'")).fetchone() == (1,)
                row = await (await conn.execute("SELECT canonical_record_id,payload::text FROM j01_upload_snapshots")).fetchone()
                assert row[0] == case.snapshot.snapshot_id and UploadSnapshot.model_validate_json(row[1]) == case.snapshot
                assert await (await conn.execute("SELECT record_id FROM k05_current_pointers WHERE pointer_name='snapshot'")).fetchone() == (
                    case.snapshot.snapshot_id,)
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs WHERE state='succeeded' AND result->>'snapshot_id'=%s",
                    (case.snapshot.snapshot_id,))).fetchone() == (2,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.publish' AND effect_id=%s "
                    "AND parent_id=%s", (case.snapshot.snapshot_id, case.snapshot.mapping.project_id))).fetchone() == (2,)
    _run(scenario())


def test_failure_after_terminal_and_audit_rolls_back_every_canonical_effect_then_retry_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])

    class FailAfterAudit(PostgresJobs):
        async def _record_action(self, *args, **kwargs):
            await super()._record_action(*args, **kwargs)
            if args[3] == "job.publish":
                raise RuntimeError("synthetic failure after terminal and audit")

    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _setup(case, FailAfterAudit)
            claim, lease = await _admit(jobs, case, "canonical-snapshot-rollback-key")
            with pytest.raises(RuntimeError, match="synthetic failure after terminal and audit"):
                await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot)
            await _assert_unpublished(case, claim.job_id)
            normal = PostgresJobs(case.pool, case.sources, audit=True, catalog=case.catalog)
            assert await normal.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot)
    _run(scenario())


def test_terminal_lease_fence_rejection_rolls_back_canonical_projection_and_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])

    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _setup(case)
            claim, lease = await _admit(jobs, case, "canonical-snapshot-expiry-key")
            async with case.pool.connection() as conn:
                # Deterministic database fault after canonical enlistment, before the terminal CAS.
                await conn.execute("CREATE FUNCTION k06_expire_publication_lease() RETURNS trigger LANGUAGE plpgsql "
                    "SET search_path FROM CURRENT AS $$ BEGIN UPDATE j01_jobs SET lease_until=clock_timestamp()-interval '1 second' "
                    "WHERE workspace_id=NEW.workspace_id AND state='running' AND operation='confirm'; RETURN NEW; END $$")
                await conn.execute("CREATE TRIGGER k06_expire_publication_lease AFTER INSERT ON j01_upload_snapshots "
                    "FOR EACH ROW EXECUTE FUNCTION k06_expire_publication_lease()")
            with pytest.raises(TimeoutError):
                await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot)
            await _assert_unpublished(case, claim.job_id)
            async with case.pool.connection() as conn:
                await conn.execute("DROP TRIGGER k06_expire_publication_lease ON j01_upload_snapshots")
            assert await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot)
    _run(scenario())


def test_pre_v5_null_link_projection_keeps_first_capture_when_reconfirmed(tmp_path, monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])

    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _setup(case)
            async with case.pool.connection() as conn:
                await conn.execute("INSERT INTO j01_upload_snapshots(snapshot_id,workspace_id,source_id,payload) "
                    "VALUES(%s,'a',%s,%s::jsonb)", (case.snapshot.snapshot_id, case.snapshot.source_id, case.snapshot.model_dump_json()))
                before = await (await conn.execute("SELECT payload::text,canonical_record_id FROM j01_upload_snapshots")).fetchone()
                assert before[1] is None
            later = build_upload_snapshot(workspace_id="a", source_id=case.snapshot.source_id,
                source_sha256=case.snapshot.source_sha256, mapping=case.snapshot.mapping,
                mapping_result=case.snapshot.mapping_result, captured_at=case.snapshot.captured_at + timedelta(seconds=30))
            assert later.snapshot_id == case.snapshot.snapshot_id and later.record_sha256 != case.snapshot.record_sha256
            claim, lease = await _admit(jobs, case, "canonical-snapshot-legacy-first-key")
            assert await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=later)
            stored = await case.catalog.read_record(credential_digest=case.a, workspace_id="a",
                project_id=case.snapshot.mapping.project_id, record_id=case.snapshot.snapshot_id)
            assert stored == case.snapshot and stored.record_sha256 != later.record_sha256
            async with case.pool.connection() as conn:
                after = await (await conn.execute("SELECT payload::text,canonical_record_id FROM j01_upload_snapshots")).fetchone()
                assert after == (before[0], case.snapshot.snapshot_id)
                assert await (await conn.execute("SELECT revision FROM k05_projects")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.publish'")).fetchone() == (1,)
    _run(scenario())


@pytest.mark.parametrize("denial", ["sha", "grant"])
def test_snapshot_source_sha_or_current_grant_mismatch_denies_before_stage(tmp_path, monkeypatch, denial):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])

    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _setup(case)
            snapshot = case.snapshot
            if denial == "sha":
                claim, lease = await _admit(jobs, case, "canonical-snapshot-wrong-sha-key")
                snapshot = build_upload_snapshot(workspace_id="a", source_id=snapshot.source_id,
                    source_sha256="f" * 64, mapping=snapshot.mapping, mapping_result=snapshot.mapping_result,
                    captured_at=snapshot.captured_at)
            else:
                async with case.pool.connection() as conn:
                    await conn.execute("UPDATE j01_memberships SET role='analyst',revision=revision+1 "
                        "WHERE workspace_id='a' AND user_id='owner_b'")
                manager = WorkspaceManagement(case.pool)
                await manager.set_source_grant(credential_digest=case.a, workspace_id="a", source_id=snapshot.source_id,
                    user_id="owner_b", actions=["read", "write"], expected_revision=0)
                receipt = await jobs.enqueue(credential_digest=case.b, workspace_id="a", operation="confirm",
                    request_key=SecretStr("canonical-snapshot-revoked-grant-key"), request={"source_id": snapshot.source_id,
                        "mapping": snapshot.mapping.model_dump(mode="json")})
                lease = SecretStr("e" * 64)
                claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=("confirm",))
                assert claim is not None and claim.job_id == receipt.job_id
                await manager.set_source_grant(credential_digest=case.a, workspace_id="a", source_id=snapshot.source_id,
                    user_id="owner_b", actions=[], expected_revision=1)
            with pytest.raises(SourceNotFound):
                await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=snapshot)
            await _assert_unpublished(case, claim.job_id)
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_object_leases")).fetchone() == (0,)
    _run(scenario())


def test_null_projection_link_cannot_shadow_or_repair_corrupt_existing_canonical_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])

    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _setup(case)
            first, first_lease = await _admit(jobs, case, "canonical-before-null-shadow-key")
            assert await jobs.complete_snapshot(claim=first, lease_digest=first_lease, snapshot=case.snapshot)
            async with case.pool.connection() as conn:
                await conn.execute("UPDATE j01_upload_snapshots SET canonical_record_id=NULL")
                digest = (await (await conn.execute("SELECT object_sha256 FROM k05_records WHERE record_id=%s",
                    (case.snapshot.snapshot_id,))).fetchone())[0]
            path = case.objects.store.path(digest)
            assert path.is_relative_to(tmp_path)
            original = path.read_bytes()
            corrupt = b"x" * len(original)
            path.write_bytes(corrupt)
            later = build_upload_snapshot(workspace_id="a", source_id=case.snapshot.source_id,
                source_sha256=case.snapshot.source_sha256, mapping=case.snapshot.mapping,
                mapping_result=case.snapshot.mapping_result, captured_at=case.snapshot.captured_at + timedelta(seconds=30))
            second, second_lease = await _admit(jobs, case, "canonical-after-null-shadow-key")
            with pytest.raises(ObjectIntegrityError):
                await jobs.complete_snapshot(claim=second, lease_digest=second_lease, snapshot=later)
            assert path.read_bytes() == corrupt
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT canonical_record_id FROM j01_upload_snapshots")).fetchone() == (None,)
                assert await (await conn.execute("SELECT state,result FROM j01_jobs WHERE job_id=%s", (second.job_id,))).fetchone() == ("running", None)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.publish'")).fetchone() == (1,)
    _run(scenario())
