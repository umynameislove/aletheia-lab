"""Follow-up ledger, publication CAS, revoke and worker-context policy on real PostgreSQL."""
from __future__ import annotations

import asyncio
import os

import pytest
from pydantic import SecretStr
from test_k05_canonical_results_postgres import _case as _local_case
from test_k05_canonical_results_postgres import _child, _run
from test_k05_native_results_postgres import _case as _native_case
from test_k06_canonical_snapshot_postgres import _admit, _setup

from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.native_upload_records import build_upload_follow_up_result, result_view
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources, SourceConflict
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.workspace_management import WorkspaceConflict
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.repositories import RotateSession, SessionRules
from aletheia_lab.hosted_auth.worker_sessions import WorkerPostgresSources
from aletheia_lab.product.follow_up import (
    build_product_follow_up_view,
    resolve_product_follow_up_selection,
)

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K06_TEST_DATABASE_URL"),
    reason="Explicit K-06 PostgreSQL test URL required")]


async def _jobs(pool, catalog):
    async with pool.connection() as conn:
        schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
        await migrate_audit(conn, schema)
    sources = PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10,
                              catalog=catalog)
    return PostgresJobs(pool, sources, catalog=catalog, audit=True), sources


def test_follow_up_concurrent_admission_replay_after_commit_keeps_receipt_and_one_effect(tmp_path, monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])

    async def run():
        async with _local_case(tmp_path) as (pool, _, catalog, _, a, _, parent, _):
            jobs, sources = await _jobs(pool, catalog)
            view = result_view(parent)
            arguments = dict(credential_digest=a, workspace_id="a", parent_result_id=parent.record_id,
                expected_revision=0, request_key=SecretStr("follow-up-durable-admission-key"),
                selection_kind="claim", selection_id=view.claims[0].id, question="Which evidence remains uncertain?")
            barrier = asyncio.Barrier(6)

            async def admit():
                await barrier.wait()
                return await jobs.enqueue_follow_up(**arguments)

            receipts = await asyncio.gather(*(admit() for _ in range(6)))
            assert len({receipt.job_id for receipt in receipts}) == 1
            assert not await PostgresJobs(pool, sources).follow_up_ready()
            assert await PostgresJobs(pool, sources).claim(lease_digest=SecretStr("c" * 64), lease_seconds=120) is None
            with pytest.raises(SourceConflict):
                await jobs.enqueue_follow_up(**{**arguments, "question": "What else remains uncertain?"})
            with pytest.raises(WorkspaceConflict):
                await jobs.enqueue_follow_up(**{**arguments, "request_key": SecretStr("follow-up-different-stale-key"), "expected_revision": 99})
            lease = SecretStr("d" * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=("follow_up",))
            assert claim is not None and claim.job_id == receipts[0].job_id
            assert await jobs.follow_up_parent(claim=claim) == view
            child = _child(parent, arguments["question"])
            assert await jobs.complete_result(claim=claim, lease_digest=lease, record=child)
            assert not await jobs.complete_result(claim=claim, lease_digest=lease, record=child)
            assert await jobs.enqueue_follow_up(**arguments) == receipts[0]
            assert await catalog.read_record(credential_digest=a, workspace_id="a", project_id=parent.project_id,
                record_id=child.record_id) == child
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id='a' AND project_id=%s", (parent.project_id,))).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs WHERE operation='follow_up'")).fetchone() == (1,)
                assert await (await conn.execute("SELECT state,canonical_result_id FROM j01_jobs WHERE job_id=%s", (claim.job_id,))).fetchone() == ("succeeded", child.record_id)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.admit'")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.publish'")).fetchone() == (1,)
    _run(run())


