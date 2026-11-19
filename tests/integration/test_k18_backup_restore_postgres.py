"""Actual custom-format PostgreSQL restore plus authenticated latest deletion history."""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import AsyncConnectionPool
from test_k05_native_results_postgres import _case, _initial, _run

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.persistence_migration import verify
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
from aletheia_lab.hosted.recovery_checkpoint import apply_checkpoint, capture_checkpoint
from aletheia_lab.hosted.recovery_points import RecoveryRejected, capture_bundle, restore_bundle
from aletheia_lab.hosted.retention import PostgresRetention
from aletheia_lab.hosted.review_migration import migrate as migrate_reviews

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get('K18_TEST_DATABASE_URL') or not os.environ.get('K18_TEST_PG_BIN'),
    reason='Explicit private K18 PostgreSQL and client binaries required')]


def test_real_restore_preserves_immutable_payload_and_applies_later_delete_and_revocation(tmp_path, monkeypatch):
    monkeypatch.setenv('K05_TEST_DATABASE_URL', os.environ['K18_TEST_DATABASE_URL'])
    async def scenario():
        database = os.environ['K18_TEST_DATABASE_URL']
        pg_bin = Path(os.environ['K18_TEST_PG_BIN'])
        key = b'synthetic-restore-signing-key-not-a-real-deployment-key'
        target_name = 'k18_restore_' + uuid4().hex
        options = conninfo_to_dict(database)
        options['dbname'] = target_name
        options.pop('options', None)
        target = make_conninfo(**options)
        async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
            await admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(target_name)))
            try:
                (tmp_path / 'source').mkdir()
                async with _case(tmp_path / 'source') as case:
                    async with case.pool.connection() as conn:
                        schema = (await (await conn.execute('SELECT current_schema()')).fetchone())[0]
                        await migrate_reviews(conn, schema)
                    _, record = await _initial(case)
                    old_bytes = case.initial.model_dump_json()
                    retention = PostgresRetention(case.pool, case.catalog)
                    bundle = tmp_path / 'backup'
                    before = await asyncio.to_thread(capture_bundle, database, schema,
                        tmp_path / 'source/native-objects', bundle, pg_bin=pg_bin, signing_key=key)
                    assert before['objects'] > 0
                    # Both effects happen AFTER the backup snapshot and must not be resurrected.
                    receipt = await PostgresProjectMutations(case.pool, case.catalog).delete(
                        credential_digest=case.a, workspace_id='a', project_id=record.project_id,
                        expected_revision=record.revision)
                    async with case.pool.connection() as conn:
                        await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 WHERE workspace_id='a' AND user_id='owner_b'")
                    checkpoint = tmp_path / 'latest-checkpoint'
                    checkpoint_manifest = await capture_checkpoint(retention, checkpoint, schema=schema, signing_key=key)
                    async with await psycopg.AsyncConnection.connect(target) as guarded:
                        await guarded.execute('ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO PUBLIC')
                    with pytest.raises(RecoveryRejected):
                        await asyncio.to_thread(restore_bundle, target, bundle, tmp_path / 'must-not-open',
                            pg_bin=pg_bin, signing_key=key)
                    assert not (tmp_path / 'must-not-open').exists()
                    async with await psycopg.AsyncConnection.connect(target) as guarded:
                        await guarded.execute('ALTER DEFAULT PRIVILEGES REVOKE SELECT ON TABLES FROM PUBLIC')
                    start = time.monotonic()
                    outcome = await asyncio.to_thread(restore_bundle, target, bundle, tmp_path / 'restored-objects',
                        pg_bin=pg_bin, signing_key=key)
                    assert outcome['status'] == 'restored_not_ready'
                    scoped = make_conninfo(target, options='-csearch_path=' + schema)
                    async with AsyncConnectionPool(scoped, min_size=1, max_size=2, open=False) as pool:
                        await pool.wait(timeout=10)
                        catalog = PostgresCatalog(pool, PostgresObjects(pool, FilesystemObjectStore(tmp_path / 'restored-objects')))
                        async with pool.connection() as conn:
                            assert await verify(conn, schema)
                        saved = await catalog.read_record(credential_digest=case.a, workspace_id='a',
                            project_id=record.project_id, record_id=record.record_id)
                        assert saved.model_dump_json() == old_bytes
                        checkpoint_outcome = await apply_checkpoint(PostgresRetention(pool, catalog), checkpoint,
                            schema=schema, signing_key=key, expected_manifest_sha256=checkpoint_manifest['manifest_sha256'])
                        assert checkpoint_outcome['restored_sessions_revoked']
                        async with pool.connection() as conn:
                            state = await (await conn.execute('SELECT deletion_tombstone_id FROM k05_projects WHERE workspace_id=%s AND project_id=%s',
                                ('a', record.project_id))).fetchone()
                            assert state == (receipt['tombstone_id'],)
                            assert await (await conn.execute("SELECT active FROM j01_memberships WHERE workspace_id='a' AND user_id='owner_b'")).fetchone() == (False,)
                            assert await (await conn.execute("SELECT count(*) FROM j01_sessions WHERE state='active'")).fetchone() == (0,)
                        with pytest.raises(AuthorizationDenied):
                            await catalog.read_record(credential_digest=case.a, workspace_id='a',
                                project_id=record.project_id, record_id=record.record_id)
                    elapsed = time.monotonic() - start
                    assert elapsed < 1800
                    report = {'scope': 'Native logical PG dump/restore + immutable object hashes + latest authenticated deletion/authority history. No physicalPITR/hostloss/IdP/joint claim.',
                        'backup_id': before['backup_id'], 'restored_objects': before['objects'],
                        'immutable_record_sha256': hashlib.sha256(old_bytes.encode()).hexdigest(),
                        'latest_tombstone_id': receipt['tombstone_id'], 'revoked_membership_stays_revoked': True,
                        'old_sessions_revoked': True, 'restore_and_checkpoint_seconds': elapsed,
                        'runtime_grants_issued': False}
                    output = os.environ.get('K18_EVIDENCE_DIR')
                    if output:
                        import json
                        (Path(output) / 'logical-restore.json').write_text(json.dumps(report, indent=2) + '\n')
            finally:
                await admin.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()', (target_name,))
                await admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(target_name)))
    _run(scenario())
