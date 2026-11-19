"""Real PostgreSQL resumable uploads in UUID-owned K-07 schemas only."""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
import sys
import threading
import zipfile
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.persistence_migration import migrate as migrate_core
from aletheia_lab.hosted.postgres_sources import PostgresSources, SourceConflict
from aletheia_lab.hosted.source_bundle import SourceRejected
from aletheia_lab.hosted.transactions import TransactionUnavailable
from aletheia_lab.hosted.upload_staging import UploadManifest, UploadRejected
from aletheia_lab.hosted.upload_staging_migration import UploadMigrationRejected, migrate, verify

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K07_TEST_DATABASE_URL"),
    reason="Explicit K-07 PostgreSQL test URL required")]


def _run(coro):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        return runner.run(coro)


def _digest(user="analyst"):
    return SecretStr(hashlib.sha256(("k07-synthetic-" + user).encode()).hexdigest())


def _bundle():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("dữ_liệu.csv", "id,target\n1,0\n2,1\n")
        archive.writestr("metrics.csv", "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n")
        archive.writestr("config.json", '{"synthetic":true}')
    return output.getvalue()


def _manifest(payload, *, part_size=120, parent_source_id=None):
    parts = [payload[start:start + part_size] for start in range(0, len(payload), part_size)]
    model = UploadManifest.model_validate({"schema_version": "product-api/v1-draft", "source_type": "zip_bundle",
        "byte_size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "parent_source_id": parent_source_id,
        "parts": [{"part_number": index + 1, "byte_size": len(part), "sha256": hashlib.sha256(part).hexdigest()}
                  for index, part in enumerate(parts)]})
    return model, parts


@asynccontextmanager
async def _database():
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool

    schema = "k07_upload_" + uuid4().hex
    database = os.environ["K07_TEST_DATABASE_URL"]
    value = urlsplit(database)
    pairs = [(key, item) for key, item in parse_qsl(value.query) if key != "options"]
    pairs.append(("options", "-csearch_path=" + schema))
    scoped = urlunsplit(value._replace(query=urlencode(pairs)))
    async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            async with AsyncConnectionPool(scoped, min_size=1, max_size=8, open=False) as pool:
                await pool.wait(timeout=15)
                async with pool.connection() as conn:
                    await migrate_core(conn, schema)
                    assert await migrate(conn, schema) == "created"
                    for user in ("analyst", "other", "owner"):
                        await conn.execute("INSERT INTO hosted_identity_bindings(issuer,subject,user_id,display_name,email,email_verified,enabled,account_epoch,onboarding,last_workspace_id,logout_all_at) VALUES('issuer',%s,%s,'Synthetic',%s,"
                            "true,true,0,'ready',NULL,NULL)", (user, user, user + "@example.invalid"))
                        await conn.execute("INSERT INTO j01_sessions(context_id,user_id,csrf_token,state,issued_at,last_seen_at,rotated_at,idle_expires_at,absolute_expires_at,generation,session_epoch) VALUES(%s,%s,'synthetic-csrf','active',statement_timestamp(),"
                            "statement_timestamp(),statement_timestamp(),statement_timestamp()+interval '900 seconds',"
                            "statement_timestamp()+interval '28000 seconds',0,0)", ("ctx_" + user, user))
                        await conn.execute("INSERT INTO j01_session_credentials(digest,context_id,state,generation) VALUES(%s,%s,'active',0)",
                                           (_digest(user).get_secret_value(), "ctx_" + user))
                    await conn.execute("INSERT INTO j01_workspaces(workspace_id,name) VALUES('a','Synthetic A'),('b','Synthetic B')")
                    for user in ("analyst", "other", "owner"):
                        await conn.execute("INSERT INTO j01_memberships(workspace_id,user_id,role,active,revision) VALUES('a',%s,%s,true,0)",
                                           (user, "owner" if user == "owner" else "analyst"))
                    await conn.execute("INSERT INTO j01_memberships(workspace_id,user_id,role,active,revision) VALUES('b','analyst','owner',true,0)")
                yield pool, schema
        finally:
            assert schema.startswith("k07_upload_") and len(schema) == 43
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _sources(pool, **kwargs):
    return PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=100, **kwargs)


async def _create(sources, payload, *, key="synthetic-k07-key-0001", **kwargs):
    manifest, parts = _manifest(payload, **kwargs)
    receipt = await sources.create_upload(credential_digest=_digest(), workspace_id="a", request_key=SecretStr(key), manifest=manifest)
    return receipt, parts


async def _parts(sources, receipt, parts):
    return await asyncio.gather(*(sources.put_upload_part(credential_digest=_digest(), workspace_id="a",
        upload_id=receipt.upload_id, part_number=index + 1, payload=part) for index, part in reversed(list(enumerate(parts)))))


