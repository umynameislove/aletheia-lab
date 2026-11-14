"""K-05 object/metadata faults on a real database and owned local filesystem."""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest

from aletheia_lab.hosted.object_store import FilesystemObjectStore, ObjectIntegrityError
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.project.persistence import _json_bytes

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K05_TEST_DATABASE_URL"), reason="Explicit K-05 PostgreSQL test URL required")]


def _run(coro):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        return runner.run(coro)


@asynccontextmanager
async def _database():
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool

    from aletheia_lab.hosted.persistence_migration import migrate

    schema = "k05_objects_" + uuid4().hex
    database = os.environ["K05_TEST_DATABASE_URL"]
    parsed = urlsplit(database)
    assert parsed.scheme == "postgresql"
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "options"]
    scoped = urlunsplit(parsed._replace(query=urlencode(query + [("options", "-csearch_path=" + schema)])))
    async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            async with AsyncConnectionPool(scoped, min_size=1, max_size=4, open=False) as pool:
                await pool.wait(timeout=10)
                async with pool.connection() as conn:
                    await migrate(conn, schema)
                    for workspace in ("a", "b"):
                        await conn.execute("INSERT INTO hosted_identity_bindings "
                            "(issuer,subject,user_id,display_name,email,email_verified,enabled,account_epoch,onboarding) "
                            "VALUES ('https://issuer.example.invalid',%s,%s,'Synthetic',%s,true,true,0,'needs_workspace')",
                            ("subject-" + workspace, "owner-" + workspace, workspace + "@example.invalid"))
                        await conn.execute("INSERT INTO j01_workspaces(workspace_id,name) VALUES (%s,'Synthetic')", (workspace,))
                        await conn.execute("INSERT INTO j01_memberships(workspace_id,user_id,role,active,revision) "
                            "VALUES (%s,%s,'owner',true,1)", (workspace, "owner-" + workspace))
                yield pool
        finally:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def _attach(pool, objects, workspace, staged):
    descriptor = staged.descriptor
    record_id = "p6-preview-" + descriptor.sha256
    async with pool.connection() as conn:
        await objects.ready(conn, workspace, staged)
        await conn.execute(
            "INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,"
            "canonical_sha256,object_sha256) VALUES (%s,%s,NULL,'preview','synthetic/v1',%s,%s) "
            "ON CONFLICT (workspace_id,record_id) DO NOTHING",
            (workspace, record_id, descriptor.sha256, descriptor.sha256),
        )
        await objects.finish(conn, workspace, staged)
    return record_id


def _old(store, descriptor):
    os.utime(store.path(descriptor.sha256), (time.time() - 600, time.time() - 600))


def test_real_metadata_rollback_lease_expiry_gc_and_retry(tmp_path):
    async def run():
        async with _database() as pool:
            store = FilesystemObjectStore(tmp_path)
            objects = PostgresObjects(pool, store)
            staged = await objects.stage("a", b"synthetic rollback", media_type="application/octet-stream")
            with pytest.raises(RuntimeError, match="rollback fault"):
                async with pool.connection() as conn:
                    await objects.ready(conn, "a", staged)
                    raise RuntimeError("rollback fault")
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_objects")).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k05_records")).fetchone() == (0,)
            _old(store, staged.descriptor)
            assert (await objects.reconcile(grace_seconds=60)).retained == 1
            async with pool.connection() as conn:
                await conn.execute("UPDATE k05_object_leases SET expires_at=clock_timestamp()-interval '1 second'")
            assert (await objects.reconcile(grace_seconds=60)).removed == 1
            with pytest.raises(ObjectIntegrityError, match="lease expired"):
                async with pool.connection() as conn:
                    await objects.ready(conn, "a", staged)
            retry = await objects.stage("a", b"synthetic rollback", media_type="application/octet-stream")
            await _attach(pool, objects, "a", retry)
            assert await objects.read("a", retry.descriptor) == b"synthetic rollback"
    _run(run())


def test_real_duplicate_shared_references_and_active_reader_lease(tmp_path, monkeypatch):
    async def run():
        async with _database() as pool:
            store = FilesystemObjectStore(tmp_path)
            objects = PostgresObjects(pool, store)
            stages = await asyncio.gather(*[
                objects.stage(workspace, b"synthetic shared", media_type="application/octet-stream")
                for workspace in ("a", "a", "b")])
            assert len({value.descriptor.sha256 for value in stages}) == 1
            for workspace, staged in zip(("a", "a", "b"), stages, strict=True):
                await _attach(pool, objects, workspace, staged)
            _old(store, stages[0].descriptor)
            async with pool.connection() as conn:
                await conn.execute("DELETE FROM k05_records WHERE workspace_id='a'")
            assert (await objects.reconcile(grace_seconds=60)).removed == 0
            started, release = threading.Event(), threading.Event()
            original = store.read

            def paused_read(descriptor):
                started.set()
                if not release.wait(10):
                    raise RuntimeError("Controlled read barrier timed out")
                return original(descriptor)

            monkeypatch.setattr(store, "read", paused_read)
            reading = asyncio.create_task(objects.read("b", stages[0].descriptor))
            try:
                assert await asyncio.to_thread(started.wait, 10)
                async with pool.connection() as conn:
                    await conn.execute("DELETE FROM k05_records WHERE workspace_id='b'")
                assert (await objects.reconcile(grace_seconds=60)).retained == 1
            finally:
                release.set()
            assert await reading == b"synthetic shared"
            monkeypatch.setattr(store, "read", original)
            assert (await objects.reconcile(grace_seconds=60)).removed == 1
            assert not store.path(stages[0].descriptor.sha256).exists()
    _run(run())


