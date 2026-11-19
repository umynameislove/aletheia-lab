"""Actual PostgreSQL bounded read projections, invalidation and atomic rebuild."""
from __future__ import annotations

import os

import pytest
from pydantic import SecretStr
from test_k04_project_policy_postgres import _credential, _database, _run
from test_k04_reviews_postgres import _setup

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.catalog_access import CatalogAccess
from aletheia_lab.hosted.cursors import CursorInvalid, ScopedCursors
from aletheia_lab.hosted.persistence_migration import verify as verify_persistence
from aletheia_lab.hosted.postgres_catalog import CatalogIntegrityError
from aletheia_lab.hosted.project_policy_migration import verify as verify_policy
from aletheia_lab.hosted.read_indexes import ReadIndexesRejected, verified_extension
from aletheia_lab.hosted.result_views import (
    ProjectionUnavailable,
    prepare_projection,
    publish_projection,
    rebuild_result_views,
)
from aletheia_lab.hosted_contract.workspaces import ResultHeader
from aletheia_lab.product.lifecycle import build_product_lifecycle_record
from aletheia_lab.product.results import ProductResultEnvelope, _view_from_record
from aletheia_lab.product.view import ProductView
from aletheia_lab.project.persistence import _json_bytes

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K04_TEST_DATABASE_URL'),
    reason='Explicit K04 PostgreSQL test URL required')]


def _access(catalog):
    return CatalogAccess(catalog, ScopedCursors(SecretStr('synthetic-k10-cursor-' + 'x' * 40)), None)


def _expanded_record(view, count=135):
    data = view.model_dump(mode='json')
    original = data['evidence'][0]
    snapshot_node = next(node['id'] for node in data['graph']['nodes'] if node['kind'] == 'Snapshot')
    for number in range(count):
        identity = f'k10-evidence-{number:06}'
        data['evidence'].append({**original, 'id': identity, 'kind': 'snapshot',
            'title': 'Đối chiếu K10 ' + str(number), 'text': 'vietnamesetoken searchable bounded evidence',
            'reproduction_ref': {'record_id': view.snapshot.id, 'record_kind': 'snapshot'}})
        node = f'k10-node-{number:06}'
        data['graph']['nodes'].append({'id': node, 'kind': 'EvidenceItem', 'source_id': identity, 'visibility': 'diagnosis'})
        data['graph']['edges'].append({'id': f'k10-edge-{number:06}', 'kind': 'OBSERVED_IN',
            'source': node, 'target': snapshot_node, 'visibility': 'diagnosis'})
        for turn in data['conversation']['turns']:
            turn['visible_evidence_ids'].append(identity)
    checked = ProductView.model_validate_json(__import__('json').dumps(data))
    record = build_product_lifecycle_record(record_kind='result', project_id=view.project.id, snapshot_id=view.snapshot.id,
        payload=ProductResultEnvelope(view=checked.model_dump(mode='json')).model_dump(mode='json'))
    return record, _view_from_record(record)


async def _append(catalog, record, *, interrupt=False):
    """Synthetic operator capture uses the real immutable domain record and object store."""
    staged = await catalog.objects.stage('wa', _json_bytes(record.model_dump(mode='json')),
        media_type='application/json', schema_version=record.schema_version)
    view = _view_from_record(record)
    async with catalog.pool.connection() as conn:
        await catalog.objects.ready(conn, 'wa', staged)
        await conn.execute('INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,'
            'canonical_sha256,object_sha256,snapshot_id,parent_result_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            ('wa', record.record_id, record.project_id, 'result', record.schema_version, record.canonical_sha256(),
             staged.descriptor.sha256, record.snapshot_id, record.parent_result_id))
        await conn.execute('INSERT INTO k05_record_parents(workspace_id,project_id,record_id,relation,parent_id) '
            "VALUES('wa',%s,%s,'snapshot',%s)", (record.project_id, record.record_id, record.snapshot_id))
        await publish_projection(conn, 'wa', prepare_projection(view, record.canonical_sha256()))
        if interrupt:
            raise RuntimeError('synthetic publication interruption')
        await catalog.objects.finish(conn, 'wa', staged)


