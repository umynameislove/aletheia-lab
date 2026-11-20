"""Canonical logical deletion under current policy, CAS, publication and retention."""
from __future__ import annotations

import asyncio
import hashlib
import os

import pytest
from pydantic import SecretStr
from test_k04_project_policy_postgres import _credential, _database, _run
from test_k04_reviews_postgres import _setup

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.legacy_import import ImportTarget, LegacyImportConflict, LegacyImporter
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
from aletheia_lab.hosted.workspace_management import WorkspaceConflict

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit K-04 PostgreSQL test URL required")]


async def _counts(pool):
    async with pool.connection() as conn:
        values = []
        for table in ("k05_records", "k05_objects", "k05_object_references", "k05_object_leases"):
            values.append((await (await conn.execute("SELECT count(*) FROM " + table)).fetchone())[0])
        return tuple(values)


async def _receipt(pool, workspace):
    payload = b"synthetic retained source"
    async with pool.connection() as conn:
        await conn.execute("INSERT INTO j01_sources(source_id,workspace_id,creator_id,request_key_hash,sha256,payload,manifest) "
            "VALUES('retained-source',%s,'analyst','synthetic-retained-key',%s,%s,'[]')",
            (workspace, hashlib.sha256(payload).hexdigest(), payload))
    sources = PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)
    return await PostgresJobs(pool, sources).enqueue(credential_digest=_credential("analyst"), workspace_id=workspace,
        operation="preview", request_key=SecretStr("synthetic-retained-job"), request={"source_id": "retained-source"})


def test_owner_tombstone_retains_history_objects_receipt_and_other_tenant(tmp_path):
    async def scenario():
        async with _database() as (pool, schema):
            _, _, catalog, project, result, source = await _setup(pool, schema, tmp_path)
            importer = LegacyImporter(pool, catalog.objects)
            await importer.import_project(source, ImportTarget(project, "wb", "other"),
                                          credential_digest=_credential("other"), batch_id="second-tenant")
            receipt = await _receipt(pool, "wa")
            before = await _counts(pool)
            files = {file.relative_to(catalog.objects.store.root): file.read_bytes()
                     for file in catalog.objects.store.object_root.rglob("*") if file.is_file()}
            mutations = PostgresProjectMutations(pool, catalog)
            for user in ("viewer", "analyst", "other"):
                with pytest.raises(AuthorizationDenied):
                    await mutations.delete(credential_digest=_credential(user), workspace_id="wa", project_id=project,
                                           expected_revision=0)
            value = await mutations.delete(credential_digest=_credential("owner"), workspace_id="wa", project_id=project,
                                           expected_revision=0)
            assert value["status"] == "tombstoned" and value["retention"] == "retained" and value["revision"] == 1
            after = await _counts(pool)
            assert after == (before[0] + 1, before[1] + 1, before[2], before[3])
            assert all((catalog.objects.store.root / path).read_bytes() == payload for path, payload in files.items())
            with pytest.raises(AuthorizationDenied):
                await catalog.read_record(credential_digest=_credential("owner"), workspace_id="wa", project_id=project, record_id=result)
            projects = await catalog.list_projects(credential_digest=_credential("owner"), workspace_id="wa")
            assert project not in {row[0] for row in projects}
            assert await catalog.read_record(credential_digest=_credential("other"), workspace_id="wb", project_id=project, record_id=result)
            with pytest.raises(LegacyImportConflict):
                await importer.import_project(source, ImportTarget(project, "wa", "analyst"),
                                              credential_digest=_credential("analyst"), batch_id="older-backup")
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT state,request_id FROM j01_jobs WHERE job_id=%s", (receipt.job_id,))).fetchone() == (
                    "queued", receipt.request_id)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE workspace_id='wa' AND record_type<>'deletion'")).fetchone() == (
                    len(source.models),)
    _run(scenario())


