"""New upload intents for identical ZIP bytes must not poison confirm jobs."""
from __future__ import annotations

import io
import os
import zipfile

import pytest
from pydantic import SecretStr
from test_k05_native_results_postgres import _case, _demo_mapping, _initial, _run
from test_k09_execution_postgres import _jobs

from aletheia_lab.hosted.postgres_sources import SourceNotFound
from aletheia_lab.hosted.preview_leases import PreviewLeases
from aletheia_lab.hosted.preview_migration import migrate as migrate_preview
from aletheia_lab.hosted.upload_project import UploadProject
from aletheia_lab.hosted.workspace_management import WorkspaceConflict

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K07_TEST_DATABASE_URL'),
    reason='Explicit private K07 PostgreSQL URL required')]


@pytest.mark.parametrize('preview_guard', [False, True])
def test_identical_zip_new_source_reuses_authorized_snapshot_without_revision_change(tmp_path, monkeypatch, preview_guard):
    monkeypatch.setenv('K05_TEST_DATABASE_URL', os.environ['K07_TEST_DATABASE_URL'])

    async def scenario():
        async with _case(tmp_path) as case:
            await _initial(case)
            if preview_guard:
                async with case.pool.connection() as conn:
                    schema = (await (await conn.execute('SELECT current_schema()')).fetchone())[0]
                    assert await migrate_preview(conn, schema) == 'adopted'
            jobs = await _jobs(case)
            (tmp_path / 'repeat-materialization').mkdir()
            worker = UploadProject(tmp_path / 'repeat-materialization', maximum_upload_bytes=10000,
                maximum_expanded_bytes=20000, maximum_entries=10)
            for index in range(3):
                source = await case.sources.put(credential_digest=case.a, workspace_id='a',
                    request_key=SecretStr(f'repeat-upload-source-{index:02}'), payload=case.payload)
                assert source.source_id != case.receipt.source_id
                raw_preview = worker.preview(case.payload, captured_at='2026-11-14T00:00:00Z')
                mapping = _demo_mapping(raw_preview)
                wire_mapping = dict(mapping)
                if preview_guard:
                    preview = await PreviewLeases(case.sources).create(credential_digest=case.a, workspace_id='a',
                        source_id=source.source_id, preview_payload=raw_preview)
                    wire_mapping['_preview_id'] = preview.preview_id
                receipt = await jobs.enqueue(credential_digest=case.a, workspace_id='a', operation='confirm',
                    request_key=SecretStr(f'repeat-confirm-request-{index:02}'),
                    request={'source_id': source.source_id, 'mapping': wire_mapping})
                lease = SecretStr(f'{index+1:x}' * 64)
                claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=('confirm',))
                assert claim is not None and claim.job_id == receipt.job_id
                snapshot = worker.confirm(case.payload, workspace_id='a', source_id=source.source_id,
                    mapping=mapping, captured_at='2026-11-14T00:00:00Z')
                assert snapshot.snapshot_id != case.snapshot.snapshot_id
                assert await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=snapshot)
                status = await jobs.status(credential_digest=case.a, workspace_id='a', job_id=receipt.job_id)
                output = await jobs.output(credential_digest=case.a, workspace_id='a', job_id=receipt.job_id)
                assert status.status == 'succeeded' and output['result']['snapshot_id'] == case.snapshot.snapshot_id
            async with case.pool.connection() as conn:
                assert await (await conn.execute('SELECT revision FROM k05_projects WHERE project_id=%s',
                    (case.initial.project_id,))).fetchone() == (2,)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_snapshot'")).fetchone() == (1,)
                assert await (await conn.execute('SELECT count(*) FROM j01_upload_snapshots')).fetchone() == (1,)
            assert await case.sources.snapshot(credential_digest=case.a, workspace_id='a',
                snapshot_id=case.snapshot.snapshot_id) == case.snapshot
            mapping['metric_definitions'][0]['regression_threshold'] += 1.0
            changed = worker.confirm(case.payload, workspace_id='a', source_id=source.source_id,
                mapping=mapping, captured_at='2026-11-14T00:00:00Z')
            receipt_changed = await jobs.enqueue(credential_digest=case.a, workspace_id='a', operation='confirm',
                request_key=SecretStr('repeat-with-different-mapping'),
                request={'source_id': source.source_id, 'mapping': mapping})
            changed_lease = SecretStr('f' * 64)
            changed_claim = await jobs.claim(lease_digest=changed_lease, lease_seconds=120, operations=('confirm',))
            assert changed_claim is not None and changed_claim.job_id == receipt_changed.job_id
            with pytest.raises(WorkspaceConflict):
                await jobs.complete_snapshot(claim=changed_claim, lease_digest=changed_lease, snapshot=changed)
            async with case.pool.connection() as conn:
                assert await (await conn.execute('SELECT revision FROM k05_projects WHERE project_id=%s',
                    (case.initial.project_id,))).fetchone() == (2,)
            with pytest.raises(SourceNotFound):
                await jobs.output(credential_digest=case.b, workspace_id='b', job_id=receipt.job_id)
    _run(scenario())


