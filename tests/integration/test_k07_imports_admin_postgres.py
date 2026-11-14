"""Operator import migration workflow owns one transaction and preserves older commands."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_k07_migration_compatibility_postgres import _core_database
from test_k07_upload_staging_postgres import _run

from aletheia_lab.hosted.persistence_migration import verify as verify_core

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K07_TEST_DATABASE_URL"),
    reason="Explicit K-07 PostgreSQL test URL required")]


def test_real_import_admin_rolls_back_both_extensions_and_keeps_staging_commands(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts" / "k07_uploads_admin.py"
    spec = importlib.util.spec_from_file_location("k07_owned_admin", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = module.migrate_preview

    async def reject_after_preview(conn, schema):
        await original(conn, schema)
        raise RuntimeError("Synthetic operator failure before committing both import extensions.")

    async def run():
        async with _core_database() as (pool, schema):
            args = SimpleNamespace(action="migrate-imports", schema=schema,
                                   database_url_env="K07_TEST_DATABASE_URL", limit=100, grace_seconds=300)
            monkeypatch.setattr(module, "migrate_preview", reject_after_preview)
            with pytest.raises(RuntimeError, match="Synthetic operator failure"):
                await module._main(args)
            async with pool.connection() as conn:
                assert await verify_core(conn, schema)
                assert await (await conn.execute("SELECT to_regclass('k07_uploads'),to_regclass('k07_import_previews'),"
                    "to_regclass('k07_upload_migrations'),to_regclass('k07_preview_migrations')")).fetchone() == (None, None, None, None)
            args.action = "migrate"
            assert await module._main(args) == 0
            args.action = "verify"
            assert await module._main(args) == 0
            args.action = "cleanup"
            assert await module._main(args) == 0
            async with pool.connection() as conn:
                assert await verify_core(conn, schema)
    _run(run())
