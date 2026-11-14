"""Bounded PostgreSQL access-path probe over real, verified synthetic templates.

Runs only in its own UUID schema on loopback PostgreSQL. A checkpointed local
synthetic template is repeated across workspaces; this measures aggregate
catalog cardinality, not a larger within-tenant project. Object bytes are real
and shared by hash. It never flushes another task's PostgreSQL caches.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from aletheia_lab.hosted.legacy_import import (
    LegacyProject,
    LegacySource,
    model_metadata,
    record_parents,
    validate_project_models,
)
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.persistence_migration import MIGRATIONS, migrate
from aletheia_lab.product.lifecycle import ProductLifecycleRecord
from aletheia_lab.project.contracts import ProjectBundle
from aletheia_lab.project.persistence import _json_bytes
from aletheia_lab.project.snapshots import ProjectSnapshot


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template-copy', type=Path, required=True)
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--synthetic-template', action='store_true', required=True,
                        help='Affirm this template contains only generated test data')
    parser.add_argument('--database-url-env', default='K05_TEST_DATABASE_URL')
    parser.add_argument('--copies', type=int, default=10)
    parser.add_argument('--maximum-records', type=int, default=100000)
    parser.add_argument('--timeout-seconds', type=int, default=300)
    parser.add_argument('--maximum-database-bytes', type=int, default=512 * 1024 * 1024)
    parser.add_argument('--minimum-free-bytes', type=int, default=512 * 1024 * 1024,
                        help='Reserve on the output filesystem; not a remote/container disk guarantee')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--authorized-read-probe', action='store_true',
        help='Also exercise current authorized catalog/pages against one real template result before cleanup')
    value = parser.parse_args(argv)
    if (not 1 <= value.copies <= 200000 or not 1 <= value.maximum_records <= 3000000
            or not 10 <= value.timeout_seconds <= 900
            or value.maximum_database_bytes < 1024 * 1024 or value.minimum_free_bytes < 0):
        parser.error('Probe count/time budget is outside the local limit')
    if value.output.exists():
        parser.error('Output must be a new file')
    return value


def _rows(project: LegacyProject):
    validate_project_models(project.models, project.project_id)
    records = []
    objects = {}
    parents = []
    references = []
    for model in project.models:
        kind, identity, scope, version, canonical = model_metadata(model)
        payload = _json_bytes(model.model_dump(mode='json'))
        digest = hashlib.sha256(payload).hexdigest()
        objects[digest] = ('application/json', version, payload)
        records.append((identity, scope, kind, version, canonical, digest,
            model.snapshot_id if isinstance(model, ProductLifecycleRecord) else None,
            model.parent_result_id if isinstance(model, ProductLifecycleRecord) else None))
        parents.extend((identity, relation, parent) for relation, parent in record_parents(model))
        if isinstance(model, ProjectBundle):
            references.extend((identity, item.artifact.sha256) for item in model.items)
    for digest, (media, payload) in project.artifacts.items():
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError('Template artifact hash differs')
        objects[digest] = (media, None, payload)
    if not any(isinstance(model, ProjectSnapshot) for model in project.models):
        raise ValueError('Template requires a canonical snapshot')
    if not any(isinstance(model, ProductLifecycleRecord) and model.record_kind == 'result'
               for model in project.models):
        raise ValueError('Template requires a validated ProductView result')
    return records, objects, sorted(set(parents)), sorted(set(references))


def query_profile(project: LegacyProject, *, snapshot_limit: int | None = None) -> LegacyProject:
    """Retain snapshots/results and their canonical parents for a metadata probe."""
    by_id = {model_metadata(model)[1]: model for model in project.models}
    snapshots = sorted(model.snapshot_id for model in project.models if isinstance(model, ProjectSnapshot))
    if snapshot_limit is not None:
        snapshots = snapshots[:snapshot_limit]
    selected = {model_metadata(model)[1] for model in project.models if isinstance(model, ProjectSnapshot)
                and model.snapshot_id in snapshots or isinstance(model, ProductLifecycleRecord)
                and model.record_kind == 'result' and model.snapshot_id in snapshots}
    pending = list(selected)
    while pending:
        for _, parent in record_parents(by_id[pending.pop()]):
            if parent not in selected:
                selected.add(parent)
                pending.append(parent)
    models = tuple(by_id[key] for key in sorted(selected))
    return LegacyProject(project.project_id, models, project.artifacts, (), (), ())


async def _copy(conn, statement, values):
    async with conn.cursor() as cursor, cursor.copy(statement) as copy:
        for value in values:
            await copy.write_row(value)


async def prepare_objects(project, store):
    """Verify filesystem objects with no database connection or transaction held."""
    records, objects, parents, refs = _rows(project)
    # File publication and hash verification happen before opening metadata writes.
    descriptors = []
    for digest, (media, version, payload) in objects.items():
        descriptor = await asyncio.to_thread(store.publish, payload, media_type=media, schema_version=version)
        if descriptor.sha256 != digest or await asyncio.to_thread(store.read, descriptor) != payload:
            raise ValueError('Template object verification failed')
        descriptors.append(descriptor)
    return records, objects, parents, refs, descriptors


async def seed(conn, project, prepared, copies, *, prefix="probe_", start=1):
    """Bulk-copy validated canonical metadata into this already-owned schema."""
    records, objects, parents, refs, descriptors = prepared
    await conn.execute("INSERT INTO hosted_identity_bindings VALUES('https://probe.example.invalid','synthetic',"
        "'probe_user','Synthetic','probe@example.invalid',true,true,0,'ready',NULL,NULL) ON CONFLICT DO NOTHING")
    await conn.execute('CREATE TEMP TABLE k05_probe_scopes(workspace_id text PRIMARY KEY) ON COMMIT DROP')
    await conn.execute("INSERT INTO k05_probe_scopes SELECT %s||lpad(n::text,8,'0') FROM generate_series(%s::integer,%s::integer) n", (prefix, start, start + copies - 1))
    await conn.execute("INSERT INTO j01_workspaces SELECT workspace_id,'Synthetic query probe' FROM k05_probe_scopes")
    await conn.execute("INSERT INTO j01_memberships SELECT workspace_id,'probe_user','owner',true,0 FROM k05_probe_scopes")
    await conn.execute("INSERT INTO k05_projects(workspace_id,project_id,creator_id) SELECT workspace_id,%s,'probe_user' FROM k05_probe_scopes",
                       (project.project_id,))
    await conn.execute('CREATE TEMP TABLE k05_probe_objects(sha256 text,byte_size bigint,media_type text,schema_version text) ON COMMIT DROP')
    await _copy(conn, 'COPY k05_probe_objects FROM STDIN',
        ((value.sha256, value.byte_size, value.media_type, value.schema_version) for value in descriptors))
    await conn.execute('INSERT INTO k05_objects(workspace_id,sha256,byte_size,media_type,schema_version) '
        'SELECT s.workspace_id,o.* FROM k05_probe_scopes s CROSS JOIN k05_probe_objects o')
    await conn.execute('CREATE TEMP TABLE k05_probe_records(record_id text,project_id text,record_type text,schema_version text,'
        'canonical_sha256 text,object_sha256 text,snapshot_id text,parent_result_id text) ON COMMIT DROP')
    await _copy(conn, 'COPY k05_probe_records FROM STDIN', records)
    await conn.execute('INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,canonical_sha256,'
        'object_sha256,snapshot_id,parent_result_id) SELECT s.workspace_id,r.* FROM k05_probe_scopes s CROSS JOIN k05_probe_records r')
    await conn.execute('CREATE TEMP TABLE k05_probe_parents(record_id text,relation text,parent_id text) ON COMMIT DROP')
    await _copy(conn, 'COPY k05_probe_parents FROM STDIN', parents)
    await conn.execute('INSERT INTO k05_record_parents SELECT s.workspace_id,%s,p.* '
        'FROM k05_probe_scopes s CROSS JOIN k05_probe_parents p', (project.project_id,))
    await conn.execute('CREATE TEMP TABLE k05_probe_refs(record_id text,sha256 text) ON COMMIT DROP')
    await _copy(conn, 'COPY k05_probe_refs FROM STDIN', refs)
    await conn.execute('INSERT INTO k05_object_references SELECT s.workspace_id,r.* FROM k05_probe_scopes s CROSS JOIN k05_probe_refs r')
    # Historical/current pointers are not required by these list/read queries;
    # this seed intentionally does not represent an imported production project.
    return {'template_records': len(records), 'template_objects': len(objects),
            'physical_object_bytes': sum(len(value[2]) for value in objects.values()),
            'aggregate_records_expected': len(records) * copies,
            'snapshots_expected': sum(row[2] == 'snapshot' for row in records) * copies,
            'results_expected': sum(row[2] == 'result' for row in records) * copies}


def _nodes(plan):
    value = {key: plan[key] for key in ('Node Type', 'Relation Name', 'Index Name', 'Actual Rows',
        'Actual Loops', 'Rows Removed by Filter', 'Rows Removed by Index Recheck') if key in plan}
    value['children'] = [_nodes(child) for child in plan.get('Plans', [])]
    return value


async def authorized_read_probe(database, schema, object_root, project_id):
    """One synthetic owner/session and one real rebuilt result; never a load/IdP test."""
    from psycopg.conninfo import make_conninfo
    from psycopg_pool import AsyncConnectionPool
    from pydantic import SecretStr

    from aletheia_lab.hosted.authorization import AuthorizationDenied
    from aletheia_lab.hosted.catalog_access import CatalogAccess
    from aletheia_lab.hosted.cursors import ScopedCursors
    from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
    from aletheia_lab.hosted.postgres_objects import PostgresObjects
    from aletheia_lab.hosted.read_indexes import verified_extension
    from aletheia_lab.hosted.result_views import rebuild_result_views
    from aletheia_lab.hosted_contract.workspaces import (
        ClaimPage,
        EvidencePage,
        EvidenceView,
        GraphView,
        ProjectPage,
        RecordPage,
        ResultHeader,
    )

    workspace, context = 'probe_00000001', 'ctx_synthetic_k10_probe'
    digest = SecretStr(hashlib.sha256(b'synthetic-k10-read-probe-credential').hexdigest())
    scoped = make_conninfo(database, options='-csearch_path=' + schema + ',pg_catalog',
                           application_name='aletheia-k10-authorized-probe')
    report = {'scope': 'Serial in-process actual CatalogAccess/PostgresCatalog with one synthetic owner/session; '
        'no HTTP/browser/IdP, concurrent-user, all-results projection or cold-cache claim.',
        'workspace_id': workspace, 'project_id': project_id, 'projection_backfill_limit': 1,
        'page_limit': 7, 'graph_node_limit': 3, 'maximum_serialized_response_bytes': 1048576, 'reads': {}}

    async with AsyncConnectionPool(scoped, min_size=1, max_size=2, open=False) as pool:
        await pool.wait(timeout=10)
        async with pool.connection() as conn:
            if not await verified_extension(conn):
                raise ValueError('Current read index extension is not ready')
            await conn.execute("INSERT INTO j01_sessions(context_id,user_id,csrf_token,state,issued_at,last_seen_at,"
                "rotated_at,idle_expires_at,absolute_expires_at,generation,session_epoch) VALUES(%s,'probe_user',"
                "'synthetic-csrf','active',statement_timestamp(),statement_timestamp(),statement_timestamp(),"
                "statement_timestamp()+interval '900 seconds',statement_timestamp()+interval '3600 seconds',0,0)", (context,))
            await conn.execute("INSERT INTO j01_session_credentials(digest,context_id,state,generation) VALUES(%s,%s,'active',0)",
                               (digest.get_secret_value(), context))
        objects = PostgresObjects(pool, FilesystemObjectStore(object_root))
        catalog = PostgresCatalog(pool, objects)
        access = CatalogAccess(catalog, ScopedCursors(SecretStr('synthetic-k10-query-cursor-' + 'x' * 40)), None)
        rebuilt = await rebuild_result_views(catalog, schema, workspace, limit=1)
        if rebuilt.examined != 1 or not await access.paged_ready():
            raise ValueError('Selected canonical result projection failed')

        async def measured(name, model, call):
            started = time.perf_counter()
            value = await call
            checked = model.model_validate(value)
            if model is ProjectPage:
                for item in checked.items:
                    await access.checkpoint(digest, workspace, item.project_id)()
            elif model is RecordPage:
                await catalog.check_records(credential_digest=digest, workspace_id=workspace,
                                            record_ids=tuple(item.record_id for item in checked.items))
            else:
                await access.checkpoint(digest, workspace, project_id, record_id=checked.result_id)()
            size = len(checked.model_dump_json().encode())
            if size > report['maximum_serialized_response_bytes']:
                raise ValueError('Authorized response exceeded its declared bound')
            report['reads'].setdefault(name, []).append({'seconds': time.perf_counter() - started,
                'serialized_bytes': size, 'model_validated': True})
            return checked

        # Backfill is explicit and complete; subsequent pages must not open a full object.
        async def forbidden(*args, **kwargs):
            raise ValueError('Paged read opened a full canonical object')

        objects.read = forbidden
        projects = await measured('projects', ProjectPage, access.projects(digest, workspace, context, limit=7))
        if [item.project_id for item in projects.items] != [project_id]:
            raise ValueError('Authorized project page scope mismatch')
        results = await measured('results', RecordPage, access.workspace_records(digest, workspace, context, kind='result', limit=7))
        if not results.items or any(item.project_id != project_id for item in results.items):
            raise ValueError('Authorized result page scope mismatch')
        result = results.items[0].record_id
        report['result_id'] = result
        header = await measured('header', ResultHeader, access.result_header(digest, workspace, result))
        for kind, model, count in [('claim', ClaimPage, header.claim_count), ('evidence', EvidencePage, header.evidence_count)]:
            cursor, identities = None, []
            for _ in range(256):
                page = await measured(kind + '_pages', model, access.result_items(digest, workspace, result, context,
                    kind=kind, limit=7, cursor=cursor))
                identities.extend(item.id for item in page.items)
                if len(page.items) > 7 or page.total != count or page.total_kind != 'exact':
                    raise ValueError('Authorized page bound/count mismatch')
                cursor = page.next_cursor
                if cursor is None:
                    break
            else:
                raise ValueError('Authorized page continuation did not terminate')
            if len(identities) != count or len(set(identities)) != count:
                raise ValueError('Authorized result pages omitted/duplicated data')
            report[kind + '_items'] = count
        cursor, nodes, edges = None, set(), set()
        for _ in range(256):
            graph = await measured('graph_pages', GraphView, access.result_graph(digest, workspace, result, context, limit=3, cursor=cursor))
            if len(graph.graph.nodes) > 3 or len(graph.graph.edges) > 1000:
                raise ValueError('Authorized graph page exceeded its bound')
            nodes.update(node.id for node in graph.graph.nodes)
            edges.update(edge.id for edge in graph.graph.edges)
            cursor = graph.next_cursor
            if cursor is None:
                break
        else:
            raise ValueError('Authorized graph continuation did not terminate')
        if (len(nodes), len(edges)) != (header.node_count, header.edge_count):
            raise ValueError('Authorized graph continuation lost data')
        report.update(graph_nodes=len(nodes), graph_edges=len(edges), canonical_object_reads_after_backfill=0)
        first = await access.result_items(digest, workspace, result, context, kind='claim', limit=1)
        if first['items'] and first['items'][0].citation_ids:
            claim = first['items'][0]
            await measured('evidence_detail', EvidenceView, access.result_evidence(digest, workspace, result, claim.citation_ids[0], claim.id))
        try:
            await access.result_header(digest, 'not_in_probe', result)
        except AuthorizationDenied:
            report['outside_workspace_denied'] = True
        else:
            raise ValueError('Outside-workspace read unexpectedly succeeded')
        async with pool.connection() as conn:
            report['catalog_counts_observed_after_reads'] = dict(await (await conn.execute(
                'SELECT record_type,count(*) FROM k05_records GROUP BY record_type')).fetchall())
            report['selected_projection_rows'] = (await (await conn.execute('SELECT count(*) FROM k10_result_views')).fetchone())[0]
        report['pool_stats'] = pool.get_stats()
    report['status'] = 'passed'
    return report


async def plans(conn, project_id, *, workspace_id="probe_00000001", repetitions=5):
    snapshot = (await (await conn.execute("SELECT record_id FROM k05_records WHERE workspace_id=%s "
        "AND project_id=%s AND record_type='snapshot' ORDER BY record_id LIMIT 1", (workspace_id, project_id,))).fetchone())[0]
    probes = (
        ('project_page', 'SELECT project_id,revision FROM k05_projects WHERE workspace_id=%s AND project_id>%s ORDER BY project_id LIMIT 50',
         (workspace_id, '')),
        ('snapshot_page', "SELECT record_id,canonical_sha256,object_sha256 FROM k05_records WHERE workspace_id=%s "
         "AND project_id=%s AND record_type='snapshot' AND record_id>%s ORDER BY record_id LIMIT 51",
         (workspace_id, project_id, '')),
        ('result_page', "SELECT record_id,canonical_sha256,object_sha256 FROM k05_records WHERE workspace_id=%s "
         "AND project_id=%s AND record_type='result' AND record_id>%s ORDER BY record_id LIMIT 51",
         (workspace_id, project_id, '')),
        ('logical_result_page', "SELECT record_id,canonical_sha256,object_sha256 FROM k05_records WHERE workspace_id=%s "
         "AND project_id=%s AND record_type=ANY(%s) AND record_id>%s ORDER BY record_id LIMIT 51",
         (workspace_id, project_id, ['result', 'upload_result'], '')),
        ('logical_workspace_result_page', "SELECT record_id,canonical_sha256,object_sha256 FROM k05_records WHERE workspace_id=%s "
         "AND record_type=ANY(%s) AND record_id>%s ORDER BY record_id LIMIT 51",
         (workspace_id, ['result', 'upload_result'], '')),
        ('snapshot_result_page', "SELECT record_id,canonical_sha256,object_sha256 FROM k05_records WHERE workspace_id=%s "
         "AND project_id=%s AND snapshot_id=%s AND record_type='result' AND record_id>%s ORDER BY record_id LIMIT 51",
         (workspace_id, project_id, snapshot, '')),
        ('record_and_object', 'SELECT r.record_id,r.record_type,r.canonical_sha256,o.byte_size,o.media_type '
         'FROM k05_records r JOIN k05_objects o ON o.workspace_id=r.workspace_id AND o.sha256=r.object_sha256 '
         'WHERE r.workspace_id=%s AND r.project_id=%s AND r.record_id=%s', (workspace_id, project_id, snapshot)),
        ('parents', 'SELECT relation,parent_id FROM k05_record_parents WHERE workspace_id=%s AND project_id=%s AND record_id=%s '
         'ORDER BY relation,parent_id', (workspace_id, project_id, snapshot)),
    )
    result = []
    for name, query, params in probes:
        measurements = []
        for _ in range(repetitions):
            row = await (await conn.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) ' + query, params)).fetchone()
            facts = row[0][0]
            measurements.append(facts)
        result.append({'query': name, 'data_statements_per_execution': 1,
            'first_observed_cache_state': 'unknown; migration/seed/analyze have already touched buffers',
            'cold_run': 'not_run; shared PostgreSQL caches were not flushed',
            'warm_execution_ms_median': statistics.median(value['Execution Time'] for value in measurements[1:]),
            'plans': measurements, 'rows_by_node': _nodes(measurements[-1]['Plan'])})
    return result


def checkpoint(args, report):
    temporary = args.output.with_suffix(args.output.suffix + '.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    temporary.replace(args.output)


def output_free_bytes(args):
    free = shutil.disk_usage(args.output.parent).free
    if free < args.minimum_free_bytes:
        raise ValueError('Output filesystem reserve is unavailable')
    return free


async def check_budget(conn, args, report, *, schema):
    size = (await (await conn.execute('SELECT coalesce(sum(pg_total_relation_size(c.oid)),0)::bigint '
        'FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind=\'r\'',
        (schema,))).fetchone())[0]
    free = output_free_bytes(args)
    report.update(catalog_bytes=size, output_filesystem_free_bytes=free)
    if size > args.maximum_database_bytes:
        raise ValueError('Probe database/output filesystem budget exhausted')


async def execute(args):
    started = time.monotonic()
    import psycopg
    from psycopg import sql

    database = os.environ.get(args.database_url_env, '')
    parsed = urlsplit(database)
    if parsed.scheme != 'postgresql' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Explicit loopback PostgreSQL test URL required')
    with LegacySource(args.template_copy) as source:
        project = source.export_project(args.project_id)
    project = query_profile(project)
    small = query_profile(project, snapshot_limit=2)
    records, _, _, _ = _rows(project)
    if len(records) * args.copies + len(small.models) > args.maximum_records:
        raise ValueError('Template copies exceed the explicit aggregate record budget')
    schema = 'k05_probe_' + uuid4().hex
    args.output.parent.mkdir(parents=True, exist_ok=True)
    owned_objects = tempfile.TemporaryDirectory(prefix=args.output.stem + '-objects-', dir=args.output.parent)
    object_root = Path(owned_objects.name)
    report = {'format': 'k05-query-probe/v1', 'schema': schema,
        'migration_checksums': [hashlib.sha256(ddl.encode()).hexdigest() for ddl in MIGRATIONS],
        'template_fingerprint': project.fingerprint, 'copies': args.copies,
        'catalog_target': {'snapshots': 200000, 'results': 2000000},
        'per_tenant_records': len(records), 'physical_object_root': str(object_root),
        'limits': {'maximum_records': args.maximum_records, 'timeout_seconds': args.timeout_seconds,
                   'maximum_database_bytes': args.maximum_database_bytes, 'minimum_free_bytes': args.minimum_free_bytes,
                   'database_budget_check': 'after each committed batch; at most 10000 metadata rows or one template'},
        'scope': 'metadata access paths; no HTTP/auth/pool/fairness/load capacity claim',
        'timeout_scope': 'database phase; elapsed_seconds includes source validation, object preparation and cleanup',
        'status': 'running',
        'tenant_strata': {'large_template_records': len(records), 'small_template_records': len(small.models), 'small_snapshot_limit': 2}}
    checkpoint(args, report)
    try:
        output_free_bytes(args)
        store = FilesystemObjectStore(object_root)
        prepared = await prepare_objects(project, store)
        small_prepared = await prepare_objects(small, store)
        async with await psycopg.AsyncConnection.connect(database, autocommit=True, connect_timeout=10) as admin:
            await admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            try:
                async with await psycopg.AsyncConnection.connect(database, connect_timeout=10) as conn:
                    async with asyncio.timeout(args.timeout_seconds):
                        async with conn.transaction():
                            await conn.execute(sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(sql.Identifier(schema)))
                            await conn.execute("SET LOCAL lock_timeout='3s'")
                            await conn.execute("SELECT set_config('statement_timeout',%s,true)", (str(args.timeout_seconds * 1000),))
                            report['migration'] = await migrate(conn, schema)
                            if args.authorized_read_probe:
                                from aletheia_lab.hosted.project_policy_migration import (
                                    migrate as migrate_policy,
                                )

                                report['authorized_policy_migration'] = await migrate_policy(conn, schema)
                        report['phase'] = 'seeding'
                        await check_budget(admin, args, report, schema=schema)
                        checkpoint(args, report)
                        # Bound deferred FK events and disk growth between budget checks.
                        batch_copies = min(100, max(1, 10000 // len(records)))
                        for start in range(1, args.copies + 1, batch_copies):
                            amount = min(batch_copies, args.copies - start + 1)
                            async with conn.transaction():
                                await conn.execute(sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(sql.Identifier(schema)))
                                await conn.execute("SELECT set_config('statement_timeout',%s,true)", (str(args.timeout_seconds * 1000),))
                                report['seed'] = await seed(conn, project, prepared, amount, start=start)
                            report['copies_committed'] = start + amount - 1
                            await check_budget(admin, args, report, schema=schema)
                            checkpoint(args, report)
                            print('K05_QUERY_PROBE_COPIES=' + str(start + amount - 1) + '/' + str(args.copies), flush=True)
                        report['seed']['aggregate_records_expected'] = len(records) * args.copies
                        report['seed']['snapshots_expected'] = sum(row[2] == 'snapshot' for row in records) * args.copies
                        report['seed']['results_expected'] = sum(row[2] == 'result' for row in records) * args.copies
                        async with conn.transaction():
                            await conn.execute(sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(sql.Identifier(schema)))
                            report['small_seed'] = await seed(conn, small, small_prepared, 1, prefix='small_')
                        await check_budget(admin, args, report, schema=schema)
                        report['phase'] = 'querying'
                        checkpoint(args, report)
                        async with conn.transaction():
                            await conn.execute(sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(sql.Identifier(schema)))
                            for table in ('k05_projects', 'k05_records', 'k05_record_parents', 'k05_objects'):
                                await conn.execute('ANALYZE ' + table)
                            report['counts'] = dict(await (await conn.execute('SELECT record_type,count(*) FROM k05_records GROUP BY record_type')).fetchall())
                            if sum(report['counts'].values()) != len(records) * args.copies + len(small.models):
                                raise ValueError('Actual imported record count differs from declared profile')
                            report['plans'] = {'large': await plans(conn, project.project_id),
                                               'small': await plans(conn, project.project_id, workspace_id='small_00000001')}
                            report['catalog_bytes'] = (await (await conn.execute('SELECT sum(pg_total_relation_size(c.oid))::bigint '
                                'FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind=\'r\'', (schema,))).fetchone())[0]
                            report['pool_wait'] = 'not_measured; direct single connection'
                        if args.authorized_read_probe:
                            report['phase'] = 'authorized_reads'
                            checkpoint(args, report)
                            report['authorized_reads'] = await authorized_read_probe(database, schema, object_root, project.project_id)
                            if report['authorized_reads']['catalog_counts_observed_after_reads'] != report['counts']:
                                raise ValueError('Canonical catalog changed during authorized read probe')
                            await check_budget(admin, args, report, schema=schema)
                            checkpoint(args, report)
                report['target_imported'] = report['counts'].get('snapshot', 0) >= 200000 and report['counts'].get('result', 0) >= 2000000
                report['status'] = 'passed'
            except BaseException as error:
                report['status'] = 'failed'
                report['failure_category'] = type(error).__name__
                report['target_imported'] = False
                try:
                    await admin.execute("SET statement_timeout='5s'")
                    projects = (await (await admin.execute(sql.SQL('SELECT count(*) FROM {}.k05_projects').format(sql.Identifier(schema)))).fetchone())[0]
                    sample = dict(await (await admin.execute(sql.SQL("SELECT record_type,count(*) FROM {}.k05_records "
                        "WHERE workspace_id='probe_00000001' GROUP BY record_type").format(sql.Identifier(schema)))).fetchall())
                    report['failure_checkpoint'] = {'projects_committed': projects, 'one_tenant_counts_committed': sample,
                        'aggregate_count_status': 'not_measured; no extrapolated cardinality acceptance'}
                except Exception as observation_error:
                    report['failure_observation_category'] = type(observation_error).__name__
                raise
            finally:
                try:
                    assert schema.startswith('k05_probe_') and len(schema) == 42
                    await admin.execute("SET statement_timeout='30s'")
                    await admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
                    report['owned_schema_removed'] = True
                except BaseException as cleanup_error:
                    report['status'] = 'failed'
                    report['owned_schema_removed'] = False
                    report['cleanup_failure_category'] = type(cleanup_error).__name__
                    raise
                finally:
                    report['elapsed_seconds'] = time.monotonic() - started
                    checkpoint(args, report)
    except BaseException as error:
        report.update(status='failed', failure_category=type(error).__name__, target_imported=False)
        raise
    finally:
        owned_objects.cleanup()
        report['owned_objects_removed'] = not object_root.exists()
        report['elapsed_seconds'] = time.monotonic() - started
        checkpoint(args, report)
    return report


def main(argv=None):
    args = arguments(argv)
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else None) as runner:
            report = runner.run(execute(args))
    except Exception as error:
        print('K05_QUERY_PROBE_FAILED=' + type(error).__name__, file=sys.stderr)
        return 1
    print('K05_QUERY_PROBE_RECORDS=' + str(sum(report['counts'].values())))
    print('K05_QUERY_PROBE_TARGET_IMPORTED=' + str(report['target_imported']))
    print('K05_QUERY_PROBE_OWNED_SCHEMA_REMOVED=' + str(report['owned_schema_removed']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

