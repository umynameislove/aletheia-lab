"""Canonical upload snapshots remain authoritative over their JSONB projections."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from pydantic import SecretStr
from test_k05_native_results_postgres import _case, _initial, _run

from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.object_store import ObjectIntegrityError
from aletheia_lab.hosted.postgres_catalog import CatalogIntegrityError, PostgresCatalog
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.upload_snapshot import build_upload_snapshot

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K06_TEST_DATABASE_URL"),
    reason="Explicit K-06 PostgreSQL test URL required")]


class CountedPool:
    """Instrumentation around real connections; every SQL statement still hits PG."""
    def __init__(self, pool):
        self.pool, self.active, self.maximum = pool, 0, 0
        self.before_checkout = None

    @asynccontextmanager
    async def connection(self):
        if self.before_checkout is not None:
            await self.before_checkout()
        async with self.pool.connection() as conn:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            try:
                yield conn
            finally:
                self.active -= 1


def _sources(case, *, pool=None, objects=None, linked=True):
    pool = case.pool if pool is None else pool
    objects = case.objects if objects is None else objects
    catalog = PostgresCatalog(pool, objects) if linked else None
    return PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
                           maximum_entries=10, catalog=catalog)


async def _projection(case, *, canonical):
    async with case.pool.connection() as conn:
        await conn.execute("INSERT INTO j01_upload_snapshots(snapshot_id,workspace_id,source_id,payload,canonical_record_id) "
            "VALUES(%s,'a',%s,%s::jsonb,%s)", (case.snapshot.snapshot_id, case.snapshot.source_id,
            case.snapshot.model_dump_json(), case.snapshot.snapshot_id if canonical else None))


async def _snapshot(sources, case):
    return await sources.snapshot(credential_digest=case.a, workspace_id="a", snapshot_id=case.snapshot.snapshot_id)


async def _persisted(case):
    async with case.pool.connection() as conn:
        return await (await conn.execute("SELECT (SELECT count(*) FROM k05_records),(SELECT count(*) FROM k05_current_pointers),"
            "(SELECT count(*) FROM k06_action_audit),(SELECT count(*) FROM j01_jobs WHERE state='succeeded')")).fetchone()


def test_linked_snapshot_requires_canonical_reader_and_exact_first_capture_projection(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            await _initial(case)
            await _projection(case, canonical=True)
            sources = _sources(case)
            assert await _snapshot(sources, case) == case.snapshot
            with pytest.raises(TimeoutError):
                await _snapshot(_sources(case, linked=False), case)
            later = build_upload_snapshot(workspace_id="a", source_id=case.snapshot.source_id,
                source_sha256=case.snapshot.source_sha256, mapping=case.snapshot.mapping,
                mapping_result=case.snapshot.mapping_result, captured_at=case.snapshot.captured_at + timedelta(seconds=5))
            async with case.pool.connection() as conn:
                await conn.execute("UPDATE j01_upload_snapshots SET payload=%s::jsonb", (later.model_dump_json(),))
            with pytest.raises(CatalogIntegrityError):
                await _snapshot(sources, case)
            assert await case.catalog.read_record(credential_digest=case.a, workspace_id="a",
                project_id=case.snapshot.mapping.project_id, record_id=case.snapshot.snapshot_id) == case.snapshot
    _run(scenario())


@pytest.mark.parametrize("damage", ["corrupt", "delete"])
def test_canonical_file_damage_denies_snapshot_and_initial_job_effect_with_intact_projection(tmp_path, damage):
    async def scenario():
        async with _case(tmp_path) as case:
            await _initial(case)
            await _projection(case, canonical=True)
            async with case.pool.connection() as conn:
                schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
                await migrate_audit(conn, schema)
                sha = (await (await conn.execute("SELECT object_sha256 FROM k05_records WHERE record_id=%s",
                                                 (case.snapshot.snapshot_id,))).fetchone())[0]
            sources = _sources(case)
            jobs = PostgresJobs(case.pool, sources, audit=True, catalog=case.catalog)
            receipt = await jobs.enqueue(credential_digest=case.a, workspace_id="a", operation="analyze",
                request_key=SecretStr("synthetic-authority-analysis-key"), request={"snapshot_id": case.snapshot.snapshot_id,
                "question": case.view.conversation.turns[0].question})
            lease = SecretStr("c" * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=("analyze",))
            assert claim is not None and claim.job_id == receipt.job_id
            before = await _persisted(case)
            target = case.objects.store.path(sha)
            if damage == "corrupt":
                target.write_bytes(b"synthetic canonical corruption")
            else:
                target.unlink()
            with pytest.raises(ObjectIntegrityError):
                await _snapshot(sources, case)
            with pytest.raises(ObjectIntegrityError):
                await jobs.complete(claim=claim, lease_digest=lease, result=case.view.model_dump(mode="json"))
            assert await _persisted(case) == before
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT state,result FROM j01_jobs WHERE job_id=%s",
                    (receipt.job_id,))).fetchone() == ("running", None)
                assert await (await conn.execute("SELECT payload=%s::jsonb FROM j01_upload_snapshots",
                    (case.snapshot.model_dump_json(),))).fetchone() == (True,)
    _run(scenario())


def test_true_legacy_null_snapshot_parses_after_release_and_rechecks_current_authority(tmp_path, monkeypatch):
    async def scenario():
        async with _case(tmp_path) as case:
            await _projection(case, canonical=False)
            counted = CountedPool(case.pool)
            sources = _sources(case, pool=counted, linked=False)
            from aletheia_lab.hosted.upload_snapshot import parse_upload_snapshot
            original = parse_upload_snapshot
            decoded = []

            def decode(value):
                assert counted.active == 0
                decoded.append(True)
                return original(value)

            monkeypatch.setattr("aletheia_lab.hosted.postgres_sources.parse_upload_snapshot", decode)
            assert await _snapshot(sources, case) == case.snapshot
            assert decoded and counted.maximum == 1 and counted.active == 0
            prior = len(decoded)

            async def revoke_after_decode():
                if len(decoded) > prior:
                    counted.before_checkout = None
                    async with case.pool.connection() as conn:
                        await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 "
                            "WHERE workspace_id='a' AND user_id='owner_a'")

            counted.before_checkout = revoke_after_decode
            with pytest.raises(AuthorizationDenied):
                await _snapshot(sources, case)
            assert counted.active == 0
    _run(scenario())


@pytest.mark.parametrize("configured_catalog", [False, True])
def test_null_link_cannot_shadow_existing_corrupt_canonical_snapshot(tmp_path, configured_catalog):
    async def scenario():
        async with _case(tmp_path) as case:
            await _initial(case)
            await _projection(case, canonical=False)
            async with case.pool.connection() as conn:
                sha = (await (await conn.execute("SELECT object_sha256 FROM k05_records WHERE record_id=%s",
                                                 (case.snapshot.snapshot_id,))).fetchone())[0]
            case.objects.store.path(sha).write_bytes(b"synthetic canonical corruption")
            with pytest.raises(CatalogIntegrityError):
                await _snapshot(_sources(case, linked=configured_catalog), case)
    _run(scenario())


def test_revoke_after_object_read_commits_without_waiting_for_snapshot_delivery(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            await _initial(case)
            await _projection(case, canonical=True)
            entered, release = asyncio.Event(), asyncio.Event()
            counted = CountedPool(case.pool)

            class BarrierObjects(PostgresObjects):
                async def read(self, *args, **kwargs):
                    payload = await super().read(*args, **kwargs)
                    assert counted.active == 0
                    entered.set()
                    await release.wait()
                    return payload

            objects = BarrierObjects(counted, case.objects.store)
            sources = _sources(case, pool=counted, objects=objects)
            task = asyncio.create_task(_snapshot(sources, case))
            await asyncio.wait_for(entered.wait(), 5)

            async def revoke():
                async with case.pool.connection() as conn:
                    await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 "
                        "WHERE workspace_id='a' AND user_id='owner_a'")

            await asyncio.wait_for(revoke(), 2)
            assert counted.active == 0
            release.set()
            with pytest.raises(AuthorizationDenied):
                await asyncio.wait_for(task, 5)
            assert counted.active == 0
    _run(scenario())
