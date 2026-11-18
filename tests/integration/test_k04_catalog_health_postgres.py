"""Readiness observes revoked SELECT on a real non-owner API LOGIN principal."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from test_k04_runtime_principal_postgres import _principals, _run

from aletheia_lab.hosted.catalog_access import CatalogAccess
from aletheia_lab.hosted.clock_guarded_backend import ClockGuardedBackend

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit K-04 PostgreSQL test URL required")]


def test_current_catalog_health_fails_after_annotation_select_revoke_and_recovers():
    from psycopg import sql

    async def scenario():
        async with _principals() as (_, api, _, schema, api_role, _, admin):
            access = CatalogAccess(SimpleNamespace(pool=api), None, None)

            async def ready():
                return True

            backend = ClockGuardedBackend(SimpleNamespace(is_ready=ready), access.is_ready)
            assert await access.is_ready() and await backend.is_ready()
            await admin.execute(sql.SQL("REVOKE SELECT ON {}.j01_annotations FROM {}").format(
                sql.Identifier(schema), sql.Identifier(api_role)))
            try:
                assert not await access.is_ready()
                assert not await backend.is_ready()
            finally:
                await admin.execute(sql.SQL("GRANT SELECT ON {}.j01_annotations TO {}").format(
                    sql.Identifier(schema), sql.Identifier(api_role)))
            assert await access.is_ready() and await backend.is_ready()
    _run(scenario())