def test_real_resume_out_of_order_parallel_retry_lost_ack_and_complete_once():
    async def run():
        async with _database() as (pool, schema):
            sources = _sources(pool)
            assert await sources.is_upload_ready()
            payload = _bundle()
            created = await asyncio.gather(*(_create(sources, payload) for _ in range(8)))
            receipt, parts = created[0]
            assert all(item[0].upload_id == receipt.upload_id for item in created)
            with pytest.raises(UploadRejected) as error:
                await sources.complete_upload(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id)
            assert error.value.code == "upload_incomplete"
            await _parts(sources, receipt, parts)
            await _parts(sources, receipt, parts)
            # A new repository instance reads committed bytes after a client reload.
            sources = _sources(pool)
            status = await sources.upload_status(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id)
            assert status.received_bytes == len(payload) and not status.missing_parts
            with pytest.raises(UploadRejected) as error:
                await sources.put_upload_part(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id,
                                              part_number=1, payload=b"x" * len(parts[0]))
            assert error.value.code == "upload_part_conflict"
            ready = await asyncio.gather(*(_sources(pool).complete_upload(credential_digest=_digest(), workspace_id="a",
                upload_id=receipt.upload_id) for _ in range(3)))
            assert ready == [ready[0]] * 3 and ready[0].state == "ready"
            assert await sources.cancel_upload(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id) == ready[0]
            assert await sources.read(credential_digest=_digest(), workspace_id="a", source_id=ready[0].source_id) == payload
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_sources")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k07_upload_parts")).fetchone() == (0,)
                assert await verify(conn, schema)
                assert await migrate(conn, schema) == "unchanged"
    _run(run())


def test_real_quota_concurrency_cancel_cleanup_expiry_and_source_preservation():
    async def run():
        async with _database() as (pool, _):
            payload = _bundle()
            sources = _sources(pool, maximum_workspace_bytes=len(payload) * 2,
                               maximum_actor_uploads=2, maximum_workspace_uploads=2)
            results = await asyncio.gather(*(_create(sources, payload, key="synthetic-k07-key-" + str(index).zfill(5))
                                             for index in range(5)), return_exceptions=True)
            accepted = [result for result in results if not isinstance(result, Exception)]
            rejected = [result for result in results if isinstance(result, UploadRejected)]
            assert len(accepted) == 2 and len(rejected) == 3
            assert all(error.code == "upload_quota_exceeded" for error in rejected)
            receipt, parts = accepted[0]
            await _parts(sources, receipt, parts)
            cancelled = await sources.cancel_upload(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id)
            assert cancelled.state == "cancelled"
            assert await sources.cleanup_uploads(grace_seconds=300) == 0
            assert await sources.cleanup_uploads(grace_seconds=0) == 1
            expired, _ = accepted[1]
            async with pool.connection() as conn:
                await conn.execute("UPDATE k07_uploads SET created_at=clock_timestamp()-interval '2 days',"
                    "expires_at=clock_timestamp()-interval '600 seconds' WHERE upload_id=%s", (expired.upload_id,))
            with pytest.raises(UploadRejected) as error:
                await sources.complete_upload(credential_digest=_digest(), workspace_id="a", upload_id=expired.upload_id)
            assert error.value.code == "upload_expired"
            assert await sources.cleanup_uploads() == 1
            receipt, parts = await _create(sources, payload, key="synthetic-ready-key-0001")
            await _parts(sources, receipt, parts)
            ready = await sources.complete_upload(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id)
            assert await sources.cleanup_uploads(grace_seconds=0) == 0
            assert await sources.read(credential_digest=_digest(), workspace_id="a", source_id=ready.source_id) == payload
            with pytest.raises(UploadRejected):
                await _create(sources, payload + b"x" * len(payload), key="synthetic-quota-key-001")
    _run(run())


def test_real_scope_role_parent_grant_revocation_and_manifest_conflict():
    async def run():
        async with _database() as (pool, _):
            sources, payload = _sources(pool), _bundle()
            receipt, parts = await _create(sources, payload)
            for digest, workspace in ((_digest("other"), "a"), (_digest(), "b")):
                with pytest.raises(AuthorizationDenied):
                    await sources.put_upload_part(credential_digest=digest, workspace_id=workspace,
                        upload_id=receipt.upload_id, part_number=1, payload=parts[0])
            changed, _ = _manifest(payload + b"x")
            with pytest.raises(SourceConflict):
                await sources.create_upload(credential_digest=_digest(), workspace_id="a",
                    request_key=SecretStr("synthetic-k07-key-0001"), manifest=changed)
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_memberships SET role='viewer',revision=revision+1 WHERE workspace_id='a' AND user_id='analyst'")
            with pytest.raises(AuthorizationDenied):
                await sources.put_upload_part(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id,
                                              part_number=1, payload=parts[0])
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_memberships SET role='analyst',revision=revision+1 WHERE workspace_id='a' AND user_id='analyst'")
            parent = await sources.put(credential_digest=_digest("owner"), workspace_id="a",
                                       request_key=SecretStr("synthetic-parent-key-01"), payload=payload)
            async with pool.connection() as conn:
                await conn.execute("INSERT INTO j01_source_grants(workspace_id,source_id,user_id,actions,revision) VALUES('a',%s,'analyst',ARRAY['read','write'],0)", (parent.source_id,))
            borrowed, borrowed_parts = await _create(sources, payload, key="synthetic-parent-upload-1", parent_source_id=parent.source_id)
            async with pool.connection() as conn:
                await conn.execute("DELETE FROM j01_source_grants WHERE workspace_id='a' AND source_id=%s", (parent.source_id,))
            with pytest.raises(AuthorizationDenied):
                await sources.put_upload_part(credential_digest=_digest(), workspace_id="a", upload_id=borrowed.upload_id,
                                              part_number=1, payload=borrowed_parts[0])
    _run(run())