def test_real_staging_lease_protects_write_without_holding_connection(tmp_path, monkeypatch):
    async def run():
        async with _database() as pool:
            store = FilesystemObjectStore(tmp_path)
            objects = PostgresObjects(pool, store)
            payload = b"synthetic in-flight publication"
            old = store.publish(payload, media_type="application/octet-stream")
            _old(store, old)
            started, release = threading.Event(), threading.Event()
            original = store.publish

            def paused_publish(*args, **kwargs):
                started.set()
                if not release.wait(10):
                    raise RuntimeError("Controlled publish barrier timed out")
                return original(*args, **kwargs)

            monkeypatch.setattr(store, "publish", paused_publish)
            publishing = asyncio.create_task(objects.stage("a", payload, media_type="application/octet-stream"))
            try:
                assert await asyncio.to_thread(started.wait, 10)
                async with pool.connection() as conn:
                    assert await (await conn.execute("SELECT count(*) FROM k05_object_leases WHERE expires_at>clock_timestamp()")).fetchone() == (1,)
                assert (await objects.reconcile(grace_seconds=60)).retained == 1
            finally:
                release.set()
            staged = await publishing
            await _attach(pool, objects, "a", staged)
            assert await objects.read("a", staged.descriptor) == payload
    _run(run())


def test_real_scoped_scrub_finds_corruption_without_serving_it(tmp_path):
    async def run():
        async with _database() as pool:
            store = FilesystemObjectStore(tmp_path)
            objects = PostgresObjects(pool, store)
            values = []
            for workspace in ("a", "b"):
                payload = _json_bytes({"schema_version": "synthetic/v1", "workspace": workspace})
                staged = await objects.stage(workspace, payload, media_type="application/json", schema_version="synthetic/v1")
                await _attach(pool, objects, workspace, staged)
                values.append(staged.descriptor)
            store.path(values[1].sha256).write_bytes(b"corrupt")
            assert not (await objects.scrub("a")).corrupt_sha256
            assert (await objects.scrub("b")).corrupt_sha256 == (values[1].sha256,)
            with pytest.raises(ObjectIntegrityError):
                await objects.read("b", values[1])
            assert hashlib.sha256(await objects.read("a", values[0])).hexdigest() == values[0].sha256
    _run(run())



def test_real_cancelled_reader_keeps_lease_until_safe_recovery(tmp_path, monkeypatch):
    async def run():
        async with _database() as pool:
            store = FilesystemObjectStore(tmp_path)
            objects = PostgresObjects(pool, store)
            staged = await objects.stage("a", b"synthetic cancelled reader", media_type="application/octet-stream")
            await _attach(pool, objects, "a", staged)
            _old(store, staged.descriptor)
            started, release, finished = threading.Event(), threading.Event(), threading.Event()
            original = store.read

            def paused_read(descriptor):
                started.set()
                try:
                    if not release.wait(10):
                        raise RuntimeError("Controlled cancel barrier timed out")
                    return original(descriptor)
                finally:
                    finished.set()

            monkeypatch.setattr(store, "read", paused_read)
            reading = asyncio.create_task(objects.read("a", staged.descriptor))
            try:
                assert await asyncio.to_thread(started.wait, 10)
                reading.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await reading
                async with pool.connection() as conn:
                    await conn.execute("DELETE FROM k05_records WHERE workspace_id='a'")
                    assert await (await conn.execute("SELECT count(*) FROM k05_object_leases WHERE expires_at>clock_timestamp()")).fetchone() == (1,)
                assert (await objects.reconcile(grace_seconds=60)).retained == 1
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 10)
            async with pool.connection() as conn:
                await conn.execute("UPDATE k05_object_leases SET expires_at=clock_timestamp()-interval '1 second'")
            assert (await objects.reconcile(grace_seconds=60)).removed == 1
    _run(run())


def test_real_out_of_band_corruption_after_verified_stage_fails_closed(tmp_path):
    async def run():
        async with _database() as pool:
            store = FilesystemObjectStore(tmp_path)
            objects = PostgresObjects(pool, store)
            staged = await objects.stage("a", b"synthetic external mutation", media_type="application/octet-stream")
            store.path(staged.descriptor.sha256).write_bytes(b"externally corrupt")
            await _attach(pool, objects, "a", staged)
            with pytest.raises(ObjectIntegrityError):
                await objects.read("a", staged.descriptor)
            assert (await objects.scrub("a")).corrupt_sha256 == (staged.descriptor.sha256,)
    _run(run())



