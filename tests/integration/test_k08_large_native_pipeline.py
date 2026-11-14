"""Actual isolated worker + durable queue/catalog with every L metric row retained."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import sys
import time
import unicodedata
import zipfile
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from pypdf import PdfReader
from test_k05_native_results_postgres import _case, _run
from test_k06_canonical_snapshot_postgres import _setup
from test_k07_metric_rows_worker import _source

from aletheia_lab.hosted.catalog_access import CatalogAccess
from aletheia_lab.hosted.catalog_http import CatalogApplication
from aletheia_lab.hosted.cursors import ScopedCursors
from aletheia_lab.hosted.managed_worker import _supervisor_type
from aletheia_lab.hosted.native_upload_records import UploadResultRecordV2
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.project_analysis_http import ProjectAnalysisApplication
from aletheia_lab.hosted.project_operations import CanonicalProjectOperations
from aletheia_lab.hosted.report_renderer import ReportRenderer
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.review_repository import PostgresReviews
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.upload_analysis import METRIC_OBSERVATION_MODEL
from aletheia_lab.hosted.upload_job_worker import _guard_claim
from aletheia_lab.hosted.upload_snapshot import UploadSnapshotV2
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE
from aletheia_lab.hosted_auth.csrf import CSRF_COOKIE, CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions, credential_digest
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_contract.job_page import JobReceipt
from aletheia_lab.hosted_contract.workspaces import ExportDescriptor
from aletheia_lab.product.reports import render_product_report

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K08_TEST_DATABASE_URL'),
    reason='Explicit private K08 PostgreSQL URL required')]


@pytest.fixture(autouse=True)
def configured_database(monkeypatch):
    monkeypatch.setenv('K05_TEST_DATABASE_URL', os.environ['K08_TEST_DATABASE_URL'])


def _mapping(preview):
    target, metric = (preview.mapping_candidates[key][0] for key in ('targets', 'metric_sources'))
    return {'schema_version': 'project-mapping-request/v2',
        'target': {'mapping_id': target['mapping_id'], 'project_item_id': target['project_item_id'],
                   'target_field': 'target', 'identifier_field': 'id'},
        'metric_sources': [{'mapping_id': metric['mapping_id'], 'project_item_id': metric['project_item_id'],
            'format': 'csv', 'records_path': [], 'metric_name_field': 'name', 'metric_value_field': 'value',
            'run_id_field': 'run', 'step_field': 'step', 'class_field': 'class', 'unit_field': 'unit'}],
        'runs': [{'run_id': f'lần-{i}', 'config_item_ids': []} for i in range(5)], 'baseline_run_id': 'lần-0',
        'metric_definitions': [{'metric_name': 'độ_chính_xác', 'direction': 'higher_is_better',
                                'regression_threshold': .08, 'unit': '%'}]}


def _adverse(data):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as old, zipfile.ZipFile(output, 'w') as new:
        for info in old.infolist():
            body = old.read(info.filename)
            if info.filename == 'metrics.csv':
                text = body.decode()
                first, rest = text.split('\n', 2)[1:]
                fields = first.split(',')
                fields[2] = '-0.08'
                body = ('run,name,value,step,class,unit\n' + ','.join(fields) + '\n' + rest).encode()
            new.writestr(info, body)
    return output.getvalue()


async def _reports(case, sessions, tokens, settings, headers, view, fallback, clock):
    renderer = ReportRenderer()
    access = CatalogAccess(case.catalog, ScopedCursors(SecretStr('synthetic-k08-cursor-key-' + 'x' * 43)), renderer)
    app = CatalogApplication(fallback, access, PostgresProjectAccess(case.pool), PostgresReviews(case.pool, case.catalog),
        sessions, tokens, settings, body_limits=BodyLimits(10000, 1, 3, 10), clock_check=clock)
    receipts = []
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.public_origin) as client:
            for fmt in ('json', 'markdown', 'pdf'):
                response = await client.post(f'/v1/workspaces/a/results/{view.result.id}/exports',
                    headers=dict(headers, **{'idempotency-key': 'k08-large-report-' + fmt}), json={'format': fmt})
                assert response.status_code == 201, response.text
                descriptor = ExportDescriptor.model_validate_json(response.content)
                full = await client.get(descriptor.download_url, headers=headers)
                assert full.status_code == 200
                assert len(full.content) == descriptor.byte_size
                assert hashlib.sha256(full.content).hexdigest() == descriptor.sha256
                assert full.content == render_product_report(view, fmt)
                assert descriptor.result_id == view.result.id and descriptor.project_id == view.project.id
                if fmt == 'pdf':
                    text = '\n'.join(line for page in PdfReader(io.BytesIO(full.content)).pages
                        for line in page.extract_text().splitlines()
                        if line != 'Aletheia Lab Product Report' and not re.fullmatch(r'Page \d+ of \d+', line))
                    normalized = ' '.join(unicodedata.normalize('NFC', text).split())
                    assert view.snapshot.id in normalized and view.result.id in normalized
                    for claim in view.claims:
                        assert claim.id in normalized
                        assert re.sub(r'\s+', ' ', unicodedata.normalize('NFC', claim.text)).strip() in normalized
                receipts.append({'format': fmt, 'byte_size': descriptor.byte_size, 'sha256': descriptor.sha256,
                    'result_id': descriptor.result_id, 'scope': 'Actual immutable L-source result through ASGI/PG export + authenticated whole-byte download.'})
    finally:
        await renderer.aclose()
    return receipts


@pytest.mark.parametrize('rows', [1000, 100000])
def test_actual_confirm_analysis_noop_adverse_refresh_and_pinned_history(tmp_path, rows):
    if rows == 100000 and os.environ.get('K08_TEST_L_ROWS') != '1':
        pytest.skip('Explicit K08_TEST_L_ROWS=1 required for the physical L pipeline')

    async def scenario():
        async with _case(tmp_path) as case:
            await _setup(case)
            raw = SecretStr('synthetic-k08-large-http-session'.ljust(43, 'x'))
            case.a = credential_digest(raw)
            tokens = CsrfTokens(SecretStr('synthetic-k08-csrf-signing-key-' + 'x' * 43))
            proof = tokens.issue('ctx_a')
            async with case.pool.connection() as conn:
                await conn.execute("UPDATE j01_session_credentials SET digest=%s WHERE context_id='ctx_a'",
                    (case.a.get_secret_value(),))
                await conn.execute("UPDATE j01_sessions SET csrf_token=%s WHERE context_id='ctx_a'",
                    (proof.get_secret_value(),))
            case.sources = PostgresSources(case.pool, maximum_upload_bytes=8 * 1024**2,
                maximum_expanded_bytes=16 * 1024**2, maximum_entries=20, catalog=case.catalog)
            jobs = PostgresJobs(case.pool, case.sources, catalog=case.catalog, audit=True, execution=True,
                                analysis_model=METRIC_OBSERVATION_MODEL)
            heartbeats = []
            heartbeat = jobs.heartbeat
            async def measured_heartbeat(**options):
                live = await heartbeat(**options)
                heartbeats.append(live)
                return live
            jobs.heartbeat = measured_heartbeat
            root = tmp_path / 'isolated-large-worker'
            root.mkdir(mode=0o700)
            sizes, timings = [], {}

            class RecordingWorker(_supervisor_type()):
                async def run_upload(self, payload):
                    output = await super().run_upload(payload)
                    sizes.append({'operation': json.loads(payload)['operation'], 'input': len(payload), 'output': len(output)})
                    print('K08_WORKER_COMPLETE ' + json.dumps(sizes[-1]), flush=True)
                    return output

            worker = RecordingWorker(python=Path(sys.executable).absolute(),
                source_root=Path(__file__).resolve().parents[2] / 'src', private_root=root,
                slots=1, timeout_seconds=90)
            operations = CanonicalProjectOperations(case.sources, worker, jobs, private_root=root)
            settings = RuntimeSettings.model_validate({'environment': 'local', 'public_origin': 'https://app.example.invalid',
                'identity': {'issuer': 'https://identity.example.invalid', 'client_id': 'synthetic',
                    'redirect_uri': 'https://app.example.invalid/v1/auth/callback', 'client_kind': 'public'},
                'storage': {'database_url_env': 'ALETHEIA_HOSTED_DATABASE_URL', 'object_root': str(tmp_path)}})
            async def fallback(*args):
                raise AssertionError('Unexpected fallback route.')
            async def clock():
                return True
            sessions = ServerSessions(PostgresSessionStore(case.pool), AuthPolicy(default_return_to='/', return_to_paths=('/',)),
                    timeout_seconds=5, maximum_cookie_bytes=settings.api.maximum_cookie_bytes)
            app = ProjectAnalysisApplication(fallback, jobs, sessions,
                tokens, settings, body_limits=BodyLimits(10000, 1, 3, 10), clock_check=clock)
            data = _source(rows, version2=True)
            source = await case.sources.put(credential_digest=case.a, workspace_id='a',
                request_key=SecretStr('k08-large-source-initial'), payload=data)
            args = dict(credential_digest=case.a, workspace_id='a')

            async def execute(receipt, operation):
                lease = SecretStr(hashlib.sha256(operation.encode()).hexdigest())
                claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=(operation,))
                assert claim and claim.job_id == receipt.job_id
                started = time.monotonic()
                print('K08_JOB_START ' + operation, flush=True)
                assert await _guard_claim(jobs, operations, claim, lease, 120, None)
                timings[operation + '-' + str(len(timings))] = time.monotonic() - started
                status = await jobs.status(**args, job_id=receipt.job_id)
                assert status.status == 'succeeded', status
                print('K08_JOB_SUCCEEDED ' + operation, flush=True)
                return (await jobs.output(**args, job_id=receipt.job_id))['result']

            try:
                preview = await operations.preview(**args, source_id=source.source_id)
                mapping = _mapping(preview)
                receipt = await jobs.enqueue(**args, operation='confirm', request_key=SecretStr('k08-large-confirm'),
                    request={'source_id': source.source_id, 'mapping': mapping})
                confirmed = await execute(receipt, 'confirm')
                snapshot = await case.sources.snapshot(**args, snapshot_id=confirmed['snapshot_id'])
                assert isinstance(snapshot, UploadSnapshotV2)
                assert len(snapshot.mapping_result.metric_observations) == rows
                assert {r.source_record_index for r in snapshot.mapping_result.metric_observations} == set(range(rows))
                project = snapshot.mapping.project_id
                headers = {'origin': settings.public_origin, 'x-csrf-token': proof.get_secret_value(),
                    'idempotency-key': 'k08-large-analysis',
                    'cookie': f'{SESSION_COOKIE}={raw.get_secret_value()}; {CSRF_COOKIE}={proof.get_secret_value()}'}
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.public_origin) as client:
                    started = time.monotonic()
                    async with asyncio.timeout(5):
                        ack = await client.post(f'/v1/workspaces/a/projects/{project}/analyses', headers=headers,
                            json={'schema_version': 'product-api/v1-draft', 'snapshot_id': snapshot.snapshot_id,
                                'question': 'Những số liệu nào được ghi nhận từ dữ liệu này?', 'backend': 'local_evidence'})
                    timings['asgi_analysis_ack'] = time.monotonic() - started
                    assert ack.status_code == 202, ack.text
                    receipt = JobReceipt.model_validate(ack.json())
                analyzed = await execute(receipt, 'analyze')
                record = await case.catalog.read_record(**args, project_id=project, record_id=analyzed['result']['id'])
                assert isinstance(record, UploadResultRecordV2)
                view = record.view
                immutable = record.model_dump_json()
                assert view.runtime.model == METRIC_OBSERVATION_MODEL
                assert len(view.claims) == 10 and f'Observed {rows} validated metric values' in view.result.summary
                if rows == 100000:
                    assert next(s for s in sizes if s['operation'] == 'analyze')['input'] > 16 * 1024**2
                async with case.pool.connection() as conn:
                    revision = (await (await conn.execute('SELECT revision FROM k05_projects WHERE project_id=%s', (project,))).fetchone())[0]
                noop = await jobs.enqueue_refresh(**args, project_id=project, source_id=source.source_id,
                    expected_revision=revision, request_key=SecretStr('k08-large-noop-refresh'))
                unchanged = await execute(noop, 'refresh')
                assert unchanged['status'] == 'unchanged' and unchanged['revision'] == revision
                changed_source = await case.sources.put(**args, request_key=SecretStr('k08-large-source-adverse'), payload=_adverse(data))
                refreshed = await jobs.enqueue_refresh(**args, project_id=project, source_id=changed_source.source_id,
                    expected_revision=revision, request_key=SecretStr('k08-large-adverse-refresh'))
                changed = await execute(refreshed, 'refresh')
                assert changed['status'] == 'new_snapshot' and changed['revision'] == revision + 1
                next_snapshot = await case.sources.snapshot(**args, snapshot_id=changed['snapshot_id'])
                assert len(next_snapshot.mapping_result.metric_observations) == rows
                assert (await case.catalog.read_record(**args, project_id=project, record_id=view.result.id)).model_dump_json() == immutable
                comparisons = await case.catalog.list_records(**args, project_id=project, record_type='snapshot_comparison')
                assert len(comparisons.records) == 1
                comparison = await case.catalog.read_record(**args, project_id=project, record_id=comparisons.records[0].record_id)
                assert sum(s.adverse for s in comparison.series) == 1
                assert comparison.before_rows == comparison.after_rows == rows
                reports = await _reports(case, sessions, tokens, settings, headers, view, fallback, clock)
                evidence = os.environ.get('K08_EVIDENCE_DIR')
                if evidence:
                    target = Path(evidence)
                    assert target.is_absolute() and target.is_dir()
                    (target / f'large-{rows}-view.json').write_text(view.model_dump_json(), encoding='utf-8')
                    (target / f'large-{rows}-comparison.json').write_text(comparison.model_dump_json(), encoding='utf-8')
                    receipt_data = {'rows': rows, 'source_bytes': len(data), 'sizes': sizes, 'timings': timings,
                        'view_bytes': len(view.model_dump_json().encode()), 'comparison_bytes': len(comparison.model_dump_json().encode()),
                        'snapshot_id': snapshot.snapshot_id, 'result_id': view.result.id, 'analysis_model': view.runtime.model,
                        'successful_heartbeats': heartbeats.count(True), 'lost_heartbeats': heartbeats.count(False),
                        'reports': reports,
                        'scope': 'Physical metric rows, actual worker/queue/PG/catalog; grouped report is bounded. No real IdP or Docker.'}
                    (target / f'large-{rows}-receipt.json').write_text(json.dumps(receipt_data, indent=2) + '\n')
                print('K08_L_PIPELINE ' + json.dumps({'rows': rows, 'sizes': sizes, 'timings': timings}))
            finally:
                await worker.aclose()
            for folder in ('inputs', 'materializations', 'payload-exchanges'):
                assert not list((root / folder).glob('*'))
    _run(scenario())
