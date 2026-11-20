"""Real PostgreSQL import, rollback/resume and consumer parity on owned schemas."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import SecretStr
from test_k05_migrations_postgres import _database, _run

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.legacy_import import (
    ImportTarget,
    LegacyImportConflict,
    LegacyImporter,
    LegacyImportError,
    LegacyProject,
    LegacySource,
    model_metadata,
)
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.persistence_migration import migrate
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.project_policy_migration import migrate as migrate_project_policy
from aletheia_lab.product.lifecycle import ProductLifecycleStore, build_product_lifecycle_record
from aletheia_lab.product.results import _view_from_record
from aletheia_lab.project.persistence import _json_bytes

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from test_k05_legacy_import import _cli, _files, legacy_journey  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K05_TEST_DATABASE_URL"), reason="Explicit K-05 test database URL required")]


async def _identities(pool):
    credentials = []
    async with pool.connection() as conn:
        for scope in ("a", "b"):
            user = "owner_" + scope
            digest = hashlib.sha256(("synthetic-k05-session-" + scope).encode()).hexdigest()
            await conn.execute("INSERT INTO hosted_identity_bindings(issuer,subject,user_id,display_name,email,email_verified,enabled,account_epoch,onboarding) VALUES('https://issuer.example.invalid',%s,%s,'Synthetic',%s,true,true,0,'ready')", (scope, user, scope + "@example.invalid"))
            await conn.execute("INSERT INTO j01_workspaces VALUES(%s,'Synthetic K05')", (scope,))
            await conn.execute("INSERT INTO j01_memberships VALUES(%s,%s,'owner',true,1)", (scope, user))
            await conn.execute("INSERT INTO j01_sessions VALUES(%s,%s,'synthetic-csrf','active',statement_timestamp()-interval '60 seconds',statement_timestamp()-interval '30 seconds',statement_timestamp()-interval '30 seconds',statement_timestamp()+interval '900 seconds',statement_timestamp()+interval '28000 seconds',0,0)", ("ctx_" + scope, user))
            await conn.execute("INSERT INTO j01_session_credentials VALUES(%s,%s,'active',0)", (digest, "ctx_" + scope))
            credentials.append(SecretStr(digest))
        await conn.execute("INSERT INTO j01_memberships VALUES('a','owner_b','owner',true,1)")
    return credentials


def _source(tmp_path):
    root, project_id, view, followed = legacy_journey(tmp_path)
    before = _files(root)
    with LegacySource(root) as legacy:
        source = legacy.export_project(project_id)
    assert _files(root) == before
    return source, view, followed


def test_real_import_preserves_full_history_pointers_and_typed_consumer(tmp_path, monkeypatch):
    source, original, followed = _source(tmp_path)

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
                await migrate_project_policy(conn, schema)
            a, b = await _identities(pool)
            objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "objects"))
            importer = LegacyImporter(pool, objects)
            target = ImportTarget(source.project_id, "a", "owner_a")
            report = await importer.import_project(source, target, credential_digest=a, batch_id="parity")
            assert report.disposition == "created" and report.source_fingerprint == source.fingerprint
            catalog = PostgresCatalog(pool, objects)
            for model in source.models:
                kind, record_id, project_id, _, _ = model_metadata(model)
                if project_id is None:
                    continue
                restored = await catalog.read_record(credential_digest=a, workspace_id="a", project_id=source.project_id, record_id=record_id)
                assert restored == model
                if kind == "result":
                    expected = original if record_id == original["result"]["id"] else followed
                    assert _view_from_record(restored).model_dump(mode="json") == expected
            current = await catalog.current(credential_digest=a, workspace_id="a", project_id=source.project_id)
            assert current.record_id == source.current_pointers[0][1]
            page = await catalog.list_records(credential_digest=a, workspace_id="a", project_id=source.project_id, record_type="result", limit=1)
            assert len(page.records) == 1 and page.next_cursor is not None
            next_page = await catalog.list_records(credential_digest=a, workspace_id="a", project_id=source.project_id, record_type="result", cursor=page.next_cursor, limit=1)
            assert len(next_page.records) == 1 and next_page.next_cursor is None
            assert {page.records[0].record_id, next_page.records[0].record_id} == {original["result"]["id"], followed["result"]["id"]}
            with pytest.raises(AuthorizationDenied):
                await catalog.read_record(credential_digest=b, workspace_id="b", project_id=source.project_id, record_id=original["result"]["id"])
            # Same content identity in another explicitly provisioned tenant remains scoped.
            await importer.import_project(source, ImportTarget(source.project_id, "b", "owner_b"), credential_digest=b, batch_id="parity")
            assert await importer.verify_project(source, target, credential_digest=a) == source.fingerprint
            assert (await importer.import_project(source, target, credential_digest=a, batch_id="parity")).disposition == "identical"
            mapping_path = tmp_path / "mapping.json"
            mapping_path.write_text(json.dumps({"schema_version": "k05-local-import/v1", "projects": [{"project_id": target.project_id, "workspace_id": target.workspace_id, "user_id": target.user_id}], "preview_projects": {}}), encoding="utf-8")
            monkeypatch.setenv("K05_IMPORTER_TEST_DIGEST", a.get_secret_value())
            cli = _cli()
            args = cli.arguments(["--source-copy", str(tmp_path / "legacy-copy"), "--mapping", str(mapping_path), "--object-root", str(tmp_path / "objects"), "--schema", schema, "--batch-id", "parity", "--database-env", "K05_TEST_DATABASE_URL", "--credential-env", "K05_IMPORTER_TEST_DIGEST"])
            cli_report = await cli.execute(args)
            assert cli_report["parity"] == "verified" and cli_report["projects"][0]["disposition"] == "identical"
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_records")).fetchone() == (2 * len(source.models),)
                assert await (await conn.execute("SELECT count(*) FROM k05_object_leases")).fetchone() == (0,)
    _run(run())


@pytest.mark.parametrize("interruption", [RuntimeError, asyncio.CancelledError])
def test_real_interrupted_metadata_rolls_back_resume_and_conflict(tmp_path, monkeypatch, interruption):
    source, _, _ = _source(tmp_path)

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            a, _ = await _identities(pool)
            objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "objects"))
            importer = LegacyImporter(pool, objects)
            target = ImportTarget(source.project_id, "a", "owner_a")
            finish = objects.finish
            async def crash(*args, **kwargs):
                raise interruption("synthetic interruption before metadata commit")
            monkeypatch.setattr(objects, "finish", crash)
            with pytest.raises(interruption, match="synthetic interruption"):
                await importer.import_project(source, target, credential_digest=a, batch_id="resume")
            async with pool.connection() as conn:
                for table in ("k05_projects", "k05_records", "k05_current_pointers", "k05_imports", "k05_objects"):
                    assert await (await conn.execute("SELECT count(*) FROM " + table)).fetchone() == (0,)
                assert (await (await conn.execute("SELECT count(*) FROM k05_object_leases")).fetchone())[0] > 0
            monkeypatch.setattr(objects, "finish", finish)
            assert (await importer.import_project(source, target, credential_digest=a, batch_id="resume")).disposition == "created"
            assert (await importer.import_project(source, target, credential_digest=a, batch_id="resume")).disposition == "identical"
            changed = replace(source, current_pointers=())
            with pytest.raises(LegacyImportConflict, match="checkpoint conflicts"):
                await importer.import_project(changed, target, credential_digest=a, batch_id="resume")
            # A completed checkpoint still checks installed parity; no stale success.
            async with pool.connection() as conn:
                await conn.execute("DELETE FROM k05_record_parents WHERE ctid IN(SELECT ctid FROM k05_record_parents LIMIT 1)")
            with pytest.raises(LegacyImportConflict, match="parent projection"):
                await importer.import_project(source, target, credential_digest=a, batch_id="resume")
    _run(run())


def test_real_revoke_between_staging_and_publish_denies_and_rolls_back(tmp_path, monkeypatch):
    source, _, _ = _source(tmp_path)

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            a, _ = await _identities(pool)
            objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "objects"))
            importer = LegacyImporter(pool, objects)
            reached, resume = asyncio.Event(), asyncio.Event()
            original = objects.stage
            first = True
            async def paused(*args, **kwargs):
                nonlocal first
                value = await original(*args, **kwargs)
                if first:
                    first = False
                    reached.set()
                    await resume.wait()
                return value
            monkeypatch.setattr(objects, "stage", paused)
            task = asyncio.create_task(importer.import_project(source, ImportTarget(source.project_id,"a","owner_a"), credential_digest=a, batch_id="revoke"))
            await asyncio.wait_for(reached.wait(), 10)
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 WHERE workspace_id='a' AND user_id='owner_a'")
            resume.set()
            with pytest.raises(AuthorizationDenied):
                await task
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_records")).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k05_imports")).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k05_objects")).fetchone() == (0,)
    _run(run())


def test_real_imported_tombstone_rejects_an_older_source_copy(tmp_path):
    source, _, _ = _source(tmp_path)
    tombstone = build_product_lifecycle_record(record_kind="deletion", project_id=source.project_id, payload={"schema_version": "p6-project-deletion/v1", "status": "deleted", "deleted_count": len(source.models), "retained_shared_count": 0, "purge_object_sha256s": []})
    deleted = LegacyProject(source.project_id, (tombstone,), {}, (), (), ())

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            a, _ = await _identities(pool)
            objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "objects"))
            importer = LegacyImporter(pool, objects)
            target = ImportTarget(source.project_id, "a", "owner_a")
            await importer.import_project(deleted, target, credential_digest=a, batch_id="deleted")
            with pytest.raises(LegacyImportConflict, match="tombstone"):
                await importer.import_project(source, target, credential_digest=a, batch_id="older-backup")
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_records")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k05_object_leases")).fetchone() == (0,)
    _run(run())


def test_real_canonical_bytes_shared_with_artifact_preserve_parity_and_leases(tmp_path):
    preview = build_product_lifecycle_record(record_kind="preview", payload={"format": "synthetic-canonical-artifact"})
    payload = _json_bytes(preview.model_dump(mode="json"))
    root, project_id, _, _ = legacy_journey(tmp_path, extra_artifact=payload)
    with ProductLifecycleStore(root) as lifecycle:
        lifecycle.put(preview)
    with LegacySource(root, preview_projects={preview.record_id: project_id}) as legacy:
        source = legacy.export_project(project_id)
    digest = hashlib.sha256(payload).hexdigest()
    assert source.artifacts[digest] == ("application/json", payload)

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            a, _ = await _identities(pool)
            objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "objects"))
            importer = LegacyImporter(pool, objects)
            target = ImportTarget(source.project_id, "a", "owner_a")
            assert (await importer.import_project(source, target, credential_digest=a, batch_id="overlap")).disposition == "created"
            assert (await importer.import_project(source, target, credential_digest=a, batch_id="overlap")).disposition == "identical"
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_object_leases")).fetchone() == (0,)
    _run(run())


def test_real_expiry_at_final_publication_rolls_back_entire_import(tmp_path, monkeypatch):
    source, _, _ = _source(tmp_path)

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            a, _ = await _identities(pool)
            objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "objects"))
            importer = LegacyImporter(pool, objects)
            target = ImportTarget(source.project_id, "a", "owner_a")
            original = objects.finish
            expired = False
            async def expire_at_metadata_boundary(conn, workspace_id, staged):
                nonlocal expired
                if not expired:
                    expired = True
                    # Trusted test operator uses the same locked transaction: no sleeps or blocked external writer.
                    await conn.execute("UPDATE j01_sessions SET idle_expires_at=statement_timestamp()-interval '1 second' WHERE context_id='ctx_a'")
                await original(conn, workspace_id, staged)
            monkeypatch.setattr(objects, "finish", expire_at_metadata_boundary)
            with pytest.raises(AuthorizationDenied):
                await importer.import_project(source, target, credential_digest=a, batch_id="late-expiry")
            assert expired
            async with pool.connection() as conn:
                for table in ("k05_projects", "k05_records", "k05_current_pointers", "k05_imports", "k05_objects"):
                    assert await (await conn.execute("SELECT count(*) FROM " + table)).fetchone() == (0,)
                assert await (await conn.execute("SELECT idle_expires_at>clock_timestamp() FROM j01_sessions WHERE context_id='ctx_a'")).fetchone() == (True,)
            monkeypatch.setattr(objects, "finish", original)
            assert (await importer.import_project(source, target, credential_digest=a, batch_id="late-expiry")).disposition == "created"
    _run(run())


def test_import_cli_core_only_schema_rejects_before_creating_object_root(tmp_path, monkeypatch):
    source, _, _ = _source(tmp_path)

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            mapping_path = tmp_path / "mapping.json"
            mapping_path.write_text(json.dumps({"schema_version": "k05-local-import/v1", "projects": [{"project_id": source.project_id, "workspace_id": "a", "user_id": "owner_a"}], "preview_projects": {}}), encoding="utf-8")
            root = tmp_path / "must-not-be-created"
            before = _files(tmp_path / "legacy-copy")
            monkeypatch.setenv("K05_IMPORTER_TEST_DIGEST", "a" * 64)
            cli = _cli()
            args = cli.arguments(["--source-copy", str(tmp_path / "legacy-copy"), "--mapping", str(mapping_path), "--object-root", str(root), "--schema", schema, "--batch-id", "core-only", "--database-env", "K05_TEST_DATABASE_URL", "--credential-env", "K05_IMPORTER_TEST_DIGEST"])
            with pytest.raises(LegacyImportError, match="project policy migration"):
                await cli.execute(args)
            assert not root.exists() and _files(tmp_path / "legacy-copy") == before
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_records")).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k05_object_leases")).fetchone() == (0,)
    _run(run())


@pytest.mark.parametrize("after_preflight", [False, True], ids=["before-staging", "after-preflight"])
def test_real_upload_provenance_rejects_legacy_import_before_publication(tmp_path, monkeypatch, after_preflight):
    source, _, _ = _source(tmp_path)

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            a, _ = await _identities(pool)
            objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "objects"))
            importer = LegacyImporter(pool, objects)
            target = ImportTarget(source.project_id, "a", "owner_a")
            reached, resume = asyncio.Event(), asyncio.Event()
            original = objects.stage
            first = True

            async def paused(*args, **kwargs):
                nonlocal first
                assert after_preflight, "An existing upload project must be rejected before staging"
                value = await original(*args, **kwargs)
                if first:
                    first = False
                    reached.set()
                    await resume.wait()
                return value

            async def publish_upload_index():
                async with pool.connection() as conn:
                    await conn.execute("INSERT INTO k05_projects(workspace_id,project_id,creator_id,provenance_kind,revision) VALUES (%s,%s,%s,'upload',7)", (target.workspace_id, target.project_id, target.user_id))

            monkeypatch.setattr(objects, "stage", paused)
            if after_preflight:
                task = asyncio.create_task(importer.import_project(source, target, credential_digest=a, batch_id="provenance"))
                try:
                    await asyncio.wait_for(reached.wait(), 10)
                    # The real second connection wins after preflight and before the final project lock.
                    await publish_upload_index()
                finally:
                    resume.set()
                with pytest.raises(LegacyImportConflict, match="^Legacy import requires local project provenance$"):
                    await task
            else:
                await publish_upload_index()
                with pytest.raises(LegacyImportConflict, match="^Legacy import requires local project provenance$"):
                    await importer.import_project(source, target, credential_digest=a, batch_id="provenance")
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT creator_id,provenance_kind,revision FROM k05_projects WHERE workspace_id=%s AND project_id=%s", (target.workspace_id, target.project_id))).fetchone() == (target.user_id, "upload", 7)
                for table in ("k05_records", "k05_record_parents", "k05_current_pointers", "k05_imports", "k05_objects", "k05_object_references", "k05_confirmations", "k05_preview_leases"):
                    assert await (await conn.execute("SELECT count(*) FROM " + table)).fetchone() == (0,)
                leases = (await (await conn.execute("SELECT count(*) FROM k05_object_leases")).fetchone())[0]
                assert leases > 0 if after_preflight else leases == 0
    _run(run())