def test_preview_guard_rejects_returning_snapshot_from_different_zip_bytes(tmp_path, monkeypatch):
    import psycopg
    from psycopg.types.json import Jsonb

    monkeypatch.setenv('K05_TEST_DATABASE_URL', os.environ['K07_TEST_DATABASE_URL'])

    async def scenario():
        async with _case(tmp_path) as case:
            await _initial(case)
            async with case.pool.connection() as conn:
                schema = (await (await conn.execute('SELECT current_schema()')).fetchone())[0]
                await migrate_preview(conn, schema)
                await conn.execute('INSERT INTO j01_upload_snapshots(snapshot_id,workspace_id,source_id,payload,canonical_record_id) '
                    'VALUES(%s,%s,%s,%s,%s)', (case.snapshot.snapshot_id, 'a', case.snapshot.source_id,
                        Jsonb(case.snapshot.model_dump(mode='json')), case.snapshot.snapshot_id))
            stream = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(case.payload)) as original, zipfile.ZipFile(stream, 'w') as changed:
                for entry in original.infolist():
                    data = original.read(entry)
                    if entry.filename == 'metrics.csv':
                        data = data.replace(b'candidate,loss,0.6', b'candidate,loss,0.7')
                    changed.writestr(entry, data)
            payload = stream.getvalue()
            source = await case.sources.put(credential_digest=case.a, workspace_id='a',
                request_key=SecretStr('different-zip-bytes-preview'), payload=payload)
            assert source.sha256 != case.receipt.sha256
            raw = UploadProject(tmp_path, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
                maximum_entries=10).preview(payload, captured_at='2026-11-14T00:00:00Z')
            preview = await PreviewLeases(case.sources).create(credential_digest=case.a, workspace_id='a',
                source_id=source.source_id, preview_payload=raw)
            jobs, lease = await _jobs(case), SecretStr('c' * 64)
            receipt = await jobs.enqueue(credential_digest=case.a, workspace_id='a', operation='confirm',
                request_key=SecretStr('different-zip-confirm-guard'), request={'source_id': source.source_id,
                    'mapping': {**_demo_mapping(raw), '_preview_id': preview.preview_id}})
            claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=('confirm',))
            assert claim is not None and claim.job_id == receipt.job_id
            with pytest.raises(psycopg.errors.CheckViolation) as error:
                async with case.pool.connection() as conn:
                    await conn.execute("UPDATE j01_jobs SET state='succeeded',result=%s WHERE job_id=%s",
                        (Jsonb({'snapshot_id': case.snapshot.snapshot_id}), receipt.job_id))
            assert error.value.diag.message_primary == 'The snapshot source is unavailable.'
            assert (await jobs.status(credential_digest=case.a, workspace_id='a', job_id=receipt.job_id)).status == 'running'
            assert await case.catalog.read_record(credential_digest=case.a, workspace_id='a',
                project_id=case.snapshot.mapping.project_id, record_id=case.snapshot.snapshot_id) == case.snapshot
    _run(scenario())