def test_real_cancel_during_parser_prevents_source_publication(monkeypatch):
    import aletheia_lab.hosted.postgres_sources as module

    async def run():
        async with _database() as (pool, _):
            sources = _sources(pool)
            receipt, parts = await _create(sources, _bundle())
            await _parts(sources, receipt, parts)
            entered, release = threading.Event(), threading.Event()
            original = module.inspect_source_bundle

            def delayed(*args, **kwargs):
                entered.set()
                assert release.wait(10)
                return original(*args, **kwargs)

            monkeypatch.setattr(module, "inspect_source_bundle", delayed)
            completing = asyncio.create_task(sources.complete_upload(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                assert (await sources.cancel_upload(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id)).state == "cancelled"
            finally:
                release.set()
            with pytest.raises(UploadRejected) as error:
                await completing
            assert error.value.code == "upload_conflict"
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_sources")).fetchone() == (0,)
    _run(run())


def test_real_hash_mismatch_corruption_and_migration_drift_fail_closed():
    async def run():
        async with _database() as (pool, schema):
            sources = _sources(pool)
            receipt, parts = await _create(sources, _bundle())
            await _parts(sources, receipt, parts)
            async with pool.connection() as conn:
                await conn.execute("UPDATE k07_upload_parts SET payload=%s WHERE upload_id=%s AND part_number=1",
                                   (b"x" * len(parts[0]), receipt.upload_id))
            with pytest.raises(UploadRejected) as error:
                await sources.complete_upload(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id)
            assert error.value.code == "upload_integrity_failed"
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_sources")).fetchone() == (0,)
                await conn.execute("ALTER TABLE k07_upload_parts ADD COLUMN unexpected boolean")
            assert not await sources.is_upload_ready()
            with pytest.raises(UploadMigrationRejected):
                async with pool.connection() as conn:
                    await migrate(conn, schema)
    _run(run())


def test_real_invalid_parser_and_total_hash_cancel_sealed_session_for_grace_cleanup():
    async def run():
        async with _database() as (pool, _):
            sources = _sources(pool)
            receipt, parts = await _create(sources, b"this is not a ZIP")
            await _parts(sources, receipt, parts)
            with pytest.raises(SourceRejected):
                await sources.complete_upload(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id)
            assert (await sources.upload_status(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id)).state == "cancelled"
            assert await sources.cleanup_uploads(grace_seconds=300) == 0
            assert await sources.cleanup_uploads(grace_seconds=0) == 1
            manifest, parts = _manifest(_bundle())
            manifest = manifest.model_copy(update={"sha256": "0" * 64})
            receipt = await sources.create_upload(credential_digest=_digest(), workspace_id="a",
                request_key=SecretStr("synthetic-hash-key-0001"), manifest=manifest)
            await _parts(sources, receipt, parts)
            with pytest.raises(UploadRejected) as error:
                await sources.complete_upload(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id)
            assert error.value.code == "upload_integrity_failed"
            assert (await sources.upload_status(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id)).state == "cancelled"
    _run(run())


def test_real_database_diskfull_rolls_back_part_and_complete_then_retry_preserves_bytes():
    async def run():
        async with _database() as (pool, _):
            class FaultPool:
                def __init__(self, prefix):
                    self.prefix, self.armed = prefix, True

                @asynccontextmanager
                async def connection(self):
                    async with pool.connection() as conn:
                        outer = self

                        class Connection:
                            autocommit = False

                            async def execute(self, query, params=(), **kwargs):
                                if outer.armed and outer.prefix in query:
                                    outer.armed = False
                                    return await conn.execute("DO $$ BEGIN RAISE EXCEPTION 'synthetic capacity fault' USING ERRCODE='53100'; END $$")
                                return await conn.execute(query, params, **kwargs)

                        yield Connection()

            sources, payload = _sources(pool), _bundle()
            receipt, parts = await _create(sources, payload)
            faulty_parts = _sources(FaultPool("INSERT INTO k07_upload_parts"))
            with pytest.raises(TransactionUnavailable):
                await faulty_parts.put_upload_part(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id,
                                                   part_number=1, payload=parts[0])
            assert (await sources.upload_status(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id)).received_bytes == 0
            await _parts(sources, receipt, parts)
            faulty_complete = _sources(FaultPool("INSERT INTO j01_sources"))
            with pytest.raises(TransactionUnavailable):
                await faulty_complete.complete_upload(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id)
            status = await sources.upload_status(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id)
            assert status.state == "active" and status.received_bytes == len(payload)
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_sources")).fetchone() == (0,)
            ready = await sources.complete_upload(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id)
            assert await sources.read(credential_digest=_digest(), workspace_id="a", source_id=ready.source_id) == payload
    _run(run())
