"""Bounded SQL read model, canonical checksums and scoped continuation contracts."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from test_k04_catalog_http import _view

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.catalog_access import CatalogAccess
from aletheia_lab.hosted.cursors import CursorInvalid, ScopedCursors
from aletheia_lab.hosted.postgres_catalog import CatalogIntegrityError
from aletheia_lab.hosted.read_indexes import _CHECKSUM, _INDEXES
from aletheia_lab.hosted.read_projection_migration import stamp_catalog_revision
from aletheia_lab.hosted.result_views import (
    PROJECTION_VERSION,
    ProjectionUnavailable,
    ResultViews,
    _item,
    prepare_projection,
    publish_projection,
)

DIGEST = SecretStr('a' * 64)


class Rows:
    def __init__(self, rows=()):
        self.rows = iter(rows)

    async def fetchone(self):
        return next(self.rows, None)


def test_projection_is_order_independent_and_keeps_frozen_domain_identifiers():
    view = _view()
    prepared = prepare_projection(view, 'c' * 64)
    shuffled = view.model_copy(update={'claims': tuple(reversed(view.claims)),
        'evidence': tuple(reversed(view.evidence)),
        'graph': view.graph.model_copy(update={'nodes': tuple(reversed(view.graph.nodes)),
                                              'edges': tuple(reversed(view.graph.edges))})})
    assert prepare_projection(shuffled, 'c' * 64) == prepared
    assert prepared.manifest.result_id == view.result.id
    assert prepared.manifest.snapshot_id == view.snapshot.id
    assert len(prepared.items) == len(view.claims) + len(view.evidence) + len(view.graph.nodes) + len(view.graph.edges)
    assert all(_item(row[0], (row[1], row[2], row[3])).id == row[1] for row in prepared.items)
    assert prepare_projection(view, 'd' * 64).manifest.projection_sha256 != prepared.manifest.projection_sha256
    kind, identity, payload, digest, *_ = prepared.items[0]
    for row in [(identity, payload + ' ', digest), ('foreign-item', payload, digest)]:
        with pytest.raises(CatalogIntegrityError):
            _item(kind, row)


def test_publication_is_transaction_enlisted_and_manifest_is_last():
    prepared = prepare_projection(_view(), 'c' * 64)

    class Connection:
        autocommit = False

        def __init__(self):
            self.calls = []
            self.installed = True

        async def execute(self, query, params=()):
            self.calls.append((query, params))
            if query.startswith('SELECT to_regclass'):
                return Rows([(self.installed,)])
            return Rows()

    async def run():
        conn = Connection()
        await publish_projection(conn, 'wa', prepared)
        inserts = [query for query, _ in conn.calls if query.startswith('INSERT')]
        assert len(inserts) == len(prepared.items) + len(prepared.links) + 1
        assert inserts[-1].startswith('INSERT INTO k10_result_views(')
        assert not any('COMMIT' in query for query, _ in conn.calls)
        conn.installed = False
        conn.calls.clear()
        await publish_projection(conn, 'wa', prepared)
        assert not any(query.startswith('INSERT') for query, _ in conn.calls)
        conn.autocommit = True
        with pytest.raises(RuntimeError):
            await publish_projection(conn, 'wa', prepared)
    asyncio.run(run())


def _reader(view=None):
    prepared = prepare_projection(view or _view(), 'c' * 64)
    manifest = prepared.manifest
    calls = []
    state = SimpleNamespace(allowed=True, installed=True, complete=True)

    class Policy:
        async def authorize(self, *args):
            if not state.allowed:
                raise AuthorizationDenied()
            return object()

        async def authorize_record(self, *args):
            if not state.allowed:
                raise AuthorizationDenied()

    class Connection:
        async def execute(self, query, params=()):
            calls.append((query, params))
            if query.startswith("SELECT to_regclass('k10_read_indexes')"):
                return Rows([('k10_read_indexes', 'k10_projects_creator', 'k10_project_grants_reader')])
            if query.startswith('SELECT version,checksum FROM k10_read_indexes'):
                return Rows([(1, _CHECKSUM)])
            if query.startswith('SELECT c.relkind,c.relpersistence'):
                return Rows([('r', 'p', False, False, False, 0, 0)])
            if query.startswith('SELECT attname,format_type'):
                return Rows([('version', 'integer', True, False, '', ''), ('checksum', 'text', True, False, '', '')])
            if query.startswith('SELECT contype,pg_get_constraintdef'):
                return Rows([('c', "CHECK ((checksum ~ '^[0-9a-f]{64}$'::text))"),
                             ('c', 'CHECK ((version = 1))'), ('p', 'PRIMARY KEY (version)')])
            if query.startswith('SELECT indisunique,indisprimary'):
                return Rows([(True, True, True, True, True, '1')])
            if query.startswith('SELECT pg_get_indexdef'):
                name, table, suffix = next(item for item in _INDEXES if item[0] == params[0])
                return Rows([(f'CREATE INDEX {name} ON synthetic.{table} USING btree {suffix}', True, True, True, True, 'synthetic')])
            if query.startswith('SELECT project_id,canonical_sha256,snapshot_id FROM k05_records'):
                return Rows([(manifest.project_id, manifest.canonical_sha256, manifest.snapshot_id)])
            if query.startswith('SELECT to_regclass'):
                return Rows([(state.installed,)])
            if query.startswith('SELECT project_id,snapshot_id,canonical_sha256,projection_sha256'):
                return Rows([(manifest.project_id, manifest.snapshot_id, manifest.canonical_sha256,
                    manifest.projection_sha256, manifest.claim_count, manifest.evidence_count,
                    manifest.node_count, manifest.edge_count, manifest.header_sha256)] if state.complete else [])
            if query.startswith('SELECT item_id,payload,sha256'):
                assert 'ORDER BY item_id LIMIT %s' in query
                assert params[:3] == ('wa', manifest.result_id, PROJECTION_VERSION)
                assert 2 <= params[-1] <= 101
                values = [row for row in prepared.items if row[0] == params[3] and row[1] > params[4]]
                return Rows([(row[1], row[2], row[3]) for row in values[:params[-1]]])
            if query.startswith('SELECT item_id FROM'):
                return Rows([(params[-1],)] if any(row[1] == params[-1] for row in prepared.items) else [])
            raise AssertionError(query)

    @asynccontextmanager
    async def connection():
        yield Connection()

    # Deliberately no canonical object reader: a page cannot accidentally open it.
    catalog = SimpleNamespace(pool=SimpleNamespace(connection=connection), policy=Policy())
    return ResultViews(catalog), prepared, state, calls


def test_sql_page_reads_limit_plus_one_and_denies_missing_partial_or_revoked_index():
    async def run():
        reader, prepared, state, calls = _reader()
        manifest = await reader.manifest(DIGEST, 'wa', prepared.manifest.result_id)
        page, continuation = await reader.page(DIGEST, 'wa', manifest, kind='evidence', limit=1)
        assert len(page) == 1
        assert continuation == (page[-1].id if manifest.evidence_count > 1 else None)
        query, params = next((query, params) for query, params in calls if query.startswith('SELECT item_id,payload'))
        assert params[-1] == 2 and 'item_id>%s' in query
        with pytest.raises(CursorInvalid):
            await reader.page(DIGEST, 'wa', manifest, kind='claim', after='foreign-cursor')
        with pytest.raises(CursorInvalid):
            await reader.page(DIGEST, 'wa', replace(manifest, projection_sha256='b' * 64), kind='claim')
        state.complete = False
        with pytest.raises(ProjectionUnavailable):
            await reader.manifest(DIGEST, 'wa', manifest.result_id)
        state.installed = False
        with pytest.raises(ProjectionUnavailable):
            await reader.manifest(DIGEST, 'wa', manifest.result_id)
        state.allowed = False
        calls.clear()
        with pytest.raises(AuthorizationDenied):
            await reader.manifest(DIGEST, 'wa', manifest.result_id)
        assert calls == []  # no private/index read after permission rejection
    asyncio.run(run())


def test_cursor_pins_projection_query_context_and_catalog_generation():
    from test_k02_graph_boundary import _large_view

    async def run():
        reader, prepared, state, _ = _reader(_large_view(3))
        access = CatalogAccess(reader.catalog, ScopedCursors(SecretStr('synthetic-k10-cursor-' + 'x' * 40)), None)
        access.views = reader
        state.revision = 1

        async def binding(digest, workspace, context, query, *, project=None):
            return {'workspace_id': workspace, 'context_id': context, 'query': query,
                    'catalog_revision': state.revision}

        access._binding = binding
        first = await access.result_items(DIGEST, 'wa', prepared.manifest.result_id, 'ctx', kind='evidence', limit=1)
        assert first['total'] == prepared.manifest.evidence_count and first['total_kind'] == 'exact'
        cursor = first['next_cursor']
        assert cursor
        for context, kwargs in [('other', {}), ('ctx', {'search': 'changed'}), ('ctx', {'limit': 2})]:
            with pytest.raises(CursorInvalid):
                await access.result_items(DIGEST, 'wa', prepared.manifest.result_id, context,
                    kind='evidence', limit=kwargs.pop('limit', 1), cursor=cursor, **kwargs)
        state.revision += 1
        with pytest.raises(CursorInvalid):
            await access.result_items(DIGEST, 'wa', prepared.manifest.result_id, 'ctx', kind='evidence', limit=1, cursor=cursor)
    asyncio.run(run())


@pytest.mark.parametrize('installed', [False, True])
def test_grant_revision_stamp_is_optional_for_legacy_but_never_hides_partial_profile(installed):
    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, query, params=()):
            self.calls.append((query, params))
            if query.startswith('SELECT'):
                return Rows([(installed,)])
            raise RuntimeError('partial profile must fail the grant transaction')

    conn = Connection()
    if installed:
        with pytest.raises(RuntimeError, match='partial profile'):
            asyncio.run(stamp_catalog_revision(conn, 'wa'))
        assert conn.calls[-1][1] == ('wa',)
    else:
        asyncio.run(stamp_catalog_revision(conn, 'wa'))
        assert len(conn.calls) == 1
