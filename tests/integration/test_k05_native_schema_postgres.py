"""Direct SQL native constraints and legacy upgrade preservation, not load tests."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from test_k05_legacy_import import legacy_journey
from test_k05_migrations_postgres import _database, _run
from test_k05_native_results_postgres import _case, _initial
from test_k05_workspace_index_postgres import _v3
from test_project_mapping import _case as mapping_case
from test_project_mapping import _configuration

from aletheia_lab.hosted.legacy_import import ImportTarget, LegacyImporter, LegacySource
from aletheia_lab.hosted.native_upload_records import build_initial_upload_result
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.persistence_migration import WORKSPACE_INDEX_DDL, _signature, migrate
from aletheia_lab.hosted.upload_analysis import analyze_initial_upload
from aletheia_lab.hosted.upload_snapshot import build_upload_snapshot
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.mapping import validate_project_mapping
from aletheia_lab.project.persistence import _json_bytes

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K05_TEST_DATABASE_URL'),
    reason='Explicit K-05 PostgreSQL test URL required')]

_COLUMNS = ('workspace_id','record_id','project_id','record_type','schema_version','canonical_sha256',
            'object_sha256','snapshot_id','parent_result_id','source_id')


async def _local(case, root):
    root.mkdir()
    source_root, project, _, _ = legacy_journey(root)
    with LegacySource(source_root) as source:
        exported = source.export_project(project)
    await LegacyImporter(case.pool, case.objects).import_project(exported, ImportTarget(project, 'a', 'owner_a'),
        credential_digest=case.a, batch_id='raw-native-schema')
    async with case.pool.connection() as conn:
        snapshot = await (await conn.execute('SELECT ' + ','.join(_COLUMNS) + " FROM k05_records WHERE workspace_id='a' "
            "AND project_id=%s AND record_type='snapshot' ORDER BY record_id LIMIT 1", (project,))).fetchone()
        result = await (await conn.execute('SELECT ' + ','.join(_COLUMNS) + " FROM k05_records WHERE workspace_id='a' "
            "AND project_id=%s AND record_type='result' AND parent_result_id IS NULL ORDER BY record_id LIMIT 1", (project,))).fetchone()
    return dict(zip(_COLUMNS, snapshot, strict=True)), dict(zip(_COLUMNS, result, strict=True))


def _bad_record(base, label, **changes):
    row = dict(base)
    row.update(changes)
    prefix = 'upload-snapshot-' if row['record_type']=='upload_snapshot' else 'p3-snapshot-' if row['record_type']=='snapshot' else 'p6-result-'
    row['record_id'] = prefix + hashlib.sha256(label.encode()).hexdigest()
    return tuple(row[column] for column in _COLUMNS)


def test_native_direct_sql_source_presence_tenant_and_snapshot_prefix_guards(tmp_path):
    import psycopg

    async def run():
        async with _case(tmp_path) as case:
            await _initial(case)
            local_snapshot, local_result = await _local(case, tmp_path / 'local-copy')
            foreign = await case.sources.put(credential_digest=case.b, workspace_id='b',
                request_key=SecretStr('synthetic-foreign-source-raw'), payload=case.payload)
            async with case.pool.connection() as conn:
                snapshot = dict(zip(_COLUMNS, await (await conn.execute('SELECT ' + ','.join(_COLUMNS) +
                    " FROM k05_records WHERE workspace_id='a' AND record_id=%s", (case.snapshot.snapshot_id,))).fetchone(), strict=True))
                result = dict(zip(_COLUMNS, await (await conn.execute('SELECT ' + ','.join(_COLUMNS) +
                    " FROM k05_records WHERE workspace_id='a' AND record_id=%s", (case.initial.record_id,))).fetchone(), strict=True))
            assert snapshot['source_id'] == case.receipt.source_id and result['source_id'] is None
            assert local_snapshot['source_id'] is None
            negatives = (
                ('missing_source', _bad_record(snapshot,'missing_source',source_id=None), 'k05_native_source_presence'),
                ('result_source', _bad_record(result,'result_source',source_id=case.receipt.source_id), 'k05_native_source_presence'),
                ('local_source', _bad_record(local_snapshot,'local_source',source_id=case.receipt.source_id), 'k05_native_source_presence'),
                ('foreign_source', _bad_record(snapshot,'foreign_source',source_id=foreign.source_id), 'k05_native_source_scope'),
                ('native_p3_snapshot', _bad_record(result,'native_p3_snapshot',project_id=local_snapshot['project_id'],snapshot_id=local_snapshot['record_id']), 'k05_record_snapshot_identity_v5'),
                ('local_upload_snapshot', _bad_record(local_result,'local_upload_snapshot',project_id=snapshot['project_id'],snapshot_id=snapshot['record_id']), 'k05_record_snapshot_identity_v5'),
                ('snapshot_column', _bad_record(snapshot,'snapshot_column',project_id=local_snapshot['project_id'],snapshot_id=local_snapshot['record_id']), 'k05_record_snapshot_presence_v5'),
            )
            async with case.pool.connection() as conn:
                await conn.execute('SELECT 1')
                for label, row, constraint in negatives:
                    with pytest.raises(psycopg.IntegrityError) as caught:
                        async with conn.transaction():
                            await conn.execute('INSERT INTO k05_records(' + ','.join(_COLUMNS) + ') VALUES(' + ','.join(['%s']*10) + ')', row)
                    assert caught.value.diag.constraint_name == constraint, label
                    assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE workspace_id='a' AND record_id=%s", (row[1],))).fetchone() == (0,)
            assert await case.catalog.read_record(credential_digest=case.a, workspace_id='a', project_id=case.snapshot.mapping.project_id,
                record_id=case.snapshot.snapshot_id) == case.snapshot
    _run(run())


def _job(label, request, *, workspace='a', creator='owner_a'):
    payload = json.dumps(request, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(label.encode()).hexdigest()
    return ('raw_' + label, workspace, creator, '1'*64, digest, hashlib.sha256(payload.encode()).hexdigest(), payload, 'request_' + label)


async def _insert_job(conn, values):
    await conn.execute("INSERT INTO j01_jobs(job_id,workspace_id,creator_id,credential_digest,operation,key_hash,request_hash,request,request_id,state) "
        "VALUES(%s,%s,%s,%s,'follow_up',%s,%s,%s::jsonb,%s,'queued')", values)


def test_follow_up_direct_sql_exact_shape_types_revision_and_parent_scope(tmp_path):
    import psycopg

    async def run():
        async with _case(tmp_path) as case:
            await _initial(case)
            _, local_result = await _local(case, tmp_path / 'local-copy')
            receipt = await case.sources.put(credential_digest=case.b, workspace_id='b',
                request_key=SecretStr('synthetic-foreign-parent-raw'), payload=case.payload)
            foreign_snapshot = build_upload_snapshot(workspace_id='b',source_id=receipt.source_id,source_sha256=receipt.sha256,
                mapping=case.snapshot.mapping,mapping_result=case.snapshot.mapping_result,captured_at=case.snapshot.captured_at)
            foreign_view = analyze_initial_upload(foreign_snapshot,'What is observed in the foreign scope?')
            foreign_record = build_initial_upload_result(foreign_snapshot,foreign_view.conversation.turns[0].question,foreign_view)
            prepared_snapshot = await case.uploads.prepare_snapshot(foreign_snapshot)
            prepared_result = await case.results.prepare_initial_result('b',snapshot=foreign_snapshot,record=foreign_record)
            async with case.pool.connection() as conn:
                from aletheia_lab.hosted.authorization import PostgresAuthorization
                auth = await PostgresAuthorization().authorize(conn,case.b.get_secret_value(),'b','confirm')
                published = await case.uploads.enlist_snapshot(conn,authorization=auth,prepared=prepared_snapshot)
                auth = await PostgresAuthorization().authorize(conn,case.b.get_secret_value(),'b','analyze')
                await case.results.enlist_result(conn,authorization=auth,prepared=prepared_result,expected_revision=published.revision)
            base = dict(project_id=case.initial.project_id,parent_result_id=case.initial.record_id,expected_revision=0,
                        selection_kind='claim',selection_id=case.initial.view.claims[0].id,question='What is still uncertain?')
            malformed = []
            for key in base:
                malformed.append(('missing_' + key,{name:value for name,value in base.items() if name!=key}))
            malformed.append(('extra',{**base,'unexpected':True}))
            for index,value in enumerate((-1,1.25,'0',True,None,9223372036854775808,[],{})):
                malformed.append(('revision_' + str(index),{**base,'expected_revision':value}))
            for key,values in (
                ('project_id',(None,123)), ('parent_result_id',(None,True)),
                ('selection_kind',('result',None,123)), ('selection_id',('', 'x'*129,None,123)),
                ('question',('', 'x'*4001,None,123)),
            ):
                for index,value in enumerate(values):
                    malformed.append((key + '_' + str(index),{**base,key:value}))
            rows = [(label,_job(label,body)) for label,body in malformed]
            scopes = [
                ('other_project',_job('other_project',{**base,'parent_result_id':local_result['record_id']})),
                ('other_workspace',_job('other_workspace',{**base,'parent_result_id':foreign_record.record_id})),
            ]
            positives = [_job('positive_' + str(index),{**base,'expected_revision':value})
                         for index,value in enumerate((0,9223372036854775807))]
            positives.extend(_job('question_' + str(length), {**base, 'question': 'x' * length})
                             for length in (2001, 4000))
            async with case.pool.connection() as conn:
                await conn.execute('SELECT 1')
                for label,values in rows:
                    with pytest.raises(psycopg.errors.CheckViolation) as caught:
                        async with conn.transaction():
                            await _insert_job(conn,values)
                    assert caught.value.diag.constraint_name == 'k05_job_request_shape_v6', label
                for label,values in scopes:
                    with pytest.raises(psycopg.errors.ForeignKeyViolation) as caught:
                        async with conn.transaction():
                            await _insert_job(conn,values)
                    assert caught.value.diag.constraint_name == 'k05_follow_up_parent_scope', label
                for values in positives:
                    await _insert_job(conn,values)
                generated = await (await conn.execute("SELECT source_id,snapshot_id,canonical_project_id,parent_result_id "
                    "FROM j01_jobs WHERE job_id='raw_positive_0'")).fetchone()
                assert generated == (None,None,case.initial.project_id,case.initial.record_id)
    _run(run())


async def _v4(pool,schema):
    await _v3(pool,schema)
    async with pool.connection() as conn:
        await conn.execute(WORKSPACE_INDEX_DDL)
        await conn.execute('INSERT INTO k05_persistence_migrations(version,checksum,signature) VALUES(4,%s,%s)',
            (hashlib.sha256(WORKSPACE_INDEX_DDL.encode()).hexdigest(),await _signature(conn,schema,4)))


def test_v4_upgrade_preserves_typed_legacy_bytes_null_bridges_and_rolls_back(tmp_path):
    import io
    import zipfile

    source = tmp_path / 'source'
    source.mkdir()
    facts = mapping_case(source)
    mapping = _configuration(facts)
    mapped = validate_project_mapping(facts.bundle,facts.artifacts,facts.collection,mapping)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive,'w') as zipped:
        for name in ('dataset.csv','metrics.csv','config.json'):
            zipped.writestr(name,(source/name).read_bytes())
    payload = archive.getvalue()
    snapshot = build_upload_snapshot(workspace_id='a',source_id='source_a',source_sha256=hashlib.sha256(payload).hexdigest(),
        mapping=mapping,mapping_result=mapped,captured_at=datetime(2026,11,10,tzinfo=UTC))
    store = FilesystemObjectStore(tmp_path/'legacy-objects')
    descriptors = []
    artifacts = {artifact.relative_path: artifact for artifact in facts.artifacts}
    for item in facts.bundle.items:
        artifact = artifacts[item.relative_path]
        assert artifact.reference == item.artifact
        value = store.publish(artifact.content,media_type=item.artifact.media_type)
        assert value.sha256 == item.artifact.sha256
        descriptors.append(value)
    bundle_bytes = _json_bytes(facts.bundle.model_dump(mode='json'))
    bundle = store.publish(bundle_bytes,media_type='application/json',schema_version=facts.bundle.schema_version)
    assert store.read(bundle) == bundle_bytes

    async def run():
        async with _database() as (pool,schema):
            await _v4(pool,schema)
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_sources SET sha256=%s,payload=%s WHERE source_id='source_a'",(snapshot.source_sha256,payload))
                await conn.execute("INSERT INTO j01_upload_snapshots(snapshot_id,workspace_id,source_id,payload) VALUES(%s,'a','source_a',%s::jsonb)",
                    (snapshot.snapshot_id,snapshot.model_dump_json()))
                await conn.execute("INSERT INTO k05_projects(workspace_id,project_id,creator_id) VALUES('a',%s,'user_a')",(facts.bundle.project_id,))
                for value in (*descriptors,bundle):
                    await conn.execute("INSERT INTO k05_objects(workspace_id,sha256,byte_size,media_type,schema_version) VALUES('a',%s,%s,%s,%s)",
                        (value.sha256,value.byte_size,value.media_type,value.schema_version))
                await conn.execute("INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,canonical_sha256,object_sha256) "
                    "VALUES('a',%s,%s,'project_bundle',%s,%s,%s)",(facts.bundle.project_bundle_id,facts.bundle.project_id,facts.bundle.schema_version,
                        canonical_project_sha256(facts.bundle.model_dump(mode='json')),bundle.sha256))
                for value in descriptors:
                    await conn.execute("INSERT INTO k05_object_references VALUES('a',%s,%s)",(facts.bundle.project_bundle_id,value.sha256))
                before_source = await (await conn.execute("SELECT sha256,payload FROM j01_sources WHERE source_id='source_a'")).fetchone()
                before_snapshot = await (await conn.execute('SELECT snapshot_id,workspace_id,source_id,payload FROM j01_upload_snapshots')).fetchall()
            with pytest.raises(RuntimeError,match='injected native upgrade rollback'):
                async with pool.connection() as conn:
                    assert await migrate(conn,schema) == 'upgraded'
                    raise RuntimeError('injected native upgrade rollback')
            async with pool.connection() as conn:
                assert await (await conn.execute('SELECT max(version) FROM k05_persistence_migrations')).fetchone() == (4,)
                assert await (await conn.execute("SELECT count(*) FROM pg_attribute WHERE attrelid='k05_records'::regclass AND attname='source_id' AND NOT attisdropped")).fetchone() == (0,)
                assert await migrate(conn,schema) == 'upgraded'
                assert await (await conn.execute("SELECT sha256,payload FROM j01_sources WHERE source_id='source_a'")).fetchone() == before_source
                assert await (await conn.execute('SELECT snapshot_id,workspace_id,source_id,payload FROM j01_upload_snapshots')).fetchall() == before_snapshot
                assert await (await conn.execute('SELECT canonical_record_id FROM j01_upload_snapshots')).fetchone() == (None,)
                assert await (await conn.execute('SELECT count(*) FROM j01_jobs WHERE canonical_result_id IS NOT NULL')).fetchone() == (0,)
                assert await (await conn.execute('SELECT source_id FROM k05_records')).fetchone() == (None,)
                assert await (await conn.execute('SELECT provenance_kind FROM k05_projects WHERE project_id=%s',(facts.bundle.project_id,))).fetchone() == ('local',)
                assert await migrate(conn,schema) == 'unchanged'
            assert store.read(bundle) == bundle_bytes
    _run(run())