def test_sql_pages_search_evidence_membership_and_graph_are_lossless_without_object_reads(tmp_path, monkeypatch):
    async def run():
        async with _database() as (pool, schema):
            _, _, catalog, project, result, _ = await _setup(pool, schema, tmp_path)
            access = _access(catalog)
            _, original = await access.result(_credential('viewer'), 'wa', result)
            record, view = _expanded_record(original)
            await _append(catalog, record)

            async def forbidden(*args, **kwargs):
                raise AssertionError('A page opened the full canonical object')

            monkeypatch.setattr(catalog.objects, 'read', forbidden)
            assert await access.paged_ready()
            header = ResultHeader.model_validate(await access.result_header(_credential('viewer'), 'wa', record.record_id))
            assert header.result == view.result and header.project == view.project and header.runtime == view.runtime
            assert header.evidence_count == len(view.evidence) and header.turn_count == len(view.conversation.turns)
            assert 'snapshot' not in header.model_dump() and 'claims' not in header.model_dump()
            evidence, cursor = [], None
            while True:
                page = await access.result_items(_credential('viewer'), 'wa', record.record_id, 'ctx_viewer',
                    kind='evidence', limit=17, cursor=cursor)
                assert len(page['items']) <= 17 and page['total'] == len(view.evidence) and page['total_kind'] == 'exact'
                evidence.extend(item.id for item in page['items'])
                cursor = page['next_cursor']
                if cursor is None:
                    break
            assert evidence == sorted(item.id for item in view.evidence) and len(evidence) == len(set(evidence))
            searched = await access.result_items(_credential('viewer'), 'wa', record.record_id, 'ctx_viewer',
                kind='evidence', search='vietnamesetoken', limit=100)
            assert len(searched['items']) == 100 and searched['next_cursor'] and searched['total_kind'] == 'unknown'
            selected = view.claims[0]
            linked = await access.result_evidence(_credential('viewer'), 'wa', record.record_id, selected.citation_ids[0], selected.id)
            assert linked['relation'] == 'citation' and linked['project_id'] == project
            with pytest.raises(AuthorizationDenied):
                await access.result_evidence(_credential('viewer'), 'wa', record.record_id, view.evidence[-1].id, selected.id)
            nodes, edges, cursor = set(), set(), None
            for _ in range(100):
                page = await access.result_graph(_credential('viewer'), 'wa', record.record_id, 'ctx_viewer', limit=17, cursor=cursor)
                graph = page['graph']
                assert len(graph.nodes) <= 17 and len(graph.edges) <= 1000
                nodes.update(node.id for node in graph.nodes)
                edges.update(edge.id for edge in graph.edges)
                cursor = page['next_cursor']
                if cursor is None:
                    break
            else:
                pytest.fail('graph continuation made no bounded progress')
            assert nodes == {node.id for node in view.graph.nodes} and edges == {edge.id for edge in view.graph.edges}
            with pytest.raises(AuthorizationDenied):
                await access.result_items(_credential('other'), 'wb', record.record_id, 'ctx_other', kind='claim')
    _run(run())


def test_catalog_cursors_reject_inserts_grant_revisions_and_delivery_rechecks_revoke(tmp_path):
    async def run():
        async with _database() as (pool, schema):
            _, manager, catalog, project, result, _ = await _setup(pool, schema, tmp_path)
            access = _access(catalog)
            first = await access.workspace_records(_credential('viewer'), 'wa', 'ctx_viewer', kind='result', limit=1)
            assert first['next_cursor']
            _, view = await access.result(_credential('viewer'), 'wa', result)
            record, _ = _expanded_record(view, 3)
            await _append(catalog, record)
            with pytest.raises(CursorInvalid):
                await access.workspace_records(_credential('viewer'), 'wa', 'ctx_viewer', kind='result', limit=1,
                                               cursor=first['next_cursor'])
            fresh = await access.workspace_records(_credential('viewer'), 'wa', 'ctx_viewer', kind='result', limit=1)
            check = access.checkpoint(_credential('viewer'), 'wa', project, record_id=result)
            await manager.set_grant(credential_digest=_credential('owner'), workspace_id='wa', project_id=project,
                user_id='viewer', actions=['read', 'export'], expected_revision=1)
            with pytest.raises(CursorInvalid):
                await access.workspace_records(_credential('viewer'), 'wa', 'ctx_viewer', kind='result', limit=1,
                                               cursor=fresh['next_cursor'])
            await manager.set_grant(credential_digest=_credential('owner'), workspace_id='wa', project_id=project,
                user_id='viewer', actions=[], expected_revision=2)
            with pytest.raises(AuthorizationDenied):
                await check()
            with pytest.raises(AuthorizationDenied):
                await access.result_items(_credential('viewer'), 'wa', result, 'ctx_viewer', kind='claim')
    _run(run())