@pytest.mark.parametrize("actor", ["admin", "analyst"])
def test_admin_or_explicit_analyst_can_delete_and_stale_revision_has_no_effect(tmp_path, actor):
    async def scenario():
        async with _database() as (pool, schema):
            _, manager, catalog, project, _, _ = await _setup(pool, schema, tmp_path)
            if actor == "analyst":
                await manager.set_grant(credential_digest=_credential("owner"), workspace_id="wa", project_id=project,
                                       user_id="analyst", actions=["read", "delete"], expected_revision=0)
            mutations = PostgresProjectMutations(pool, catalog)
            before = await _counts(pool)
            with pytest.raises(WorkspaceConflict):
                await mutations.delete(credential_digest=_credential(actor), workspace_id="wa", project_id=project,
                                       expected_revision=1)
            assert await _counts(pool) == before
            assert (await mutations.delete(credential_digest=_credential(actor), workspace_id="wa", project_id=project,
                                           expected_revision=0))["revision"] == 1
    _run(scenario())


def test_revoke_at_staged_publication_denies_tombstone_without_metadata_effects(tmp_path):
    async def scenario():
        async with _database() as (pool, schema):
            _, manager, catalog, project, result, _ = await _setup(pool, schema, tmp_path)
            await manager.set_grant(credential_digest=_credential("owner"), workspace_id="wa", project_id=project,
                                   user_id="analyst", actions=["read", "delete"], expected_revision=0)
            before = await _counts(pool)
            entered, release = asyncio.Event(), asyncio.Event()

            class BarrierObjects:
                async def stage(self, *args, **kwargs):
                    value = await catalog.objects.stage(*args, **kwargs)
                    entered.set()
                    await release.wait()
                    return value

            class BarrierCatalog:
                objects = BarrierObjects()

            task = asyncio.create_task(PostgresProjectMutations(pool, BarrierCatalog()).delete(
                credential_digest=_credential("analyst"), workspace_id="wa", project_id=project, expected_revision=0))
            await asyncio.wait_for(entered.wait(), 10)
            await manager.set_grant(credential_digest=_credential("owner"), workspace_id="wa", project_id=project,
                                   user_id="analyst", actions=[], expected_revision=1)
            release.set()
            with pytest.raises(AuthorizationDenied):
                await task
            after = await _counts(pool)
            assert after[:3] == before[:3]
            assert await catalog.read_record(credential_digest=_credential("owner"), workspace_id="wa", project_id=project, record_id=result)
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id='wa' AND project_id=%s", (project,))).fetchone() == (0,)
    _run(scenario())


def test_identical_original_cas_deletes_replay_one_tombstone_without_project_lock_deadlock(tmp_path):
    async def scenario():
        async with _database() as (pool, schema):
            _, _, catalog, project, _, _ = await _setup(pool, schema, tmp_path)
            before = await _counts(pool)
            entered, release, count = asyncio.Event(), asyncio.Event(), 0

            class BarrierObjects:
                async def stage(self, *args, **kwargs):
                    nonlocal count
                    value = await catalog.objects.stage(*args, **kwargs)
                    count += 1
                    if count == 2:
                        entered.set()
                    await release.wait()
                    return value

                async def ready(self, *args, **kwargs):
                    return await catalog.objects.ready(*args, **kwargs)

                async def finish(self, *args, **kwargs):
                    return await catalog.objects.finish(*args, **kwargs)

            class BarrierCatalog:
                objects = BarrierObjects()

            mutations = PostgresProjectMutations(pool, BarrierCatalog())

            async def delete(actor):
                return await mutations.delete(credential_digest=_credential(actor), workspace_id="wa", project_id=project,
                                              expected_revision=0)

            first, second = asyncio.create_task(delete("owner")), asyncio.create_task(delete("admin"))
            await asyncio.wait_for(entered.wait(), 10)
            release.set()
            outcomes = await asyncio.wait_for(asyncio.gather(first, second), 10)
            assert outcomes[0] == outcomes[1]
            assert outcomes[0]["revision"] == 1 and outcomes[0]["status"] == "tombstoned"
            after = await _counts(pool)
            assert after == (before[0] + 1, before[1] + 1, before[2], before[3])
            async with pool.connection() as conn:
                saved = await (await conn.execute("SELECT revision,deletion_revision,deletion_tombstone_id,deleted_by "
                    "FROM k05_projects WHERE workspace_id='wa' AND project_id=%s", (project,))).fetchone()
                assert saved[:3] == (1, 1, outcomes[0]["tombstone_id"]) and saved[3] in ("owner", "admin")
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE workspace_id='wa' "
                    "AND project_id=%s AND record_type='deletion'", (project,))).fetchone() == (1,)
    _run(scenario())
