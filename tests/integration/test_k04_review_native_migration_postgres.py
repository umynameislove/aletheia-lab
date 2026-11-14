"""Real review V1 adoption, V2 ledger drift and canonical native-parent guards."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from pydantic import SecretStr
from test_k04_project_policy_postgres import _credential, _database, _run
from test_k05_legacy_import_postgres import _source
from test_k05_native_results_postgres import _zip

from aletheia_lab.hosted import review_migration as migration
from aletheia_lab.hosted.authorization import PostgresAuthorization
from aletheia_lab.hosted.canonical_results import CanonicalResults
from aletheia_lab.hosted.legacy_import import ImportTarget, LegacyImporter
from aletheia_lab.hosted.native_upload_records import build_initial_upload_result
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.upload_analysis import analyze_initial_upload
from aletheia_lab.hosted.upload_project import UploadProject
from aletheia_lab.hosted.upload_publication import UploadPublication
from aletheia_lab.product.demo_flow import _demo_mapping

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit K-04 PostgreSQL test URL required")]


async def _install_original_v1(pool, schema):
    """Reproduce the historical V1 install using its unchanged shipped SQL bytes."""
    ddl = Path(migration.__file__).with_name("review_schema.sql").read_bytes()
    async with pool.connection() as conn:
        await migration._context(conn, schema)
        await conn.execute(ddl.decode("utf-8"))
        await conn.execute("CREATE TABLE k04_review_migrations(version integer PRIMARY KEY CHECK(version>0),"
            "checksum text NOT NULL CHECK(checksum~'^[0-9a-f]{64}$'),"
            "signature text NOT NULL CHECK(signature~'^[0-9a-f]{64}$'),"
            "applied_at timestamptz NOT NULL DEFAULT clock_timestamp())")
        await conn.execute("INSERT INTO k04_review_migrations(version,checksum,signature) VALUES(1,%s,%s)",
                           (hashlib.sha256(ddl).hexdigest(), await migration._signature(conn)))
        return await (await conn.execute("SELECT version,checksum,signature,applied_at FROM k04_review_migrations")).fetchone()


async def _annotation(pool, project, result, *, revision=1, author="analyst", annotation="native-note"):
    async with pool.connection() as conn:
        await conn.execute("INSERT INTO j01_annotations(workspace_id,project_id,result_id,annotation_id,revision,"
            "author_id,body,status) VALUES('wa',%s,%s,%s,%s,%s,'Synthetic human comment','comment')",
            (project, result, annotation, revision, author))


def test_v1_upgrade_preserves_annotation_rows_and_original_ledger_then_replays_unchanged(tmp_path):
    async def scenario():
        async with _database() as (pool, schema):
            source, original, _ = _source(tmp_path)
            objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "legacy-review-objects"))
            await LegacyImporter(pool, objects).import_project(source, ImportTarget(source.project_id, "wa", "analyst"),
                credential_digest=_credential("analyst"), batch_id="synthetic-review-upgrade")
            prior_ledger = await _install_original_v1(pool, schema)
            await _annotation(pool, source.project_id, original["result"]["id"])
            async with pool.connection() as conn:
                before = await migration._rows(conn, "SELECT * FROM j01_annotations ORDER BY revision")
                assert not await migration.verify(conn, schema)
                assert await migration.migrate(conn, schema) == "upgraded"
                assert await migration.verify(conn, schema)
                after = await migration._rows(conn, "SELECT * FROM j01_annotations ORDER BY revision")
                assert before == after
                assert await (await conn.execute("SELECT version,checksum,signature,applied_at FROM k04_review_migrations "
                    "WHERE version=1")).fetchone() == prior_ledger
                assert await (await conn.execute("SELECT version,checksum FROM k04_review_migrations ORDER BY version")).fetchone() == (1, migration._checksums()[0])
                rows = await migration._rows(conn, "SELECT version,checksum,signature,applied_at FROM k04_review_migrations ORDER BY version")
                assert [row[:2] for row in rows] == list(enumerate(migration._checksums(), 1))
                assert await migration.migrate(conn, schema) == "unchanged"
                assert rows == await migration._rows(conn, "SELECT version,checksum,signature,applied_at FROM k04_review_migrations ORDER BY version")
    _run(scenario())


@pytest.mark.parametrize("drift", ["checksum", "signature", "trigger", "unknown"])
def test_v1_upgrade_rejects_forged_prefix_and_full_structural_drift(drift):
    async def scenario():
        async with _database() as (pool, schema):
            await _install_original_v1(pool, schema)
            async with pool.connection() as conn:
                if drift == "checksum":
                    await conn.execute("UPDATE k04_review_migrations SET checksum=%s", ("0" * 64,))
                elif drift == "signature":
                    await conn.execute("UPDATE k04_review_migrations SET signature=%s", ("0" * 64,))
                elif drift == "trigger":
                    await conn.execute("ALTER TABLE j01_annotations DISABLE TRIGGER j01_annotations_insert_guard")
                else:
                    await conn.execute("INSERT INTO k04_review_migrations(version,checksum,signature) VALUES(99,%s,%s)", ("0" * 64, "0" * 64))
            with pytest.raises(migration.ReviewMigrationRejected):
                async with pool.connection() as conn:
                    await migration.migrate(conn, schema)
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k04_review_migrations WHERE version=2")).fetchone() == (0,)
    _run(scenario())


@pytest.mark.parametrize("drift", ["v1-checksum", "v1-signature", "v2-checksum", "trigger", "unknown"])
def test_v2_verify_and_replay_reject_historical_edits_unknown_revisions_and_live_drift(drift):
    async def scenario():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                assert await migration.migrate(conn, schema) == "created"
                assert await migration.verify(conn, schema)
                if drift in ("v1-checksum", "v2-checksum"):
                    await conn.execute("UPDATE k04_review_migrations SET checksum=%s WHERE version=%s",
                                       ("0" * 64, 1 if drift == "v1-checksum" else 2))
                elif drift == "v1-signature":
                    await conn.execute("UPDATE k04_review_migrations SET signature=%s WHERE version=1", ("0" * 64,))
                elif drift == "trigger":
                    await conn.execute("ALTER TABLE j01_annotations DISABLE TRIGGER j01_annotations_insert_guard")
                else:
                    await conn.execute("INSERT INTO k04_review_migrations(version,checksum,signature) VALUES(99,%s,%s)", ("0" * 64, "0" * 64))
                assert not await migration.verify(conn, schema)
            with pytest.raises(migration.ReviewMigrationRejected):
                async with pool.connection() as conn:
                    await migration.migrate(conn, schema)
    _run(scenario())


async def _publish_native(pool, tmp_path):
    payload = _zip()
    limits = dict(maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)
    receipt = await PostgresSources(pool, **limits).put(credential_digest=_credential("analyst"), workspace_id="wa",
        request_key=SecretStr("synthetic-review-native-01"), payload=payload)
    private = tmp_path / "native-worker"
    private.mkdir()
    worker = UploadProject(private, **limits)
    captured = "2026-11-10T00:00:00Z"
    preview = worker.preview(payload, captured_at=captured)
    snapshot = worker.confirm(payload, workspace_id="wa", source_id=receipt.source_id, mapping=_demo_mapping(preview), captured_at=captured)
    question = "What is observed in this exact uploaded source?"
    view = analyze_initial_upload(snapshot, question)
    record = build_initial_upload_result(snapshot, question, view)
    objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "native-review-objects"))
    uploads, results = UploadPublication(objects), CanonicalResults(objects)
    prepared_snapshot = await uploads.prepare_snapshot(snapshot)
    prepared_result = await results.prepare_initial_result("wa", snapshot=snapshot, record=record)
    policy = PostgresAuthorization()
    async with pool.connection() as conn:
        published_snapshot = await uploads.enlist_snapshot(conn, authorization=await policy.authorize(
            conn, _credential("analyst").get_secret_value(), "wa", "confirm"), prepared=prepared_snapshot)
        published_result = await results.enlist_result(conn, authorization=await policy.authorize(
            conn, _credential("analyst").get_secret_value(), "wa", "analyze"), prepared=prepared_result,
            expected_revision=published_snapshot.revision)
    return published_result.project_id, published_result.record_id, published_snapshot.snapshot_id


def test_real_native_result_parent_accepts_reviews_but_other_kinds_author_switch_and_cas_still_fail(tmp_path):
    import psycopg

    async def scenario():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                assert await migration.migrate(conn, schema) == "created"
            project, result, snapshot = await _publish_native(pool, tmp_path)
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT record_type FROM k05_records WHERE workspace_id='wa' AND record_id=%s", (result,))).fetchone() == ("upload_result",)
            await _annotation(pool, project, result)
            for revision, author in ((2, "owner"), (3, "analyst"), (1, "analyst")):
                with pytest.raises(psycopg.errors.CheckViolation):
                    await _annotation(pool, project, result, revision=revision, author=author)
            for wrong_parent in (snapshot, "missing-parent"):
                with pytest.raises(psycopg.errors.CheckViolation):
                    await _annotation(pool, project, wrong_parent, annotation="wrong-kind")
            await _annotation(pool, project, result, revision=2)
            for sql in ("UPDATE j01_annotations SET body='tampered'", "DELETE FROM j01_annotations"):
                with pytest.raises(psycopg.errors.CheckViolation):
                    async with pool.connection() as conn:
                        await conn.execute(sql)
            async with pool.connection() as conn:
                assert await migration._rows(conn, "SELECT revision,author_id FROM j01_annotations ORDER BY revision") == [(1, "analyst"), (2, "analyst")]
                assert await migration.verify(conn, schema)
                assert await migration.migrate(conn, schema) == "unchanged"
    _run(scenario())
