"""V14 to V16 under real catalog reads; old wire bytes/history stay immutable."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from test_k05_migrations_postgres import _run
from test_k05_native_results_postgres import _case, _initial

from aletheia_lab.hosted import persistence_migration as core
from aletheia_lab.hosted.review_migration import migrate as migrate_reviews
from aletheia_lab.hosted.review_migration import verify as verify_reviews

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K05_TEST_DATABASE_URL'),
    reason='Explicit private PostgreSQL URL required')]


def test_v14_v16_preserves_canonical_bytes_and_old_ledger_under_catalog_read_traffic(tmp_path):
    async def run():
        current = core.MIGRATIONS
        with patch.object(core, 'MIGRATIONS', current[:14]):
            manager = _case(tmp_path)
            case = await manager.__aenter__()
        try:
            await _initial(case)
            async with case.pool.connection() as conn:
                schema = (await (await conn.execute('SELECT current_schema()')).fetchone())[0]
                with patch.object(core, 'MIGRATIONS', current[:14]):
                    await migrate_reviews(conn, schema)
                old = await (await conn.execute('SELECT version,checksum,signature FROM k05_persistence_migrations ORDER BY version')).fetchall()
            before = case.initial.model_dump_json()
            observed, latency = [], []
            stop = asyncio.Event()
            async def readers():
                while not stop.is_set():
                    started = time.monotonic()
                    value = await case.catalog.read_record(credential_digest=case.a, workspace_id='a',
                        project_id=case.initial.project_id, record_id=case.initial.record_id)
                    observed.append(value.model_dump_json())
                    latency.append(time.monotonic() - started)
                    await asyncio.sleep(0)
            traffic = asyncio.create_task(readers())
            try:
                while len(observed) < 5:
                    await asyncio.sleep(.001)
                started = time.monotonic()
                async with case.pool.connection() as conn:
                    await conn.execute("SET LOCAL lock_timeout='1s'")
                    await conn.execute("SET LOCAL statement_timeout='30s'")
                    assert await core.migrate(conn, schema) == 'upgraded'
                    await migrate_reviews(conn, schema)
                duration = time.monotonic() - started
                while len(observed) < 10:
                    await asyncio.sleep(.001)
            finally:
                stop.set()
                await traffic
            async with case.pool.connection() as conn:
                assert await core.verify(conn, schema) and await verify_reviews(conn, schema)
                rows = await (await conn.execute('SELECT version,checksum,signature FROM k05_persistence_migrations ORDER BY version')).fetchall()
                assert rows[:14] == old and [r[0] for r in rows] == list(range(1, len(core.MIGRATIONS) + 1))
                assert await core.migrate(conn, schema) == 'unchanged'
            assert observed and set(observed) == {before}
            report = {'scope': f'Native catalog reads during additive V14->V{len(core.MIGRATIONS)}; no old-binary rolling-readiness claim.',
                'reads': len(observed), 'read_errors': 0, 'maximum_read_seconds': max(latency),
                'migration_seconds': duration, 'old_14_ledger_rows_unchanged': True,
                'canonical_bytes_equal': True, 'review_v3_verified': True,
                'deployment_policy': 'Drain old RC before migration; strict old readiness cannot adopt unknown newer ledger.'}
            if folder := os.environ.get('K18_EVIDENCE_DIR'):
                Path(folder).mkdir(parents=True, exist_ok=True)
                (Path(folder) / 'migration-traffic.json').write_text(json.dumps(report, indent=2) + '\n')
        finally:
            await manager.__aexit__(None, None, None)
    _run(run())
