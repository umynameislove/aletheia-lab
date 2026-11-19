"""Sparse same-tenant authorization must select visible projects before records."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

import pytest
from test_k04_project_policy_postgres import _credential, _database, _run
from test_k04_reviews_postgres import _setup

from aletheia_lab.hosted.postgres_catalog import PostgresCatalog

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K04_TEST_DATABASE_URL'),
    reason='Explicit K04 PostgreSQL test URL required')]


def _walk(node):
    yield node
    for child in node.get('Plans', []):
        yield from _walk(child)


def test_sparse_viewer_uses_actor_indexes_and_does_not_walk_unrelated_tenant_records(tmp_path):
    async def run():
        async with _database() as (pool, schema):
            _, _, catalog, _, result, _ = await _setup(pool, schema, tmp_path)
            async with pool.connection() as conn:
                digest = (await (await conn.execute("SELECT object_sha256 FROM k05_records "
                    "WHERE workspace_id='wa' AND record_id=%s", (result,))).fetchone())[0]
                await conn.execute("INSERT INTO k05_projects(workspace_id,project_id,creator_id) "
                    "SELECT 'wa','p3-project-'||lpad(to_hex(n),64,'0'),'analyst' FROM generate_series(1,5000)n")
                # Metadata-only access-path fixture; these rows are never decoded as analyses.
                await conn.execute('INSERT INTO k05_records(workspace_id,project_id,record_id,record_type,schema_version,'
                    "canonical_sha256,object_sha256) SELECT 'wa','p3-project-'||lpad(to_hex(n),64,'0'),"
                    "'p3-bundle-'||lpad(to_hex(n),64,'0'),'project_bundle','synthetic-query-metadata/v1',%s,%s "
                    "FROM generate_series(1,5000)n", ('c' * 64, digest))
                await conn.execute("INSERT INTO j01_project_grants(workspace_id,project_id,user_id,actions,revision) "
                    "SELECT 'wa','p3-project-'||lpad(to_hex(n),64,'0'),'reviewer',ARRAY['read'],0 "
                    'FROM generate_series(1,5000)n')
                for table in ('k05_records', 'k05_projects', 'j01_project_grants'):
                    await conn.execute('ANALYZE ' + table)
            queries = []

            class Connection:
                def __init__(self, conn):
                    self.conn = conn

                def __getattr__(self, name):
                    return getattr(self.conn, name)

                async def execute(self, query, params=(), **kwargs):
                    if query.startswith('SELECT r.record_id'):
                        queries.append((query, params))
                    return await self.conn.execute(query, params, **kwargs)

            class Pool:
                @asynccontextmanager
                async def connection(self):
                    async with pool.connection() as conn:
                        yield Connection(conn)

            reader = PostgresCatalog(Pool(), catalog.objects)
            page = await reader.list_workspace_records(credential_digest=_credential('viewer'), workspace_id='wa',
                                                       kind='project_bundle', limit=10)
            assert len(page.records) == 2
            query, params = queries[-1]
            async with pool.connection() as conn:
                plan = (await (await conn.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) ' + query, params)).fetchone())[0][0]['Plan']
            nodes = list(_walk(plan))
            assert any(node.get('Index Name') == 'k10_project_grants_reader' and node['Actual Loops'] > 0 for node in nodes)
            assert any(node.get('Index Name') == 'k10_projects_creator' and node['Actual Loops'] > 0 for node in nodes)
            visited = sum((node.get('Actual Rows', 0) + node.get('Rows Removed by Filter', 0)) * node.get('Actual Loops', 0)
                          for node in nodes if node.get('Relation Name') == 'k05_records')
            assert visited < 100, plan
            # The manager branch needs no visible-project union or actor grant scan.
            await reader.list_workspace_records(credential_digest=_credential('owner'), workspace_id='wa', kind='project_bundle', limit=10)
            assert 'visible.project_id' not in queries[-1][0]
    _run(run())
