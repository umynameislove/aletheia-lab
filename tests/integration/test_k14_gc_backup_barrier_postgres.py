"""A physical recovery point prevents existing GC from removing its closure."""
import asyncio
import os

import pytest
from test_k05_objects_postgres import _database, _old, _run

from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.recovery_points import BACKUP_ADVISORY_KEY

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K14_TEST_DATABASE_URL'),
    reason='Explicit private K14 PostgreSQL URL required')]


def test_existing_gc_defers_under_backup_lock_and_recovers_after_unlock(tmp_path, monkeypatch):
    monkeypatch.setenv('K05_TEST_DATABASE_URL', os.environ['K14_TEST_DATABASE_URL'])
    async def run():
        async with _database() as pool:
            store = FilesystemObjectStore(tmp_path)
            objects = PostgresObjects(pool, store)
            staged = await objects.stage('a', b'synthetic orphan for backup', media_type='application/octet-stream')
            _old(store, staged.descriptor)
            async with pool.connection() as conn:
                await conn.execute("UPDATE k05_object_leases SET expires_at=clock_timestamp()-interval '1 second'")
            async with pool.connection() as backup:
                await backup.execute('SELECT pg_advisory_lock(%s)', (BACKUP_ADVISORY_KEY,))
                try:
                    async with asyncio.timeout(1):
                        skipped = await objects.reconcile(grace_seconds=60)
                    assert skipped.deferred_for_backup and skipped.examined == skipped.removed == 0
                    assert store.path(staged.descriptor.sha256).is_file()
                    async with pool.connection() as conn:
                        assert await (await conn.execute('SELECT count(*) FROM k05_object_leases')).fetchone() == (1,)
                finally:
                    assert await (await backup.execute('SELECT pg_advisory_unlock(%s)', (BACKUP_ADVISORY_KEY,))).fetchone() == (True,)
            recovered = await objects.reconcile(grace_seconds=60)
            assert not recovered.deferred_for_backup and recovered.removed == 1 and recovered.expired_leases_removed == 1
            assert not store.path(staged.descriptor.sha256).exists()
    _run(run())