def test_follow_up_current_project_grant_revoke_after_stage_prevents_publication(tmp_path, monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])

    async def run():
        async with _local_case(tmp_path) as (pool, _, catalog, _, a, b, parent, _):
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_memberships SET role='analyst',revision=revision+1 WHERE workspace_id='a' AND user_id='owner_b'")
            manager = PostgresProjectAccess(pool)
            await manager.set_grant(credential_digest=a, workspace_id="a", project_id=parent.project_id,
                user_id="owner_b", actions=["read", "write"], expected_revision=0)
            jobs, _ = await _jobs(pool, catalog)
            view, question = result_view(parent), "Which evidence remains uncertain after review?"
            receipt = await jobs.enqueue_follow_up(credential_digest=b, workspace_id="a", parent_result_id=parent.record_id,
                expected_revision=0, request_key=SecretStr("follow-up-revoke-stage-key"), selection_kind="claim",
                selection_id=view.claims[0].id, question=question)
            lease = SecretStr("e" * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=("follow_up",))
            assert claim is not None and claim.job_id == receipt.job_id
            child = _child(parent, question)
            entered, release = asyncio.Event(), asyncio.Event()
            original = jobs._canonical.prepare_result

            async def stage(*args, **kwargs):
                prepared = await original(*args, **kwargs)
                entered.set()
                await release.wait()
                return prepared

            jobs._canonical.prepare_result = stage
            pending = asyncio.create_task(jobs.complete_result(claim=claim, lease_digest=lease, record=child))
            await asyncio.wait_for(entered.wait(), 10)
            await manager.set_grant(credential_digest=a, workspace_id="a", project_id=parent.project_id,
                user_id="owner_b", actions=[], expected_revision=1)
            release.set()
            with pytest.raises(AuthorizationDenied):
                await pending
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_id=%s", (child.record_id,))).fetchone() == (0,)
                assert await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id='a' AND project_id=%s", (parent.project_id,))).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.publish'")).fetchone() == (0,)
    _run(run())


def test_native_follow_up_worker_reads_and_publishes_across_rotation_in_original_context(tmp_path, monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])

    async def run():
        async with _native_case(tmp_path) as case:
            api = await _setup(case)
            confirming, confirm_lease = await _admit(api, case, "worker-native-confirm-key")
            assert await api.complete_snapshot(claim=confirming, lease_digest=confirm_lease, snapshot=case.snapshot)
            analysis = await api.enqueue(credential_digest=case.a, workspace_id="a", operation="analyze",
                request_key=SecretStr("worker-native-analysis-key"), request={"snapshot_id": case.snapshot.snapshot_id,
                    "question": case.view.conversation.turns[0].question})
            analysis_lease = SecretStr("1" * 64)
            analyzing = await api.claim(lease_digest=analysis_lease, lease_seconds=120, operations=("analyze",))
            assert analyzing is not None and analyzing.job_id == analysis.job_id
            assert await api.complete(claim=analyzing, lease_digest=analysis_lease, result=case.view.model_dump(mode="json"))
            question = "What remains unknown in this exact evidence?"
            receipt = await api.enqueue_follow_up(credential_digest=case.a, workspace_id="a", parent_result_id=case.initial.record_id,
                expected_revision=2, request_key=SecretStr("worker-native-follow-up-key"), selection_kind="claim",
                selection_id=case.view.claims[0].id, question=question)
            sources = WorkerPostgresSources(case.pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
                                           maximum_entries=10, catalog=case.catalog)
            worker = PostgresJobs(case.pool, sources, catalog=case.catalog, audit=True)
            lease = SecretStr("2" * 64)
            claim = await worker.claim(lease_digest=lease, lease_seconds=120, operations=("follow_up",))
            assert claim is not None and claim.job_id == receipt.job_id
            sessions = PostgresSessionStore(case.pool)
            previous = await sessions.inspect(case.a)
            replacement = SecretStr("3" * 64)
            rotated = await sessions.rotate_if_current(RotateSession(presented_digest=case.a, replacement_digest=replacement,
                expected_user_id=previous.actor.user_id, expected_context_id=previous.context_id,
                expected_generation=previous.generation, expected_account_epoch=previous.account_epoch,
                rules=SessionRules(idle_seconds=900, absolute_seconds=28000, rotation_seconds=1)))
            assert rotated is not None and rotated.generation == previous.generation + 1
            with pytest.raises(AuthorizationDenied):
                await api.follow_up_parent(claim=claim)
            parent = await worker.follow_up_parent(claim=claim)
            assert parent == case.view
            selection = resolve_product_follow_up_selection(parent, selection_kind="claim", selection_id=parent.claims[0].id, question=question)
            child = build_upload_follow_up_result("a", parent, build_product_follow_up_view(parent, selection))
            assert await worker.complete_result(claim=claim, lease_digest=lease, record=child)
            stored = await case.catalog.read_record(credential_digest=replacement, workspace_id="a",
                project_id=child.project_id, record_id=child.record_id)
            assert stored == child
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT revision FROM k05_projects")).fetchone() == (3,)
                assert await (await conn.execute("SELECT state,canonical_result_id FROM j01_jobs WHERE job_id=%s", (claim.job_id,))).fetchone() == ("succeeded", child.record_id)
    _run(run())