def test_atomic_publication_and_owner_rebuild_preserve_canonical_records_on_interrupt(tmp_path, monkeypatch):
    async def run():
        async with _database() as (pool, schema):
            _, _, catalog, _, result, _ = await _setup(pool, schema, tmp_path)
            access = _access(catalog)
            _, view = await access.result(_credential('viewer'), 'wa', result)
            record, _ = _expanded_record(view, 3)
            with pytest.raises(RuntimeError, match='publication interruption'):
                await _append(catalog, record, interrupt=True)
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k10_result_views WHERE result_id=%s", (record.record_id,))).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_id=%s", (record.record_id,))).fetchone() == (0,)
            await _append(catalog, record)
            async with pool.connection() as conn:
                before = await (await conn.execute('SELECT canonical_sha256,object_sha256 FROM k05_records '
                    'WHERE workspace_id=%s AND record_id=%s', ('wa', record.record_id))).fetchone()
                for table in ('k10_evidence_links', 'k10_result_items', 'k10_result_views'):
                    await conn.execute('DELETE FROM ' + table + ' WHERE workspace_id=%s', ('wa',))
            with pytest.raises(ProjectionUnavailable):
                await access.result_items(_credential('viewer'), 'wa', record.record_id, 'ctx_viewer', kind='claim')
            import aletheia_lab.hosted.result_views as module

            original = module.publish_projection

            async def interrupted(*args):
                await original(*args)
                raise RuntimeError('synthetic rebuild interruption')

            monkeypatch.setattr(module, 'publish_projection', interrupted)
            with pytest.raises(RuntimeError, match='rebuild interruption'):
                await rebuild_result_views(catalog, schema, 'wa', limit=1)
            async with pool.connection() as conn:
                assert await (await conn.execute('SELECT count(*) FROM k10_result_views')).fetchone() == (0,)
                assert await (await conn.execute('SELECT count(*) FROM k10_result_items')).fetchone() == (0,)
            monkeypatch.setattr(module, 'publish_projection', original)
            after, examined = '', 0
            while True:
                page = await rebuild_result_views(catalog, schema, 'wa', after=after, limit=1)
                examined += page.examined
                if page.next_after is None:
                    break
                after = page.next_after
            assert examined >= 3
            page = await access.result_items(_credential('viewer'), 'wa', record.record_id, 'ctx_viewer', kind='evidence')
            assert page['total'] == len(view.evidence) + 3
            async with pool.connection() as conn:
                assert await (await conn.execute('SELECT canonical_sha256,object_sha256 FROM k05_records '
                    'WHERE workspace_id=%s AND record_id=%s', ('wa', record.record_id))).fetchone() == before
                await conn.execute("UPDATE k10_result_items SET payload=payload||' ' WHERE workspace_id='wa' AND result_id=%s", (record.record_id,))
            with pytest.raises(CatalogIntegrityError):
                await access.result_items(_credential('viewer'), 'wa', record.record_id, 'ctx_viewer', kind='claim')
            await rebuild_result_views(catalog, schema, 'wa', limit=100, replace=True)
            assert (await access.result_items(_credential('viewer'), 'wa', record.record_id, 'ctx_viewer', kind='claim'))['items']
    _run(run())


def test_strict_actor_indexes_reject_partial_or_malformed_extension_without_fallback(tmp_path):
    async def run():
        async with _database() as (pool, schema):
            _, _, catalog, _, _, _ = await _setup(pool, schema, tmp_path)
            access = _access(catalog)
            assert await access.paged_ready()
            async with pool.connection() as conn:
                await conn.execute('ALTER TABLE k10_read_indexes RENAME TO k10_read_indexes_saved')
                await conn.execute('CREATE VIEW k10_read_indexes AS SELECT version,checksum FROM k10_read_indexes_saved')
            assert not await access.paged_ready()
            async with pool.connection() as conn:
                assert not await verify_policy(conn, schema) and not await verify_persistence(conn, schema)
                await conn.execute('DROP VIEW k10_read_indexes')
                await conn.execute('ALTER TABLE k10_read_indexes_saved RENAME TO k10_read_indexes')
                assert await verified_extension(conn)
            async with pool.connection() as conn:
                await conn.execute('DROP INDEX k10_project_grants_reader')
                await conn.execute("CREATE INDEX k10_project_grants_reader ON j01_project_grants "
                    "USING btree (workspace_id,user_id) WHERE ('read'::text = ANY(actions))")
            assert not await access.paged_ready()
            async with pool.connection() as conn:
                with pytest.raises(ReadIndexesRejected):
                    await verified_extension(conn)
                assert not await verify_policy(conn, schema)
                assert not await verify_persistence(conn, schema)
            with pytest.raises(ReadIndexesRejected):
                await catalog.list_workspace_records(credential_digest=_credential('viewer'), workspace_id='wa')
            async with pool.connection() as conn:
                await conn.execute('DROP INDEX k10_project_grants_reader')
            assert not await access.paged_ready()
            async with pool.connection() as conn:
                with pytest.raises(ReadIndexesRejected):
                    await verified_extension(conn)
                await conn.execute("CREATE INDEX k10_project_grants_reader ON j01_project_grants "
                    "USING btree (workspace_id,user_id,project_id) WHERE ('read'::text = ANY(actions))")
                assert await verified_extension(conn)
                assert await verify_policy(conn, schema) and await verify_persistence(conn, schema)
            assert await access.paged_ready()
    _run(run())