def test_real_stale_stage_and_no_file_lease_are_bounded_cleanup(tmp_path):
    async def run():
        async with _database() as pool:
            store = FilesystemObjectStore(tmp_path)
            objects = PostgresObjects(pool, store)
            staged = await objects.stage("a", b"synthetic referenced object", media_type="application/octet-stream")
            await _attach(pool, objects, "a", staged)
            final = store.path(staged.descriptor.sha256)
            abandoned = final.with_name("." + final.name + ".synthetic.stage")
            abandoned.write_bytes(b"abandoned partial")
            os.utime(abandoned, (time.time() - 600, time.time() - 600))
            async with pool.connection() as conn:
                await conn.execute("INSERT INTO k05_object_leases(workspace_id,sha256,lease_id,expires_at) "
                    "VALUES ('a',%s,'synthetic-no-file',clock_timestamp()-interval '1 second')", ("0" * 64,))
            report = await objects.reconcile(grace_seconds=60)
            assert report.removed == 1 and report.expired_leases_removed == 1
            assert not abandoned.exists() and final.exists()
            assert await objects.read("a", staged.descriptor) == b"synthetic referenced object"
    _run(run())



def test_real_unlink_then_metadata_rollback_recovers_in_bounded_pages(tmp_path):
    async def run():
        async with _database() as pool:
            store = FilesystemObjectStore(tmp_path)
            objects = PostgresObjects(pool, store)
            orphan_digests = []
            for index in range(3):
                staged = await objects.stage("a", f"synthetic orphan {index}".encode(), media_type="application/octet-stream")
                async with pool.connection() as conn:
                    await objects.ready(conn, "a", staged)
                    await objects.finish(conn, "a", staged)
                    await conn.execute("UPDATE k05_objects SET ready_at=clock_timestamp()-interval '600 seconds' WHERE sha256=%s",
                                       (staged.descriptor.sha256,))
                with pytest.raises(RuntimeError, match="metadata commit fault"):
                    async with pool.connection() as conn:
                        await objects.lock(conn, staged.descriptor.sha256)
                        await conn.execute("DELETE FROM k05_objects WHERE sha256=%s", (staged.descriptor.sha256,))
                        store.path(staged.descriptor.sha256).unlink()
                        raise RuntimeError("metadata commit fault")
                orphan_digests.append(staged.descriptor.sha256)
            referenced = await objects.stage("b", b"synthetic missing referenced", media_type="application/octet-stream")
            await _attach(pool, objects, "b", referenced)
            store.path(referenced.descriptor.sha256).unlink()
            async with pool.connection() as conn:
                await conn.execute("UPDATE k05_objects SET ready_at=clock_timestamp()-interval '600 seconds'")
            total, cursor, pages = 0, "", 0
            while True:
                report = await objects.reconcile(grace_seconds=60, limit=1, metadata_after=cursor)
                total += report.orphan_metadata_removed
                pages += 1
                assert pages <= 5
                if report.metadata_next_cursor is None:
                    break
                cursor = report.metadata_next_cursor
            assert total == 3 and pages == 5
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_objects")).fetchone() == (1,)
            assert (await objects.scrub("b")).corrupt_sha256 == (referenced.descriptor.sha256,)
            assert not store.candidates()
    _run(run())



@pytest.mark.parametrize("known_first", [True, False])
def test_real_same_bytes_schema_hint_is_compatible_with_artifact_use(tmp_path, known_first):
    async def run():
        async with _database() as pool:
            store = FilesystemObjectStore(tmp_path)
            objects = PostgresObjects(pool, store)
            payload = _json_bytes({"schema_version": "synthetic/v1", "value": "same bytes"})
            schemas = ("synthetic/v1", None) if known_first else (None, "synthetic/v1")
            descriptors = []
            for schema in schemas:
                staged = await objects.stage("a", payload, media_type="application/json", schema_version=schema)
                await _attach(pool, objects, "a", staged)
                descriptors.append(staged.descriptor)
            for descriptor in descriptors:
                assert await objects.read("a", descriptor) == payload
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_objects")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k05_object_leases")).fetchone() == (0,)
            with pytest.raises(ObjectIntegrityError):
                from dataclasses import replace

                await objects.read("a", replace(descriptors[0], schema_version="synthetic/v2"))
    _run(run())



def test_real_digest_gate_is_catalog_scoped_and_shared_by_its_connections():
    async def run():
        import psycopg

        async with _database() as first_pool, _database() as second_pool:
            digest = "1" * 64
            async with first_pool.connection() as holder:
                await PostgresObjects.lock(holder, digest)
                async with second_pool.connection() as unrelated:
                    await unrelated.execute("SET LOCAL lock_timeout='300ms'")
                    await PostgresObjects.lock(unrelated, digest)
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    async with first_pool.connection() as same_catalog:
                        await same_catalog.execute("SET LOCAL lock_timeout='100ms'")
                        await PostgresObjects.lock(same_catalog, digest)
            async with first_pool.connection() as after_release:
                await after_release.execute("SET LOCAL lock_timeout='300ms'")
                await PostgresObjects.lock(after_release, digest)
    _run(run())
