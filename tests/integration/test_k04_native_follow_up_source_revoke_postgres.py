"""Native follow-up needs current source write at admission, dispatch and commit."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import pytest
from pydantic import SecretStr
from test_k04_native_catalog_http_postgres import _native_application
from test_k04_runtime_principal_postgres import _run

from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.native_upload_records import build_upload_follow_up_result
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.runtime_privileges import grant_runtime
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.product.follow_up import (
    build_product_follow_up_view,
    resolve_product_follow_up_selection,
)

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit K-04 PostgreSQL test URL required")]


async def _source_actions(case, actions):
    async with case.api.connection() as conn:
        row = await (await conn.execute("SELECT revision FROM j01_source_grants WHERE workspace_id=%s "
            "AND source_id=%s AND user_id='analyst'", (case.workspace, case.source.source_id))).fetchone()
    await WorkspaceManagement(case.api).set_source_grant(credential_digest=case.digests["owner_a"],
        workspace_id=case.workspace, source_id=case.source.source_id, user_id="analyst", actions=actions,
        expected_revision=0 if row is None else row[0])


@asynccontextmanager
async def _jobs(tmp_path):
    async with _native_application(tmp_path) as case:
        async with case.api.connection() as conn:
            schema, role, session_role = await (await conn.execute(
                "SELECT current_schema(),current_user,session_user")).fetchone()
            assert role == session_role and role.startswith("k04_api_")
        async with case.owner.connection() as conn:
            await migrate_audit(conn, schema)
            await grant_runtime(conn, schema, role, profile="api", audit=True)
        await PostgresProjectAccess(case.api).set_grant(credential_digest=case.digests["owner_a"],
            workspace_id=case.workspace, project_id=case.initial.project_id, user_id="analyst",
            actions=["read", "write"], expected_revision=1)
        await _source_actions(case, ["read", "write"])
        catalog = case.app._access.catalog
        sources = PostgresSources(case.api, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
                                  maximum_entries=10, catalog=catalog)
        jobs = PostgresJobs(case.api, sources, audit=True, catalog=catalog)
        assert await jobs.follow_up_ready()
        yield case, jobs


def _request(case, suffix):
    return dict(credential_digest=case.digests["analyst"], workspace_id=case.workspace,
        parent_result_id=case.child.record_id, expected_revision=3,
        request_key=SecretStr("native-follow-up-source-" + suffix), selection_kind="claim",
        selection_id=case.child.view.claims[0].id, question="Which exact source evidence remains uncertain?")


async def _canonical_state(case):
    async with case.owner.connection() as conn:
        scope = (case.workspace, case.initial.project_id)
        revision = await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id=%s "
            "AND project_id=%s", scope)).fetchone()
        pointers = await (await conn.execute("SELECT pointer_name,record_id,revision FROM k05_current_pointers "
            "WHERE workspace_id=%s AND project_id=%s ORDER BY pointer_name", scope)).fetchall()
        records = await (await conn.execute("SELECT record_id,record_type,snapshot_id,parent_result_id FROM k05_records "
            "WHERE workspace_id=%s AND project_id=%s ORDER BY record_id", scope)).fetchall()
        parents = await (await conn.execute("SELECT record_id,relation,parent_id FROM k05_record_parents "
            "WHERE workspace_id=%s AND project_id=%s ORDER BY record_id,relation,parent_id", scope)).fetchall()
        references = await (await conn.execute("SELECT record_id,sha256 FROM k05_object_references "
            "WHERE workspace_id=%s ORDER BY record_id,sha256", (case.workspace,))).fetchall()
        return revision, pointers, records, parents, references


async def _failed_receipt(case, receipt):
    async with case.owner.connection() as conn:
        assert await (await conn.execute("SELECT state,result,canonical_result_id,request_id FROM j01_jobs "
            "WHERE workspace_id=%s AND job_id=%s", (case.workspace, receipt.job_id))).fetchone() == (
                "failed", None, None, receipt.request_id)
        actions = await (await conn.execute("SELECT action,effect_id FROM k06_action_audit "
            "WHERE workspace_id=%s AND job_id=%s ORDER BY action", (case.workspace, receipt.job_id))).fetchall()
        assert actions == [("job.admit", receipt.job_id), ("job.fail", receipt.job_id)]


def test_native_follow_up_source_read_without_write_denies_admission(tmp_path):
    async def scenario():
        async with _jobs(tmp_path) as (case, jobs):
            before = await _canonical_state(case)
            await _source_actions(case, ["read"])
            with pytest.raises(AuthorizationDenied):
                await jobs.enqueue_follow_up(**_request(case, "admission"))
            assert await _canonical_state(case) == before
            async with case.owner.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit "
                    "WHERE action IN ('job.admit','job.publish')")).fetchone() == (0,)
    _run(scenario())


def test_native_follow_up_source_write_revoke_prevents_dispatch_and_retains_failed_receipt(tmp_path):
    async def scenario():
        async with _jobs(tmp_path) as (case, jobs):
            receipt = await jobs.enqueue_follow_up(**_request(case, "dispatch"))
            before = await _canonical_state(case)
            await _source_actions(case, ["read"])
            assert await jobs.claim(lease_digest=SecretStr("3" * 64), lease_seconds=120,
                                    operations=("follow_up",)) is None
            assert await _canonical_state(case) == before
            await _failed_receipt(case, receipt)
    _run(scenario())


@pytest.mark.parametrize("remaining_actions", [["read"], []], ids=["write_only_revoke", "full_revoke"])
def test_native_follow_up_source_revoke_after_stage_blocks_all_canonical_effects(tmp_path, monkeypatch, remaining_actions):
    async def scenario():
        async with _jobs(tmp_path) as (case, jobs):
            arguments = _request(case, "publication")
            receipt = await jobs.enqueue_follow_up(**arguments)
            lease = SecretStr("4" * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=("follow_up",))
            assert claim is not None and claim.job_id == receipt.job_id
            parent = await jobs.follow_up_parent(claim=claim)
            selection = resolve_product_follow_up_selection(parent, selection_kind=arguments["selection_kind"],
                selection_id=arguments["selection_id"], question=arguments["question"])
            child = build_upload_follow_up_result(case.workspace, parent, build_product_follow_up_view(parent, selection))
            before = await _canonical_state(case)
            entered, release = asyncio.Event(), asyncio.Event()
            assert jobs._canonical is not None
            original = jobs._canonical.prepare_result

            async def held(*args, **kwargs):
                prepared = await original(*args, **kwargs)
                entered.set()
                await release.wait()
                return prepared

            monkeypatch.setattr(jobs._canonical, "prepare_result", held)
            pending = asyncio.create_task(jobs.complete_result(claim=claim, lease_digest=lease, record=child))
            try:
                async with asyncio.timeout(12):
                    await entered.wait()
                    # No DB connection is held while canonical bytes are staged.
                    await _source_actions(case, remaining_actions)
                    release.set()
                    with pytest.raises(AuthorizationDenied):
                        await pending
            finally:
                release.set()
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            assert await _canonical_state(case) == before
            async with case.owner.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE workspace_id=%s "
                    "AND record_id=%s", (case.workspace, child.record_id))).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE job_id=%s "
                    "AND action='job.publish'", (receipt.job_id,))).fetchone() == (0,)
            # Real worker failure API preserves terminal truth after revocation; no synthetic SQL terminal state.
            assert await jobs.fail(claim=claim, lease_digest=lease)
            assert not await jobs.fail(claim=claim, lease_digest=lease)
            await _failed_receipt(case, receipt)
    _run(scenario())