def test_policy_before_k10_upgrade_installs_indexes_without_rewriting_historical_checksums(monkeypatch):
    from test_k05_migrations_postgres import _database as blank_database

    import aletheia_lab.hosted.persistence_migration as persistence
    from aletheia_lab.hosted.project_policy_migration import migrate as migrate_policy

    async def run():
        async with blank_database() as (pool, schema):
            current = persistence.MIGRATIONS
            monkeypatch.setattr(persistence, 'MIGRATIONS', current[:12])
            async with pool.connection() as conn:
                assert await persistence.migrate(conn, schema) == 'created'
                assert await migrate_policy(conn, schema) == 'created'
                assert not await verified_extension(conn)
                historical = await (await conn.execute('SELECT version,checksum FROM k05_persistence_migrations '
                                                       'ORDER BY version')).fetchall()
            monkeypatch.setattr(persistence, 'MIGRATIONS', current)
            async with pool.connection() as conn:
                assert await persistence.migrate(conn, schema) == 'upgraded'
                assert await verified_extension(conn)
                assert await verify_policy(conn, schema) and await persistence.verify(conn, schema)
                assert await (await conn.execute('SELECT version,checksum FROM k05_persistence_migrations '
                    'WHERE version<=12 ORDER BY version')).fetchall() == historical
    _run(run())


def test_real_native_result_publisher_commits_projection_and_fence_failure_rolls_it_back(tmp_path):
    from test_k05_native_results_postgres import _auth, _case, _initial

    async def run():
        async with _case(tmp_path) as case:
            snapshot = await case.uploads.prepare_snapshot(case.snapshot)
            result = await case.results.prepare_initial_result('a', snapshot=case.snapshot, record=case.initial)
            with pytest.raises(RuntimeError, match='synthetic later fence'):
                async with case.pool.connection() as conn:
                    published = await case.uploads.enlist_snapshot(conn, authorization=await _auth(conn, case, 'confirm'), prepared=snapshot)
                    await case.results.enlist_result(conn, authorization=await _auth(conn, case), prepared=result,
                                                     expected_revision=published.revision)
                    raise RuntimeError('synthetic later fence')
            async with case.pool.connection() as conn:
                assert await (await conn.execute('SELECT count(*) FROM k10_result_views')).fetchone() == (0,)
                assert await (await conn.execute('SELECT count(*) FROM k10_result_items')).fetchone() == (0,)
            await _initial(case)
            access = _access(case.catalog)
            header = ResultHeader.model_validate(await access.result_header(case.a, 'a', case.initial.record_id))
            assert header.result == case.view.result and header.claim_count == len(case.view.claims)
            assert header.evidence_count == len(case.view.evidence)
    _run(run())


def test_owner_rebuild_rejects_parent_index_drift_before_publishing_read_model(tmp_path):
    async def run():
        async with _database() as (pool, schema):
            _, _, catalog, project, result, _ = await _setup(pool, schema, tmp_path)
            async with pool.connection() as conn:
                parents = await (await conn.execute('SELECT relation,parent_id FROM k05_record_parents '
                    'WHERE workspace_id=%s AND record_id=%s', ('wa', result))).fetchall()
                canonical = await (await conn.execute('SELECT canonical_sha256,object_sha256 FROM k05_records '
                    'WHERE workspace_id=%s AND record_id=%s', ('wa', result))).fetchone()
                assert parents
                for table in ('k10_evidence_links', 'k10_result_items', 'k10_result_views'):
                    await conn.execute('DELETE FROM ' + table + ' WHERE workspace_id=%s AND result_id=%s', ('wa', result))
                await conn.execute('DELETE FROM k05_record_parents WHERE workspace_id=%s AND record_id=%s', ('wa', result))
            with pytest.raises(CatalogIntegrityError):
                await rebuild_result_views(catalog, schema, 'wa', limit=100, replace=True)
            async with pool.connection() as conn:
                assert await (await conn.execute('SELECT count(*) FROM k10_result_views WHERE workspace_id=%s '
                    'AND result_id=%s', ('wa', result))).fetchone() == (0,)
                assert await (await conn.execute('SELECT canonical_sha256,object_sha256 FROM k05_records '
                    'WHERE workspace_id=%s AND record_id=%s', ('wa', result))).fetchone() == canonical
                for relation, parent in parents:
                    await conn.execute('INSERT INTO k05_record_parents(workspace_id,project_id,record_id,relation,parent_id) '
                        'VALUES(%s,%s,%s,%s,%s)', ('wa', project, result, relation, parent))
            await rebuild_result_views(catalog, schema, 'wa', limit=100, replace=True)
            assert (await _access(catalog).result_header(_credential('viewer'), 'wa', result))['result_id'] == result
    _run(run())
