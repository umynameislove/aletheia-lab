"""Regression: recognized K-07 extensions must preserve K-05 readiness verification.

This deliberately fails while the core signature treats the required preview
index/trigger as unknown drift. It never rewrites an installed core signature.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest
from test_k07_upload_staging_postgres import _run

from aletheia_lab.hosted.persistence_migration import MIGRATIONS, _signature
from aletheia_lab.hosted.persistence_migration import migrate as migrate_core
from aletheia_lab.hosted.persistence_migration import verify as verify_core
from aletheia_lab.hosted.preview_migration import migrate as migrate_preview
from aletheia_lab.hosted.preview_migration import verify as verify_preview
from aletheia_lab.hosted.upload_staging_migration import migrate as migrate_staging
from aletheia_lab.hosted.upload_staging_migration import verify as verify_staging

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K07_TEST_DATABASE_URL"),
    reason="Explicit K-07 PostgreSQL test URL required")]


@asynccontextmanager
async def _core_database():
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool

    schema = "k07_compat_" + uuid4().hex
    database = os.environ["K07_TEST_DATABASE_URL"]
    value = urlsplit(database)
    pairs = [(key, item) for key, item in parse_qsl(value.query) if key != "options"]
    pairs.append(("options", "-csearch_path=" + schema))
    scoped = urlunsplit(value._replace(query=urlencode(pairs)))
    async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            async with AsyncConnectionPool(scoped, min_size=1, max_size=2, open=False) as pool:
                await pool.wait(timeout=15)
                async with pool.connection() as conn:
                    assert await migrate_core(conn, schema) == "created"
                yield pool, schema
        finally:
            assert schema.startswith("k07_compat_") and len(schema) == 43
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def _ledger(conn):
    cursor = await conn.execute("SELECT version,checksum,signature FROM k05_persistence_migrations ORDER BY version")
    rows = []
    while (row := await cursor.fetchone()) is not None:
        rows.append(row)
    return rows


def test_staging_extension_preserves_core_migration_verification():
    async def run():
        async with _core_database() as (pool, schema), pool.connection() as conn:
            before = await _ledger(conn)
            assert await verify_core(conn, schema)
            assert await migrate_staging(conn, schema) == "created"
            assert await verify_staging(conn, schema)
            assert await verify_core(conn, schema)
            assert await _ledger(conn) == before
            print("K07_COMPAT " + json.dumps({"extension": "staging", "core_version": len(before),
                "core_verified_before": True, "core_verified_after": True, "core_ledger_unchanged": True}))
    _run(run())


def test_preview_extension_preserves_core_migration_verification():
    async def run():
        async with _core_database() as (pool, schema), pool.connection() as conn:
            assert await verify_core(conn, schema)
            assert await migrate_staging(conn, schema) == "created"
            assert await verify_core(conn, schema)
            before = await _ledger(conn)
            stored = before[-1][2]
            assert await migrate_preview(conn, schema) == "adopted"
            assert await verify_preview(conn)
            assert await verify_staging(conn, schema)
            after_preview = await verify_core(conn, schema)
            actual = await _signature(conn, schema, len(MIGRATIONS))
            assert await _ledger(conn) == before
            print("K07_COMPAT " + json.dumps({"extension": "staging_then_preview", "core_version": len(before),
                "core_verified_before": True, "core_verified_after": after_preview,
                "core_ledger_unchanged": True, "core_stored_signature": stored,
                "core_actual_signature": actual,
                "migration_checksums": [hashlib.sha256(ddl.encode()).hexdigest() for ddl in MIGRATIONS],
                "new_shared_index": "k07_jobs_preview_ref", "new_shared_trigger": "k07_confirm_preview_guard"}))
            assert after_preview, "Recognized K-07 preview index/trigger currently invalidate K-05 core readiness."
    _run(run())
